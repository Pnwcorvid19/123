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

# Restricted strictly to Ethereum, XRP, and NEAR 15-minute markets
SERIES_MAPPING = {
    "ETH": {"series": "KXETH15M", "index": "ETHUSD_RTI"},
    "XRP": {"series": "KXXRP15M", "index": "XRPUSD_RTI"},
    "NEAR": {"series": "KXNEAR15M", "index": "NEARUSD_RTI"}
}

def sign_kalshi_request(method: str, path: str) -> dict:
    """Generates native RSA-PSS authentication headers for Kalshi API calls"""
    if not KALSHI_KEY_ID or not KALSHI_PRIVATE_KEY_PEM:
        logger.warning("Missing Kalshi API Key ID or Private Key environment variables.")
        return {}
        
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import serialization
        
        timestamp_ms = str(int(time.time() * 1000))
        msg = f"{timestamp_ms}{method}{path}".encode('utf-8')
        
        formatted_key = KALSHI_PRIVATE_KEY_PEM.replace("\\n", "\n")
        private_key = serialization.load_pem_private_key(
            formatted_key.encode('utf-8'),
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

def fetch_15m_crypto_markets():
    """Fetches active 15-minute crypto markets for ETH, XRP, and NEAR"""
    results = []
    for coin, info in SERIES_MAPPING.items():
        series_ticker = info["series"]
        try:
            resp = requests.get(
                f"{KALSHI_BASE_URL}/trade-api/v2/markets",
                params={"series_ticker": series_ticker, "status": "open"},
                timeout=5
            )
            if resp.status_code == 200:
                markets = resp.json().get("markets", [])
                for m in markets:
                    ticker = str(m.get("ticker", "")).upper()
                    title = str(m.get("title", ""))
                    strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                    
                    yes_price = (
                        m.get("yes_bid_dollars") or 
                        m.get("yes_ask_dollars") or 
                        m.get("last_price_dollars") or 
                        m.get("yes_bid") or 
                        m.get("yes_price")
                    )
                    
                    if isinstance(yes_price, str):
                        try:
                            yes_price = float(yes_price)
                        except ValueError:
                            yes_price = None

                    if isinstance(yes_price, (int, float)) and yes_price > 1.0:
                        yes_price = yes_price / 100.0

                    results.append({
                        "coin": coin,
                        "ticker": ticker,
                        "title": title,
                        "strike": strike,
                        "yes_price": yes_price
                    })
        except Exception as e:
            logger.error(f"Error fetching markets for {series_ticker}: {e}")
            
    return results

def get_cf_index(index_id: str):
    """Fetches official CF Benchmarks index value using native signed headers"""
    path = f"/trade-api/v2/cfbenchmarks/values"
    url = f"{KALSHI_BASE_URL}{path}?id={index_id}"
    headers = sign_kalshi_request("GET", path)
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            payload = resp.json().get("data", {}).get("payload", {})
            val = payload.get("val")
            avg_60s = payload.get("avg_60s_data", {}).get("avg") or val
            return {"current": val, "avg_60s": avg_60s}
        else:
            logger.error(f"CF Index API Error [{resp.status_code}] for {index_id}: {resp.text}")
    except Exception as e:
        logger.error(f"Error fetching CF benchmark {index_id}: {e}")
        
    return {"current": None, "avg_60s": None}

# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Kalshi ETH, XRP, NEAR 15-Min Edge Bot Active**\n\n"
        "Commands:\n"
        "• `/fetchstrikes` - View active 15m ETH, XRP, NEAR targets\n"
        "• `/analyze` - Run official settlement index analysis & edge check\n"
        "• `/startalerts` - Turn on 20s background push notifications\n"
        "• `/stopalerts` - Turn off background alerts"
    )

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute ETH, XRP, or NEAR markets found right now.")
        return
        
    msg = "⏱ **Active 15-Min ETH, XRP & NEAR Contracts**\n\n"
    for m in markets[:8]:
        strike_str = f"${m['strike']:,.4f}" if isinstance(m['strike'], (int, float)) else "N/A"
        yes_str = f"${m['yes_price']:.2f}" if isinstance(m['yes_price'], (int, float)) else "N/A"
        msg += f"• **[{m['coin']}] {m['title']}**\n  Ticker: `{m['ticker']}`\n  Strike: **{strike_str}** | Yes: **{yes_str}**\n\n"
        
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching official CF Benchmarks for ETH, XRP, NEAR...")
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found.")
        return
        
    msg = "🧮 **ETH, XRP, NEAR Settlement Index & Edge Analysis**\n\n"
    cache = {}
    
    for m in markets[:6]:
        coin = m['coin']
        index_id = SERIES_MAPPING.get(coin, {}).get("index")
        
        if index_id not in cache:
            cache[index_id] = get_cf_index(index_id)
            
        idx = cache[index_id] or {}
        settlement_ref = idx.get('avg_60s') or idx.get('current')
        
        strike = m['strike']
        yes_price = m['yes_price']
        
        insight = "⚖️ Market balanced"
        if settlement_ref and isinstance(strike, (int, float)) and isinstance(yes_price, (int, float)):
            diff = settlement_ref - strike
            prob = int(yes_price * 100)
            if settlement_ref > strike:
                insight = f"✅ **ITM:** Index +${diff:.4f} above strike ({prob}% Yes odds)" if prob >= 50 else f"🔥 **UNDERPRICED YES:** Index +${diff:.4f} above strike, but Yes is only {prob}%!"
            else:
                insight = f"❌ **OTM:** Index -${abs(diff):.4f} below strike ({prob}% Yes odds)" if prob < 50 else f"⚠️ **OVERPRICED YES:** Index -${abs(diff):.4f} below strike, but Yes is {prob}%!"

        ref_str = f"${settlement_ref:,.4f}" if settlement_ref else "Unavailable (Check Render Env Keys)"
        strike_str = f"${strike:,.4f}" if isinstance(strike, (int, float)) else "N/A"

        msg += f"• **[{coin}] {m['title']}**\n  Target Strike: **{strike_str}**\n  CF Index (60s Avg): **{ref_str}**\n  Status: {insight}\n\n"

    await update.message.reply_text(msg)

