import os
import time
import base64
import logging
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY")  # Saved as RSA Private Key PEM string

KALSHI_BASE_URL = "https://external-api.kalshi.com"

# Coin Symbol -> CF Benchmarks Index Mapping
INDEX_MAPPING = {
    "BTC": "BRTI",
    "ETH": "ETHUSD_RTI",
    "SOL": "SOLUSD_RTI",
    "XRP": "XRPUSD_RTI",
    "DOGE": "DOGEUSD_RTI",
    "SUI": "SUIUSD_RTI"
}

def sign_kalshi_request(method: str, path: str) -> dict:
    """Generates RSA-PSS authentication headers required for Kalshi API calls"""
    if not KALSHI_KEY_ID or not KALSHI_PRIVATE_KEY_PEM:
        return {}
        
    try:
        timestamp_ms = str(int(time.time() * 1000))
        msg = f"{timestamp_ms}{method}{path}".encode('utf-8')
        
        private_key = serialization.load_pem_private_key(
            KALSHI_PRIVATE_KEY_PEM.encode('utf-8'),
            password=None
        )
        
        signature = private_key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        sig_b64 = base64.b64encode(signature).decode('utf-8')
        
        return {
            "KALSHI-ACCESS-KEY": KALSHI_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type": "application/json"
        }
    except Exception as e:
        logger.error(f"Error signing Kalshi request: {e}")
        return {}

def fetch_cf_benchmark_index(index_id: str) -> dict:
    """Fetches official live index value and 60s trailing average from Kalshi's CF Benchmarks API"""
    path = f"/trade-api/v2/cfbenchmarks/values?id={index_id}"
    url = f"{KALSHI_BASE_URL}{path}"
    
    headers = sign_kalshi_request("GET", "/trade-api/v2/cfbenchmarks/values")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            payload = response.json().get("data", {}).get("payload", {})
            current_val = payload.get("val")
            avg_60s = payload.get("avg_60s_data", {}).get("avg") or current_val
            return {"current": current_val, "avg_60s": avg_60s}
    except Exception as e:
        logger.error(f"Error fetching CF Benchmark for {index_id}: {e}")
        
    return {"current": None, "avg_60s": None}

def fetch_all_15m_crypto_markets() -> list:
    """Queries all active 15-minute crypto markets across all supported coins"""
    path = "/trade-api/v2/markets"
    url = f"{KALSHI_BASE_URL}{path}"
    
    try:
        # Fetch open markets
        response = requests.get(url, params={"status": "open", "limit": 100}, timeout=10)
        data = response.json()
        markets = data.get("markets", [])
        
        crypto_15m_markets = []
        for m in markets:
            ticker = str(m.get("ticker", "")).upper()
            title = str(m.get("title", ""))
            
            # Identify 15m crypto contracts by ticker naming conventions (e.g. KXETH15M, KXBTC15M, etc.)
            if "15M" in ticker or "15 MIN" in title.upper() or "15MIN" in ticker:
                # Extract asset symbol
                coin = "ETH"
                for c in INDEX_MAPPING.keys():
                    if c in ticker:
                        coin = c
                        break

                strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
                
                if isinstance(yes_bid, (int, float)) and yes_bid > 1.0:
                    yes_bid = yes_bid / 100.0

                crypto_15m_markets.append({
                    "coin": coin,
                    "ticker": ticker,
                    "title": title,
                    "strike": strike,
                    "yes_price": yes_bid
                })
        return crypto_15m_markets
    except Exception as e:
        logger.error(f"Error pulling 15m markets: {e}")
        return []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Multi-Crypto Kalshi 15-Min Index Engine Ready**\n\n"
        "Commands:\n"
        "• `/fetchstrikes` - List active 15m crypto target contracts\n"
        "• `/analyze` - Run CF Benchmark settlement index gap analysis"
    )

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Pulling active 15-minute crypto markets...")
    markets = fetch_all_15m_crypto_markets()
    
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute crypto contracts found right now.")
        return
        
    msg = "⏱ **Active 15-Min Kalshi Crypto Markets**\n\n"
    for m in markets[:8]:
        strike_str = f"${m['strike']:,.2f}" if isinstance(m['strike'], (int, float)) else "Target Price"
        yes_str = f"${m['yes_price']:.2f}" if isinstance(m['yes_price'], (int, float)) else "N/A"
        
        msg += f"• **[{m['coin']}] {m['title']}**\n"
        msg += f"  Ticker: `{m['ticker']}`\n"
        msg += f"  Strike Target: **{strike_str}** | Yes Bid: **{yes_str}**\n\n"
        
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Running CF Benchmark Settlement Index calculations...")
    markets = fetch_all_15m_crypto_markets()
    
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found for analysis.")
        return
        
    msg = "🧮 **CF Benchmark 15-Min Market Predictions & Discrepancies**\n\n"
    
    # Cache index feeds to avoid redundant network calls
    index_cache = {}
    
    for m in markets[:6]:
        coin = m['coin']
        index_id = INDEX_MAPPING.get(coin, "ETHUSD_RTI")
        
        if index_id not in index_cache:
            index_cache[index_id] = fetch_cf_benchmark_index(index_id)
            
        idx_data = index_cache[index_id]
        settlement_ref = idx_data['avg_60s'] or idx_data['current']
        
        strike = m['strike']
        yes_price = m['yes_price']
        
        insight = "⚖️ Market balanced"
        if settlement_ref and isinstance(strike, (int, float)) and isinstance(yes_price, (int, float)):
            diff = settlement_ref - strike
            prob = int(yes_price * 100)
            
            if settlement_ref > strike:
                if prob < 50:
                    insight = f"🔥 **UNDERPRICED YES:** Index is +${diff:.2f} above strike, but Yes is only {prob}%!"
                else:
                    insight = f"✅ **IN-THE-MONEY:** Index +${diff:.2f} above strike ({prob}% implied)."
            else:
                if prob > 50:
                    insight = f"⚠️ **OVERPRICED YES:** Index is -${abs(diff):.2f} below strike, but Yes is {prob}%!"
                else:
                    insight = f"❌ **OUT-OF-THE-MONEY:** Index -${abs(diff):.2f} below strike ({prob}% implied)."
                    
        ref_str = f"${settlement_ref:,.2f}" if settlement_ref else "N/A"
        strike_str = f"${strike:,.2f}" if isinstance(strike, (int, float)) else "N/A"

        msg += f"• **[{coin}] {m['title']}**\n"
        msg += f"  Target Strike: **{strike_str}**\n"
        msg += f"  Live Index (60s Avg): **{ref_str}**\n"
        msg += f"  Insight: {insight}\n\n"

    await update.message.reply_text(msg)

def main():
    if not BOT_TOKEN:
        raise ValueError("No BOT_TOKEN found!")

    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("fetchstrikes", fetch_strikes_command))
    application.add_handler(CommandHandler("analyze", analyze_command))

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
