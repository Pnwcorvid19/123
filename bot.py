import os
import time
import base64
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY")

KALSHI_BASE_URL = "https://external-api.kalshi.com"

# Crypto Symbol -> CF Benchmarks Index Mapping
INDEX_MAPPING = {
    "BTC": "BRTI",
    "ETH": "ETHUSD_RTI",
    "SOL": "SOLUSD_RTI",
    "XRP": "XRPUSD_RTI",
    "DOGE": "DOGEUSD_RTI",
    "SUI": "SUIUSD_RTI"
}

# --- Kalshi RSA Authentication & Data Fetching ---

def sign_kalshi_request(method: str, path: str) -> dict:
    if not KALSHI_KEY_ID or not KALSHI_PRIVATE_KEY_PEM:
        return {}
        
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        
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
    path = "/trade-api/v2/markets"
    url = f"{KALSHI_BASE_URL}{path}"
    
    try:
        response = requests.get(url, params={"status": "open", "limit": 100}, timeout=10)
        data = response.json()
        markets = data.get("markets", [])
        
        crypto_15m_markets = []
        for m in markets:
            ticker = str(m.get("ticker", "")).upper()
            title = str(m.get("title", ""))
            
            if "15M" in ticker or "15 MIN" in title.upper() or "15MIN" in ticker:
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

# --- Background Worker & Push Notification Handler ---

async def monitor_mispricings_job(context: ContextTypes.DEFAULT_TYPE):
    """Background polling loop executed by JobQueue every 20 seconds"""
    chat_id = context.job.chat_id
    markets = fetch_all_15m_crypto_markets()
    
    if not markets:
        return

    # Initialize memory set to prevent repeating duplicate notifications
    if "alerted_tickers" not in context.bot_data:
        context.bot_data["alerted_tickers"] = set()

    index_cache = {}
    
    for m in markets:
        coin = m['coin']
        ticker = m['ticker']
        strike = m['strike']
        yes_price = m['yes_price']
        
        if not isinstance(strike, (int, float)) or not isinstance(yes_price, (int, float)):
            continue

        index_id = INDEX_MAPPING.get(coin, "ETHUSD_RTI")
        if index_id not in index_cache:
            index_cache[index_id] = fetch_cf_benchmark_index(index_id)
            
        idx_data = index_cache[index_id]
        settlement_ref = idx_data['avg_60s'] or idx_data['current']
        
        if not settlement_ref:
            continue

        diff = settlement_ref - strike
        prob = int(yes_price * 100)

        # Threshold Rule 1: Index is IN-THE-MONEY (Spot > Strike), but Yes Odds < 45% (Underpriced)
        if settlement_ref > strike and prob <= 45:
            alert_key = f"{ticker}_UNDERPRICED_{prob}"
            if alert_key not in context.bot_data["alerted_tickers"]:
                context.bot_data["alerted_tickers"].add(alert_key)
                
                msg = f"🔥 **KALSHI MISPRICING ALERT [{coin}]** 🔥\n\n" \
                      f"• **Market:** `{ticker}`\n" \
                      f"• **Settlement Strike:** ${strike:,.2f}\n" \
                      f"• **CF Benchmark Index:** ${settlement_ref:,.2f} (+${diff:.2f} ITM)\n" \
                      f"• **Current 'Yes' Odds:** **{prob}%** (${yes_price:.2f})\n\n" \
                      f"⚡ *Spot index is already above strike, but market probability is under 45%!*"
                
                await context.bot.send_message(chat_id=chat_id, text=msg)

        # Threshold Rule 2: Index is OUT-OF-THE-MONEY (Spot < Strike), but Yes Odds > 65% (Overpriced)
        elif settlement_ref < strike and prob >= 65:
            alert_key = f"{ticker}_OVERPRICED_{prob}"
            if alert_key not in context.bot_data["alerted_tickers"]:
                context.bot_data["alerted_tickers"].add(alert_key)
                
                msg = f"⚠️ **HIGH RISK OVERPRICING ALERT [{coin}]** ⚠️\n\n" \
                      f"• **Market:** `{ticker}`\n" \
                      f"• **Settlement Strike:** ${strike:,.2f}\n" \
                      f"• **CF Benchmark Index:** ${settlement_ref:,.2f} (-${abs(diff):.2f} OTM)\n" \
                      f"• **Current 'Yes' Odds:** **{prob}%** (${yes_price:.2f})\n\n" \
                      f"⚡ *Spot index is below target, but market is pricing 'Yes' above 65%!*"
                
                await context.bot.send_message(chat_id=chat_id, text=msg)

# --- Bot Commands ---

async def start_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # Remove existing jobs for this chat if present
    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in current_jobs:
        job.schedule_removal()

    # Add recurring background job (polls every 20 seconds)
    context.job_queue.run_repeating(
        monitor_mispricings_job,
        interval=20,
        first=1,
        chat_id=chat_id,
        name=str(chat_id)
    )
    
    await update.message.reply_text(
        "🚨 **Live Mispricing Push Notifications Enabled!**\n\n"
        "The bot will now scan all 15-minute crypto markets every 20 seconds and alert you instantly "
        "when CF Benchmark index settlement odds diverge from market contract pricing."
    )

async def stop_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    
    if not current_jobs:
        await update.message.reply_text("ℹ️ No active polling alert jobs are running.")
        return
        
    for job in current_jobs:
        job.schedule_removal()
        
    await update.message.reply_text("🛑 **Live alerts disabled.**")

def main():
    if not BOT_TOKEN:
        raise ValueError("No BOT_TOKEN found!")

    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("startalerts", start_alerts_command))
    application.add_handler(CommandHandler("stopalerts", stop_alerts_command))

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
