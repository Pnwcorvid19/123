import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from kalshi_python_sync import Configuration, KalshiClient

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY")

# Initialize Official Kalshi Client using Configuration object
kalshi_client = None
try:
    if KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PEM:
        formatted_key = KALSHI_PRIVATE_KEY_PEM.replace("\\n", "\n")
        
        # Configure client connection
        config = Configuration(host="https://external-api.kalshi.com/trade-api/v2")
        config.api_key_id = KALSHI_KEY_ID
        config.private_key_pem = formatted_key
        
        kalshi_client = KalshiClient(config)
        logger.info("Kalshi client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Kalshi client: {e}")

INDEX_MAPPING = {
    "BTC": "BRTI",
    "ETH": "ETHUSD_RTI",
    "SOL": "SOLUSD_RTI",
    "XRP": "XRPUSD_RTI",
    "DOGE": "DOGEUSD_RTI",
    "SUI": "SUIUSD_RTI"
}

def fetch_15m_crypto_markets():
    """Fetches active 15-minute crypto markets from Kalshi public API"""
    try:
        resp = requests.get(
            "https://external-api.kalshi.com/trade-api/v2/markets",
            params={"status": "open", "limit": 100},
            timeout=10
        )
        data = resp.json()
        markets = data.get("markets", [])
        
        results = []
        for m in markets:
            ticker = str(m.get("ticker", "")).upper()
            title = str(m.get("title", ""))
            
            if "15M" in ticker or "15 MIN" in title.upper():
                coin = "ETH"
                for c in INDEX_MAPPING.keys():
                    if c in ticker:
                        coin = c
                        break
                
                strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
                if isinstance(yes_bid, (int, float)) and yes_bid > 1.0:
                    yes_bid = yes_bid / 100.0

                results.append({
                    "coin": coin,
                    "ticker": ticker,
                    "title": title,
                    "strike": strike,
                    "yes_price": yes_bid
                })
        return results
    except Exception as e:
        logger.error(f"Error fetching markets: {e}")
        return []

def get_cf_index(index_id: str):
    """Fetches official CF Benchmarks value using the authenticated SDK client API call"""
    if not kalshi_client:
        return None
    try:
        # Use underlying api_client to call the CF Benchmarks endpoint with auto-signed auth headers
        response_data = kalshi_client.api_client.call_api(
            "/trade-api/v2/cfbenchmarks/values",
            "GET",
            query_params=[("id", index_id)],
            response_type=object
        )
        
        # response_data[0] contains the deserialized JSON response dictionary
        payload = response_data[0].get("data", {}).get("payload", {})
        val = payload.get("val")
        avg_60s = payload.get("avg_60s_data", {}).get("avg") or val
        return {"current": val, "avg_60s": avg_60s}
    except Exception as e:
        logger.error(f"Error pulling CF benchmark for {index_id}: {e}")
        return None

# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Kalshi 15-Min Multi-Crypto Bot Active**\n\n"
        "Commands:\n"
        "• `/fetchstrikes` - View active 15m crypto target prices\n"
        "• `/analyze` - Run official settlement index analysis & predictions\n"
        "• `/startalerts` - Turn on 20s background push notifications\n"
        "• `/stopalerts` - Turn off background alerts"
    )

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute crypto markets found right now.")
        return
        
    msg = "⏱ **Active 15-Min Crypto Contracts**\n\n"
    for m in markets[:8]:
        strike_str = f"${m['strike']:,.2f}" if isinstance(m['strike'], (int, float)) else "N/A"
        yes_str = f"${m['yes_price']:.2f}" if isinstance(m['yes_price'], (int, float)) else "N/A"
        msg += f"• **[{m['coin']}] {m['title']}**\n  Ticker: `{m['ticker']}`\n  Strike: **{strike_str}** | Yes: **{yes_str}**\n\n"
        
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching official CF Benchmarks & analyzing markets...")
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found.")
        return
        
    msg = "🧮 **15-Min Settlement Index & Prediction Analysis**\n\n"
    cache = {}
    
    for m in markets[:6]:
        coin = m['coin']
        index_id = INDEX_MAPPING.get(coin, "ETHUSD_RTI")
        
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
                insight = f"✅ **ITM:** Index is +${diff:.2f} above strike ({prob}% Yes odds)" if prob >= 50 else f"🔥 **UNDERPRICED YES:** Index is +${diff:.2f} above strike, but Yes is only {prob}%!"
            else:
                insight = f"❌ **OTM:** Index is -${abs(diff):.2f} below strike ({prob}% Yes odds)" if prob < 50 else f"⚠️ **OVERPRICED YES:** Index is -${abs(diff):.2f} below strike, but Yes is {prob}%!"

        ref_str = f"${settlement_ref:,.2f}" if settlement_ref else "Unavailable (Check API Keys)"
        strike_str = f"${strike:,.2f}" if isinstance(strike, (int, float)) else "N/A"

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

        index_id = INDEX_MAPPING.get(coin, "ETHUSD_RTI")
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
                    text=f"🔥 **MISPRICING ALERT [{coin}]** 🔥\n`{ticker}`\nStrike: ${strike:,.2f}\nIndex: ${settlement_ref:,.2f} (+${diff:.2f})\nYes Odds: {prob}%"
                )

async def start_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    context.job_queue.run_repeating(monitor_job, interval=20, first=1, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text("🚨 **Live Background Alerts Enabled!**")

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
