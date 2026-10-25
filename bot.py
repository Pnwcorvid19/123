import os
import time
import logging
import datetime
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))
KALSHI_BASE_URL = "https://external-api.kalshi.com"

# Expanded list including ETH, XRP, NEAR, BTC, SOL, BNB, ZEC, and HYPE 15-minute series
SERIES_MAPPING = {
    "ETH": "KXETH15M",
    "XRP": "KXXRP15M",
    "NEAR": "KXNEAR15M",
    "BTC": "KXBTC15M",
    "SOL": "KXSOL15M",
    "BNB": "KXBNB15M",
    "ZEC": "KXZEC15M",
    "HYPE": "KXHYPE15M"
}

# Cache for spot velocity tracking
SPOT_HISTORY = {}

# --- Render Port Binder (Satisfies Web Service Requirement) ---
def run_dummy_web_server():
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is active and running!")
        def log_message(self, format, *args):
            pass

    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        server.serve_forever()
    except Exception as e:
        logger.error(f"Web server error: {e}")

def fetch_single_market(coin, series_ticker):
    results = []
    try:
        resp = requests.get(
            f"{KALSHI_BASE_URL}/trade-api/v2/markets",
            params={"series_ticker": series_ticker, "status": "open"},
            timeout=3
        )
        if resp.status_code == 200:
            markets = resp.json().get("markets", [])
            for m in markets:
                ticker = str(m.get("ticker", "")).upper()
                title = str(m.get("title", ""))
                strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                close_time_str = m.get("close_time")
                
                yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
                yes_ask = m.get("yes_ask_dollars") or m.get("yes_ask")
                
                if isinstance(yes_bid, (int, float)) and yes_bid > 1.0:
                    yes_bid = yes_bid / 100.0
                if isinstance(yes_ask, (int, float)) and yes_ask > 1.0:
                    yes_ask = yes_ask / 100.0

                results.append({
                    "coin": coin,
                    "ticker": ticker,
                    "title": title,
                    "strike": strike,
                    "close_time": close_time_str,
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask
                })
    except Exception as e:
        logger.error(f"Error fetching markets for {series_ticker}: {e}")
    return results

def fetch_15m_crypto_markets():
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_single_market, coin, ticker): coin for coin, ticker in SERIES_MAPPING.items()}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.extend(res)
    return results

def fetch_kalshi_orderbook_imbalance(ticker: str) -> dict:
    """Queries Kalshi's public order book using correct v2 orderbook_fp schema"""
    try:
        resp = requests.get(f"{KALSHI_BASE_URL}/trade-api/v2/markets/{ticker}/orderbook", timeout=2)
        if resp.status_code == 200:
            body = resp.json()
            ob = body.get("orderbook_fp") or body.get("orderbook") or {}
            yes_orders = ob.get("yes_dollars") or ob.get("yes") or []
            no_orders = ob.get("no_dollars") or ob.get("no") or []
            
            yes_volume = sum([float(o[1]) for o in yes_orders if len(o) >= 2])
            no_volume = sum([float(o[1]) for o in no_orders if len(o) >= 2])
            
            total_vol = yes_volume + no_volume
            ratio = (yes_volume / total_vol) if total_vol > 0 else 0.5
            return {"yes_vol": yes_volume, "no_vol": no_volume, "ratio": ratio}
    except Exception as e:
        logger.error(f"Error fetching orderbook for {ticker}: {e}")
    return {"yes_vol": 0, "no_vol": 0, "ratio": 0.5}

def get_live_spot_price(coin: str):
    current_price = None
    
    # 1. CoinGecko
    try:
        cg_ids = {
            "ETH": "ethereum", "XRP": "ripple", "NEAR": "near",
            "BTC": "bitcoin", "SOL": "solana", "BNB": "binancecoin",
            "ZEC": "zcash", "HYPE": "hyperliquid"
        }
        cg_id = cg_ids.get(coin)
        if cg_id:
            resp = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd", timeout=2)
            if resp.status_code == 200:
                val = resp.json().get(cg_id, {}).get("usd")
                if val:
                    current_price = float(val)
    except Exception:
        pass

    # 2. Coinbase Fallback
    if not current_price:
        try:
            cb_symbols = {
                "ETH": "ETH-USD", "XRP": "XRP-USD", "NEAR": "NEAR-USD",
                "BTC": "BTC-USD", "SOL": "SOL-USD", "BNB": "BNB-USD",
                "ZEC": "ZEC-USD", "HYPE": "HYPE-USD"
            }
            symbol = cb_symbols.get(coin)
            if symbol:
                resp = requests.get(f"https://api.coinbase.com/v2/prices/{symbol}/spot", timeout=2)
                if resp.status_code == 200:
                    val = resp.json().get("data", {}).get("amount")
                    if val:
                        current_price = float(val)
        except Exception:
            pass

    # 3. CoinCap Fallback
    if not current_price:
        try:
            cc_ids = {
                "ETH": "ethereum", "XRP": "xrp", "NEAR": "near",
                "BTC": "bitcoin", "SOL": "solana", "BNB": "binance-coin",
                "ZEC": "zcash", "HYPE": "hyperliquid"
            }
            cc_id = cc_ids.get(coin)
            if cc_id:
                resp = requests.get(f"https://api.coincap.io/v2/assets/{cc_id}", timeout=2)
                if resp.status_code == 200:
                    val = resp.json().get("data", {}).get("priceUsd")
                    if val:
                        current_price = float(val)
        except Exception:
            pass

    now = time.time()
    if current_price:
        if coin not in SPOT_HISTORY:
            SPOT_HISTORY[coin] = []
        SPOT_HISTORY[coin].append((now, current_price))
        SPOT_HISTORY[coin] = [p for p in SPOT_HISTORY[coin] if now - p[0] <= 300]

    return current_price

