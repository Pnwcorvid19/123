import os
import math
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

# Restricted strictly to ETH, XRP, and NEAR 15-minute series with volatility parameters
SERIES_MAPPING = {
    "ETH": {"series": "KXETH15M", "symbol": "ETHUSDT", "vol": 0.75},
    "XRP": {"series": "KXXRP15M", "symbol": "XRPUSDT", "vol": 0.95},
    "NEAR": {"series": "KXNEAR15M", "symbol": "NEARUSDT", "vol": 1.10}
}

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

def get_live_spot_price(symbol: str):
    """Fetches real-time spot price from Binance"""
    try:
        resp = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=3)
        if resp.status_code == 200:
            return float(resp.json().get("price"))
    except Exception as e:
        logger.error(f"Error fetching spot for {symbol}: {e}")
    return None

def calculate_win_probability(spot: float, strike: float, close_time_str: str, volatility: float) -> float:
    """Quantitative Black-Scholes binary option probability model"""
    if not spot or not strike or not close_time_str:
        return 0.5
    try:
        if close_time_str.endswith("Z"):
            close_time_str = close_time_str[:-1] + "+00:00"
        close_dt = datetime.datetime.fromisoformat(close_time_str)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        
        seconds_left = (close_dt - now_dt).total_seconds()
        if seconds_left <= 0:
            return 1.0 if spot > strike else 0.0
            
        T = seconds_left / (365.25 * 24 * 3600)  # Time in years
        sigma = volatility
        ln_sk = math.log(spot / strike)
        
        denom = sigma * math.sqrt(T)
        if denom == 0:
            return 1.0 if spot > strike else 0.0
            
        d2 = (ln_sk - (0.5 * sigma**2) * T) / denom
        prob = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
        return max(0.01, min(0.99, prob))
    except Exception as e:
        logger.error(f"Error computing prediction model: {e}")
        return 0.5

# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **ETH, XRP, NEAR 15-Min Quantitative Prediction Bot**\n\n"
        "Commands:\n"
        "• `/fetchstrikes` - View active contracts & target strikes\n"
        "• `/predict` - Run mathematical prediction & edge analysis\n"
        "• `/startalerts` - Turn on 20s automated mispricing alerts\n"
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
        msg += f"• **[{m['coin']}] {m['title']}**\n  Ticker: `{m['ticker']}`\n  Strike: **{strike_str}** | Yes: **{yes_str}**\n\n"
        
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔮 Running quantitative prediction models...")
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found.")
        return
        
    msg = "📊 **15-Min Quantitative Prediction & Edge Analysis**\n\n"
    cache_spot = {}
    
    for m in markets[:6]:
        coin = m['coin']
        info = SERIES_MAPPING.get(coin, {})
        symbol = info.get("symbol")
        vol = info.get("vol", 0.8)
        
        if coin not in cache_spot:
            cache_spot[coin] = get_live_spot_price(symbol)
            
        spot = cache_spot[coin]
        strike = m['strike']
        yes_price = m['yes_price']
        close_time = m['close_time']
        
        prediction_text = "Calculations unavailable"
        if spot and isinstance(strike, (int, float)):
            model_prob = calculate_win_probability(spot, strike, close_time, vol)
            model_prob_pct = int(model_prob * 100)
            
            if isinstance(yes_price, (int, float)):
                market_prob_pct = int(yes_price * 100)
                edge = model_prob_pct - market_prob_pct
                
                if edge >= 15:
                    prediction_text = f"🔥 **STRONG BUY YES** (Model: {model_prob_pct}% vs Market: {market_prob_pct}% | Edge: +{edge}%)"
                elif edge <= -15:
                    prediction_text = f"⚠️ **OVERPRICED YES / BUY NO** (Model: {model_prob_pct}% vs Market: {market_prob_pct}% | Edge: {edge}%)"
                else:
                    prediction_text = f"⚖️ **Fairly Priced** (Model: {model_prob_pct}% vs Market: {market_prob_pct}%)"
            else:
                prediction_text = f"📈 **Model Win Probability:** {model_prob_pct}% (Market Odds N/A)"

        spot_str = f"${spot:,.4f}" if spot else "Unavailable"
        strike_str = f"${strike:,.4f}" if isinstance(strike, (int, float)) else "N/A"

        msg += f"• **[{coin}] {m['title']}**\n  Strike: **{strike_str}** | Spot: **{spot_str}**\n  Prediction: {prediction_text}\n\n"

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
        close_time = m['close_time']
        
        if not isinstance(strike, (int, float)) or not isinstance(yes_price, (int, float)):
            continue

        info = SERIES_MAPPING.get(coin, {})
        symbol = info.get("symbol")
        vol = info.get("vol", 0.8)

        if coin not in cache_spot:
            cache_spot[coin] = get_live_spot_price(symbol)
            
        spot = cache_spot[coin]
        if not spot:
            continue

        model_prob = calculate_win_probability(spot, strike, close_time, vol)
        model_prob_pct = int(model_prob * 100)
        market_prob_pct = int(yes_price * 100)
        edge = model_prob_pct - market_prob_pct

        # Trigger push alert if model finds a major mispricing edge (+20% or more)
        if edge >= 20:
            key = f"{ticker}_EDGE"
            if key not in context.bot_data["alerted"]:
                context.bot_data["alerted"].add(key)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚨 **PREDICTION EDGE ALERT [{coin}]** 🚨\n`{ticker}`\nStrike: ${strike:,.4f}\nSpot: ${spot:,.4f}\nModel Win Odds: **{model_prob_pct}%**\nMarket Yes Price: **{market_prob_pct}%**\n⚡ Edge: **+{edge}%**"
                )

async def start_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    context.job_queue.run_repeating(monitor_job, interval=20, first=1, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text("🚨 **Quantitative Prediction Alerts Enabled!**")

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
    app.add_handler(CommandHandler("predict", analyze_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("startalerts", start_alerts))
    app.add_handler(CommandHandler("stopalerts", stop_alerts))

    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