# --- Background Worker ---

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    markets = fetch_15m_crypto_markets()
    if not markets:
        return

    if "alerted" not in context.bot_data:
        context.bot_data["alerted"] = set()

    cache = {}
    for m in markets:
        coin = m['coin']
        ticker = m['ticker']
        strike = m['strike']
        yes_price = m['yes_price']
        
        if not isinstance(strike, (int, float)) or not isinstance(yes_price, (int, float)):
            continue

        index_id = SERIES_MAPPING.get(coin, {}).get("index")
        if index_id not in cache:
            cache[index_id] = get_cf_index(index_id)
            
        idx = cache[index_id] or {}
        settlement_ref = idx.get('avg_60s') or idx.get('current')
        if not settlement_ref:
            continue

        diff = settlement_ref - strike
        prob = int(yes_price * 100)

        if settlement_ref > strike and prob <= 40:
            key = f"{ticker}_UNDER"
            if key not in context.bot_data["alerted"]:
                context.bot_data["alerted"].add(key)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔥 **MISPRICING ALERT [{coin}]** 🔥\n`{ticker}`\nStrike: ${strike:,.4f}\nIndex: ${settlement_ref:,.4f} (+${diff:.4f})\nYes Odds: {prob}%"
                )

async def start_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    context.job_queue.run_repeating(monitor_job, interval=20, first=1, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text("🚨 **ETH, XRP, NEAR Live Alerts Enabled!**")

async def stop_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if not jobs:
        await update.message.reply_text("ℹ️ No alerts running.")
        return
    for j in jobs:
        j.schedule_removal()
    await update.message.reply_text("🛑 **Alerts stopped.**")

def main():
    if not BOT_TOKEN:
        raise ValueError("Missing BOT_TOKEN environment variable!")

    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("fetchstrikes", fetch_strikes_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("startalerts", start_alerts))
    app.add_handler(CommandHandler("stopalerts", stop_alerts))

    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
