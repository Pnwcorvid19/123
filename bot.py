import os
import logging
import datetime
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
KALSHI_BASE_URL = "https://external-api.kalshi.com"

# Restricted strictly to ETH, XRP, and NEAR 15-minute series
SERIES_MAPPING = {
    "ETH": "KXETH15M",
    "XRP": "KXXRP15M",
    "NEAR": "KXNEAR15M"
}

def fetch_15m_crypto_markets():
    """Fetches active 15-minute crypto markets for ETH, XRP, and NEAR from Kalshi"""
    results = []
    for coin, series_ticker in SERIES_MAPPING.items():
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
                    close_time_str = m.get("close_time")
                    
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
                        "close_time": close_time_str,
                        "yes_price": yes_price
                    })
        except Exception as e:
            logger.error(f"Error fetching markets for {series_ticker}: {e}")
            
    return results

def get_live_spot_price(coin: str):
    """Multi-source fallback spot price fetcher to bypass cloud IP blocks"""
    try:
        cg_ids = {"ETH": "ethereum", "XRP": "ripple", "NEAR": "near"}
        cg_id = cg_ids.get(coin)
        if cg_id:
            resp = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd", timeout=3)
            if resp.status_code == 200:
                val = resp.json().get(cg_id, {}).get("usd")
                if val:
                    return float(val)
    except Exception:
        pass

    try:
        cb_symbols = {"ETH": "ETH-USD", "XRP": "XRP-USD", "NEAR": "NEAR-USD"}
        symbol = cb_symbols.get(coin)
        if symbol:
            resp = requests.get(f"https://api.coinbase.com/v2/prices/{symbol}/spot", timeout=3)
            if resp.status_code == 200:
                val = resp.json().get("data", {}).get("amount")
                if val:
                    return float(val)
    except Exception:
        pass

    try:
        cc_ids = {"ETH": "ethereum", "XRP": "xrp", "NEAR": "near"}
        cc_id = cc_ids.get(coin)
        if cc_id:
            resp = requests.get(f"https://api.coincap.io/v2/assets/{cc_id}", timeout=3)
            if resp.status_code == 200:
                val = resp.json().get("data", {}).get("priceUsd")
                if val:
                    return float(val)
    except Exception:
        pass

    return None

def get_seconds_left(close_time_str: str) -> int:
    """Calculates remaining seconds until contract expiration"""
    if not close_time_str:
        return 9999
    try:
        if close_time_str.endswith("Z"):
            close_time_str = close_time_str[:-1] + "+00:00"
        close_dt = datetime.datetime.fromisoformat(close_time_str)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        return int((close_dt - now_dt).total_seconds())
    except Exception:
        return 9999

# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **ETH, XRP, NEAR Late-Clock Momentum Bot Active**\n\n"
        "Commands:\n"
        "• `/fetchstrikes` - View active 15m contracts & strikes\n"
        "• `/analyze` - Scan spot vs. strike thresholds & final-minute edges\n"
        "• `/startalerts` - Turn on 20s background late-clock alerts\n"
        "• `/stopalerts` - Turn off alerts"
    )

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found right now.")
        return
        
    msg = "⏱ **Active 15-Min ETH, XRP & NEAR Contracts**\n\n"
    for m in markets[:8]:
        strike_str = f"${m['strike']:,.4f}" if isinstance(m['strike'], (int, float)) else "N/A"
        yes_str = f"${m['yes_price']:.2f}" if isinstance(m['yes_price'], (int, float)) else "N/A"
        secs = get_seconds_left(m['close_time'])
        mins = secs // 60
        msg += f"• **[{m['coin niest'] or m['coin']}] {m['title']}**\n  Ticker: `{m['ticker']}`\n  Strike: **{strike_str}** | Yes: **{yes_str}**\n  ⏳ Time Left: **{mins}m ({secs}s)**\n\n"
        
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning live spot prices and late-clock thresholds...")
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found.")
        return
        
    msg = "📊 **Late-Clock Threshold & Momentum Analysis**\n\n"
    cache_spot = {}
    
    for m in markets[:6]:
        coin = m['coin']
        if coin not in cache_spot:
            cache_spot[coin] = get_live_spot_price(coin)
            
        spot = cache_spot[coin]
        strike = m['strike']
        yes_price = m['yes_price']
        secs_left = get_seconds_left(m['close_time'])
        mins_left = secs_left // 60
        
        status_text = "Waiting for final 5 minutes..."
        if spot and isinstance(strike, (int, float)):
            diff = spot - strike
            yes_cents = int(yes_price * 100) if isinstance(yes_price, (int, float)) else 50
            
            if secs_left <= 300: # Final 5 minutes window
                if spot > strike:
                    if yes_cents <= 70:
                        status_text = f"🔥 **LATE MISPRICING (BUY YES):** Spot is +${diff:.4f} above strike with only {mins_left}m left, but Yes is only {yes_cents}¢!"
                    else:
                        status_text = f"✅ **CONVERGED ITM:** Spot is +${diff:.4f} above strike ({yes_cents}¢ Yes)"
                else:
                    if yes_cents >= 30:
                        status_text = f"⚠️ **LATE MISPRICING (BUY NO):** Spot is -${abs(diff):.4f} below strike with {mins_left}m left, but Yes is still {yes_cents}¢!"
                    else:
                        status_text = f"❌ **CONVERGED OTM:** Spot is -${abs(diff):.4f} below strike ({yes_cents}¢ Yes)"
            else:
                direction = "ABOVE" if spot > strike else "BELOW"
                status_text = f"⏳ **Early Window ({mins_left}m left):** Spot is ${abs(diff):.4f} {direction} strike. (Monitoring for final 5m window)"

        spot_str = f"${spot:,.4f}" if spot else "Unavailable"
        strike_str = f"${strike:,.4f}" if isinstance(strike, (int, float)) else "N/A"

        msg += f"• **[{coin}] {m['title']}**\n  Strike: **{strike_str}** | Spot: **{spot_str}**\n  Status: {status_text}\n\n"

    await update.message.reply_text(msg)

# --- Background Worker ---

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    markets = fetch_15m_crypto_markets()
    if not markets:
        return

    if "alerted" not in context.bot_data:
        context.bot_data["alerted"] = set()

    cache_spot = {}
    for m in markets:
        coin = m['coin']
        ticker = m['ticker']
        strike = m['strike']
        yes_price = m['yes_price']
        secs_left = get_seconds_left(m['close_time'])
        
        if not isinstance(strike, (int, float)) or not isinstance(yes_price, (int, float)):
            continue

        if coin not in cache_spot:
            cache_spot[coin] = get_live_spot_price(coin)
            
        spot = cache_spot[coin]
        if not spot:
            continue

        diff = spot - strike
        yes_cents = int(yes_price * 100)

        # Trigger push alert strictly when entering the final 5 minutes with clear mispricing
        if secs_left <= 300 and secs_left > 0:
            if spot > strike and yes_cents <= 65:
                key = f"{ticker}_LATE_YES"
                if key not in context.bot_data["alerted"]:
                    context.bot_data["alerted"].add(key)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🚨 **LATE-CLOCK BUY YES [{coin}]** 🚨\n`{ticker}`\n⏳ Time Left: {secs_left}s\nStrike: ${strike:,.4f}\nSpot: ${spot:,.4f} (+${diff:.4f})\nYes Price: **{yes_cents}¢** (Lagging!)"
                    )
            elif spot < strike and yes_cents >= 35:
                key = f"{ticker}_LATE_NO"
                if key not in context.bot_data["alerted"]:
                    context.bot_data["alerted"].add(key)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🚨 **LATE-CLOCK BUY NO [{coin}]** 🚨\n`{ticker}`\n⏳ Time Left: {secs_left}s\nStrike: ${strike:,.4f}\nSpot: ${spot:,.4f} (-${abs(diff):.4f})\nYes Price is ⏳ **{yes_cents}¢** (Overpriced!)"
                    )

async def start_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    context.job_queue.run_repeating(monitor_job, interval=20, first=1, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text("🚨 **Late-Clock Threshold Alerts Enabled!**")

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