def get_spot_velocity(coin: str) -> float:
    history = SPOT_HISTORY.get(coin, [])
    if len(history) < 2:
        return 0.0
    
    now = time.time()
    recent = history[-1][1]
    past = next((p[1] for p in history if now - p[0] >= 45), history[0][1])
    
    if past == 0:
        return 0.0
    return ((recent - past) / past) * 100.0

def get_seconds_left(close_time_str: str) -> int:
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
        "🤖 **Crypto Multi-Factor Prediction Bot Active**\n\n"
        "Commands:\n"
        "• `/fetchstrikes` - View active 15m contracts & strikes\n"
        "• `/analyze` - Run multi-factor YES/NO predictions\n"
        "• `/startalerts` - Turn on background alerts\n"
        "• `/stopalerts` - Turn off alerts"
    )

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found right now.")
        return
        
    msg = "⏱ **Active 15-Min Crypto Contracts**\n\n"
    for m in markets[:12]:
        strike_str = f"${m['strike']:,.4f}" if isinstance(m['strike'], (int, float)) else "N/A"
        yes_bid_str = f"${m['yes_bid']:.2f}" if isinstance(m['yes_bid'], (int, float)) else "N/A"
        secs = get_seconds_left(m['close_time'])
        mins = secs // 60
        msg += f"• **[{m['coin']}] {m['title']}**\n  Ticker: `{m['ticker']}`\n  Strike: **{strike_str}** | Yes Bid: **{yes_bid_str}**\n  ⏳ Time Left: **{mins}m ({secs}s)**\n\n"
        
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analyzing spot velocity, orderbook depth, spreads, and market beta across all coins...")
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute markets found.")
        return
        
    cache_spot = {}
    cache_vel = {}
    cache_ob = {}
    
    active_markets = markets[:10]

    with ThreadPoolExecutor(max_workers=16) as executor:
        spot_futures = {executor.submit(get_live_spot_price, coin): coin for coin in SERIES_MAPPING.keys()}
        ob_futures = {executor.submit(fetch_kalshi_orderbook_imbalance, m['ticker']): m['ticker'] for m in active_markets}
        
        for future in as_completed(spot_futures):
            coin = spot_futures[future]
            try:
                cache_spot[coin] = future.result()
                cache_vel[coin] = get_spot_velocity(coin)
            except Exception:
                cache_spot[coin] = None
                cache_vel[coin] = 0.0

        for future in as_completed(ob_futures):
            ticker = ob_futures[future]
            try:
                cache_ob[ticker] = future.result()
            except Exception:
                cache_ob[ticker] = {"yes_vol": 0, "no_vol": 0, "ratio": 0.5}

    valid_vels = [v for v in cache_vel.values() if v != 0.0]
    avg_velocity = sum(valid_vels) / len(valid_vels) if valid_vels else 0.0

    msg = "📊 **Multi-Factor Prediction & Market Depth Analysis**\n\n"
    
    for m in active_markets:
        coin = m['coin']
        ticker = m['ticker']
        spot = cache_spot.get(coin)
        strike = m['strike']
        yes_bid = m['yes_bid'] or 0.5
        yes_ask = m['yes_ask'] or 0.5
        secs_left = get_seconds_left(m['close_time'])
        mins_left = secs_left // 60
        
        ob = cache_ob.get(ticker, {"ratio": 0.5})
        vel = cache_vel.get(coin, 0.0)
        spread = abs(yes_ask - yes_bid)
        
        prediction = "PENDING"
        rationale = "Insufficient data"
        
        if spot and isinstance(strike, (int, float)):
            diff = spot - strike
            yes_cents = int(yes_bid * 100)
            
            if spot > strike:
                prediction = "YES 🟢"
                factors = []
                if vel > 0.05:
                    factors.append("🚀 Strong Positive Velocity")
                if ob['ratio'] > 0.55:
                    factors.append("🛡️ YES Orderbook Support")
                if avg_velocity > 0:
                    factors.append("🌐 Positive Market Beta")
                
                factor_str = " | ".join(factors) if factors else "Spot holds above strike"
                
                if secs_left <= 300 and yes_cents <= 70 and spread <= 0.08:
                    rationale = f"Spot +${diff:.4f} above strike with {mins_left}m left. **HIGH CONFIDENCE BUY YES at {yes_cents}¢** ({factor_str})"
                else:
                    rationale = f"Spot +${diff:.4f} above strike ({mins_left}m left) | {factor_str}"
            else:
                prediction = "NO 🔴"
                factors = []
                if vel < -0.05:
                    factors.append("📉 Negative Velocity")
                if ob['ratio'] < 0.45:
                    factors.append("🧱 Heavy NO Wall")
                if avg_velocity < 0:
                    factors.append("🌐 Negative Market Beta")
                
                factor_str = " | ".join(factors) if factors else "Spot sits below strike"
                
                if secs_left <= 300 and yes_cents >= 30 and spread <= 0.08:
                    rationale = f"Spot -${abs(diff):.4f} below strike with {mins_left}m left. **HIGH CONFIDENCE BUY NO / YES is {yes_cents}¢** ({factor_str})"
                else:
                    rationale = f"Spot -${abs(diff):.4f} below strike ({mins_left}m left) | {factor_str}"

        spot_str = f"${spot:,.4f}" if spot else "Unavailable"
        strike_str = f"${strike:,.4f}" if isinstance(strike, (int, float)) else "N/A"
        spread_str = f"{int(spread * 100)}¢"

        msg += (
            f"• **[{coin}] {m['title']}**\n"
            f"  Strike: **{strike_str}** | Spot: **{spot_str}** (1m Vel: `{vel:+.2f}%`)\n"
            f"  Spread: **{spread_str}** | Orderbook Depth: **{int(ob['ratio']*100)}% YES**\n"
            f"  🔮 **Prediction: {prediction}**\n"
            f"  📝 *{rationale}*\n\n"
        )

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
    cache_vel = {}
    cache_ob = {}
    
    with ThreadPoolExecutor(max_workers=16) as executor:
        spot_futures = {executor.submit(get_live_spot_price, coin): coin for coin in SERIES_MAPPING.keys()}
        ob_futures = {executor.submit(fetch_kalshi_orderbook_imbalance, m['ticker']): m['ticker'] for m in markets}
        
        for future in as_completed(spot_futures):
            coin = spot_futures[future]
            try:
                cache_spot[coin] = future.result()
                cache_vel[coin] = get_spot_velocity(coin)
            except Exception:
                cache_spot[coin] = None
                cache_vel[coin] = 0.0

        for future in as_completed(ob_futures):
            ticker = ob_futures[future]
            try:
                cache_ob[ticker] = future.result()
            except Exception:
                cache_ob[ticker] = {"yes_vol": 0, "no_vol": 0, "ratio": 0.5}

    for m in markets:
        coin = m['coin']
        ticker = m['ticker']
        strike = m['strike']
        yes_bid = m['yes_bid'] or 0.5
        yes_ask = m['yes_ask'] or 0.5
        secs_left = get_seconds_left(m['close_time'])
        
        if not isinstance(strike, (int, float)):
            continue

        spot = cache_spot.get(coin)
        vel = cache_vel.get(coin, 0.0)
        if not spot:
            continue

        diff = spot - strike
        yes_cents = int(yes_bid * 100)
        spread = abs(yes_ask - yes_bid)

        if secs_left <= 300 and secs_left > 0 and spread <= 0.08:
            ob = cache_ob.get(ticker, {"ratio": 0.5})
            
            if spot > strike and yes_cents <= 65 and vel >= 0 and ob['ratio'] >= 0.50:
                key = f"{ticker}_MULTI_YES"
                if key not in context.bot_data["alerted"]:
                    context.bot_data["alerted"].add(key)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🚨 **MULTI-FACTOR BUY YES ALERT [{coin}]** 🚨\n"
                            f"`{ticker}`\n"
                            f"⏳ Time Left: {secs_left}s\n"
                            f"Strike: ${strike:,.4f} | Spot: ${spot:,.4f} (+${diff:.4f})\n"
                            f"1m Velocity: `{vel:+.2f}%` | Orderbook: `{int(ob['ratio']*100)}% YES`\n"
                            f"Yes Price: **{yes_cents}¢** (Underpriced Edge!)"
                        )
                    )
            elif spot < strike and yes_cents >= 35 and vel <= 0 and ob['ratio'] <= 0.50:
                key = f"{ticker}_MULTI_NO"
                if key not in context.bot_data["alerted"]:
                    context.bot_data["alerted"].add(key)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🚨 **MULTI-FACTOR BUY NO ALERT [{coin}]** 🚨\n"
                            f"`{ticker}`\n"
                            f"⏳ Time Left: {secs_left}s\n"
                            f"Strike: ${strike:,.4f} | Spot: ${spot:,.4f} (-${abs(diff):.4f})\n"
                            f"1m Velocity: `{vel:+.2f}%` | Orderbook: `{int(ob['ratio']*100)}% YES`\n"
                            f"Yes Price: **{yes_cents}¢** (Overpriced Edge!)"
                        )
                    )

async def start_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    context.job_queue.run_repeating(monitor_job, interval=20, first=1, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text("🚨 **Multi-Factor Prediction Alerts Enabled!**")

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

    threading.Thread(target=run_dummy_web_server, daemon=True).start()

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
