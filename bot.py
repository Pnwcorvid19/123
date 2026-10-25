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

SPOT_HISTORY = {}

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
            params={"series_ticker": series_ticker, "status": "open", "mve_filter": "exclude"},
            timeout=3
        )
        if resp.status_code == 200:
            markets = resp.json().get("markets", [])
            for m in markets:
                ticker = str(m.get("ticker", "")).upper()
                title = str(m.get("title", ""))
                
                raw_strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                strike = None
                if raw_strike is not None:
                    try:
                        strike = float(raw_strike)
                    except (ValueError, TypeError):
                        strike = None

                close_time_str = m.get("close_time")
                
                raw_yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
                raw_yes_ask = m.get("yes_ask_dollars") or m.get("yes_ask")
                
                yes_bid = 0.5
                if raw_yes_bid is not None:
                    try:
                        val = float(raw_yes_bid)
                        yes_bid = val / 100.0 if val > 1.0 else val
                    except (ValueError, TypeError):
                        pass

                yes_ask = 0.5
                if raw_yes_ask is not None:
                    try:
                        val = float(raw_yes_ask)
                        yes_ask = val / 100.0 if val > 1.0 else val
                    except (ValueError, TypeError):
                        pass

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
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_single_market, coin, ticker): coin for coin, ticker in SERIES_MAPPING.items()}
            for future in as_completed(futures):
                res = future.result()
                if res:
                    results.extend(res)
    except Exception as e:
        logger.error(f"Error in fetch_15m_crypto_markets: {e}")
    return results

def fetch_kalshi_orderbook_imbalance(ticker: str) -> dict:
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
                if val is not None:
                    current_price = float(val)
    except Exception:
        pass

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
                    if val is not None:
                        current_price = float(val)
        except Exception:
            pass

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
                    if val is not None:
                        current_price = float(val)
        except Exception:
            pass

    try:
        now = time.time()
        if current_price is not None:
            if coin not in SPOT_HISTORY:
                SPOT_HISTORY[coin] = []
            SPOT_HISTORY[coin].append((now, current_price))
            SPOT_HISTORY[coin] = [p for p in SPOT_HISTORY[coin] if now - p[0] <= 300]
    except Exception as e:
        logger.error(f"Error updating spot history for {coin}: {e}")

    return current_price

def get_spot_velocity(coin: str) -> float:
    try:
        history = SPOT_HISTORY.get(coin, [])
        if len(history) < 2:
            return 0.0
        
        now = time.time()
        recent = history[-1][1]
        past = next((p[1] for p in history if now - p[0] >= 45), history[0][1])
        
        if past == 0:
            return 0.0
        return ((recent - past) / past) * 100.0
    except Exception:
        return 0.0

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

def evaluate_forward_prediction(spot, strike, yes_bid, yes_ask, vel, ob_ratio, secs_left, avg_market_beta):
    if spot is None or not isinstance(strike, (int, float)) or strike <= 0:
        return "PENDING", "Insufficient market data for forecasting."
        
    diff = spot - strike
    pct_diff = (diff / strike) * 100
    yes_cents = int(yes_bid * 100) if isinstance(yes_bid, (int, float)) else 50
    spread = abs(yes_ask - yes_bid) if isinstance(yes_ask, (int, float)) and isinstance(yes_bid, (int, float)) else 0.05
    mins_left = secs_left // 60
    
    # Forward-Looking Trajectory Score Calculation
    momentum_bias = vel * 2.5       # Velocity momentum weight
    depth_bias = (ob_ratio - 0.5) * 3.0  # Orderbook imbalance weight
    beta_bias = 0.4 if avg_market_beta > 0 else -0.4
    
    if secs_left <= 180: # Final 3 minutes: Current position heavily dictates expiry outcome
        if diff > 0:
            score = 4.0 + (pct_diff * 80) + momentum_bias
        else:
            score = -4.0 + (pct_diff * 80) + momentum_bias
    else: # > 3 minutes left: Momentum and orderbook depth drive the forward projection
        score = (pct_diff * 40) + (momentum_bias * 2.5) + (depth_bias * 2.0) + beta_bias
        
    prediction = "PENDING"
    rationale = ""
    
    if score > 0.6:
        prediction = "YES 🟢"
        if secs_left <= 300 and yes_cents <= 70 and spread <= 0.08:
            rationale = f"**PROJECTED ABOVE STRIKE** (+${diff:.4f}, {mins_left}m left). Vel: `{vel:+.2f}%`, OB: `{int(ob_ratio*100)}% YES`. **HIGH EDGE BUY YES at {yes_cents}¢**"
        else:
            rationale = f"Bullish trajectory toward expiry | Spot +${diff:.4f} above strike | Vel: `{vel:+.2f}%`, OB: `{int(ob_ratio*100)}% YES`"
    elif score < -0.6:
        prediction = "NO 🔴"
        if secs_left <= 300 and yes_cents >= 30 and spread <= 0.08:
            rationale = f"**PROJECTED BELOW STRIKE** (-${abs(diff):.4f}, {mins_left}m left). Vel: `{vel:+.2f}%`, OB: `{int(ob_ratio*100)}% YES`. **HIGH EDGE BUY NO / YES is {yes_cents}¢**"
        else:
            rationale = f"Bearish trajectory toward expiry | Spot -${abs(diff):.4f} below strike | Vel: `{vel:+.2f}%`, OB: `{int(ob_ratio*100)}% YES`"
    else:
        prediction = "TOSS-UP ⚪"
        rationale = f"High volatility / Zone boundary | Diff: ${diff:+.4f} | Vel: `{vel:+.2f}%`, OB: `{int(ob_ratio*100)}% YES`"
        
    return prediction, rationale

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(
            "🤖 **Crypto Multi-Factor Prediction Bot Active**\n\n"
            "Commands:\n"
            "• `/fetchstrikes` - View active 15m contracts & strikes\n"
            "• `/analyze` - Run forward-looking trajectory predictions\n"
            "• `/startalerts` - Turn on background alerts\n"
            "• `/stopalerts` - Turn off alerts"
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        markets = fetch_15m_crypto_markets()
        if not markets:
            await update.message.reply_text("⚠️ No active 15-minute markets found right now.")
            return
            
        msg = "⏱ **Active 15-Min Crypto Contracts**\n\n"
        for m in markets[:12]:
            c_coin = m["coin"]
            c_title = m["title"]
            c_ticker = m["ticker"]
            strike_val = m["strike"]
            yes_val = m["yes_bid"]
            
            strike_str = f"${strike_val:,.4f}" if isinstance(strike_val, (int, float)) else "N/A"
            yes_bid_str = f"${yes_val:.2f}" if isinstance(yes_val, (int, float)) else "N/A"
            secs = get_seconds_left(m["close_time"])
            mins = secs // 60
            
            msg += f"• **[{c_coin}] {c_title}**\n  Ticker: `{c_ticker}`\n  Strike: **{strike_str}** | Yes Bid: **{yes_bid_str}**\n  ⏳ Time Left: **{mins}m ({secs}s)**\n\n"
            
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error in fetch_strikes_command: {e}")

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("🔍 Analyzing forward trajectory, velocity momentum, and orderbook depth across all coins...")
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
            ob_futures = {executor.submit(fetch_kalshi_orderbook_imbalance, m["ticker"]): m["ticker"] for m in active_markets}
            
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

        msg = "📊 **Forward-Looking Expiry Forecast & Analysis**\n\n"
        
        for m in active_markets:
            coin = m["coin"]
            ticker = m["ticker"]
            title = m["title"]
            spot = cache_spot.get(coin)
            strike = m["strike"]
            yes_bid = m["yes_bid"] if isinstance(m["yes_bid"], (int, float)) else 0.5
            yes_ask = m["yes_ask"] if isinstance(m["yes_ask"], (int, float)) else 0.5
            secs_left = get_seconds_left(m["close_time"])
            
            ob = cache_ob.get(ticker, {"ratio": 0.5})
            vel = cache_vel.get(coin, 0.0)
            spread = abs(yes_ask - yes_bid)
            
            prediction, rationale = evaluate_forward_prediction(
                spot, strike, yes_bid, yes_ask, vel, ob["ratio"], secs_left, avg_velocity
            )

            spot_str = f"${spot:,.4f}" if spot is not None else "Unavailable"
            strike_str = f"${strike:,.4f}" if isinstance(strike, (int, float)) else "N/A"
            spread_cents = int(spread * 100)
            ob_ratio_pct = int(ob["ratio"] * 100)

            msg += (
                f"• **[{coin}] {title}**\n"
                f"  Strike: **{strike_str}** | Spot: **{spot_str}** (1m Vel: `{vel:+.2f}%`)\n"
                f"  Spread: **{spread_cents}¢** | Orderbook Depth: **{ob_ratio_pct}% YES**\n"
                f"  🔮 **Forecast: {prediction}**\n"
                f"  📝 *{rationale}*\n\n"
            )

        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error in analyze_command: {e}")

async def monitor_job(context: ContextTypes.DEFAULT_TYPE):
    try:
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
            ob_futures = {executor.submit(fetch_kalshi_orderbook_imbalance, m["ticker"]): m["ticker"] for m in markets}
            
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

        for m in markets:
            coin = m["coin"]
            ticker = m["ticker"]
            strike = m["strike"]
            yes_bid = m["yes_bid"] if isinstance(m["yes_bid"], (int, float)) else 0.5
            yes_ask = m["yes_ask"] if isinstance(m["yes_ask"], (int, float)) else 0.5
            secs_left = get_seconds_left(m["close_time"])
            
            if not isinstance(strike, (int, float)):
                continue

            spot = cache_spot.get(coin)
            vel = cache_vel.get(coin, 0.0)
            if spot is None:
                continue

            spread = abs(yes_ask - yes_bid)
            prediction, _ = evaluate_forward_prediction(
                spot, strike, yes_bid, yes_ask, vel, cache_ob.get(ticker, {}).get("ratio", 0.5), secs_left, avg_velocity
            )
            yes_cents = int(yes_bid * 100)
            ob_ratio_pct = int(cache_ob.get(ticker, {}).get("ratio", 0.5) * 100)

            if secs_left <= 300 and secs_left > 0 and spread <= 0.08:
                if prediction == "YES 🟢" and yes_cents <= 65:
                    key = f"{ticker}_FORWARD_YES"
                    if key not in context.bot_data["alerted"]:
                        context.bot_data["alerted"].add(key)
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🚨 **EXPIRY FORECAST: BUY YES [{coin}]** 🚨\n"
                                f"`{ticker}`\n"
                                f"⏳ Time Left: {secs_left}s\n"
                                f"Strike: ${strike:,.4f} | Spot: ${spot:,.4f}\n"
                                f"Velocity: `{vel:+.2f}%` | Orderbook: `{ob_ratio_pct}% YES`\n"
                                f"Yes Price: **{yes_cents}¢** (Projected to clear strike!)"
                            )
                        )
                elif prediction == "NO 🔴" and yes_cents >= 35:
                    key = f"{ticker}_FORWARD_NO"
                    if key not in context.bot_data["alerted"]:
                        context.bot_data["alerted"].add(key)
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🚨 **EXPIRY FORECAST: BUY NO [{coin}]** 🚨\n"
                                f"`{ticker}`\n"
                                f"⏳ Time Left: {secs_left}s\n"
                                f"Strike: ${strike:,.4f} | Spot: ${spot:,.4f}\n"
                                f"Velocity: `{vel:+.2f}%` | Orderbook: `{ob_ratio_pct}% YES`\n"
                                f"Yes Price: **{yes_cents}¢** (Projected to finish below strike!)"
                            )
                        )
    except Exception as e:
        logger.error(f"Error in monitor_job: {e}")

async def start_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        for job in context.job_queue.get_jobs_by_name(str(chat_id)):
            job.schedule_removal()
        context.job_queue.run_repeating(monitor_job, interval=20, first=1, chat_id=chat_id, name=str(chat_id))
        await update.message.reply_text("🚨 **Forward-Looking Expiry Alerts Enabled!**")
    except Exception as e:
        logger.error(f"Error in start_alerts: {e}")

async def stop_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        jobs = context.job_queue.get_jobs_by_name(str(chat_id))
        if not jobs:
            await update.message.reply_text("ℹ️ No alerts running.")
            return
        for j in jobs:
            j.schedule_removal()
        await update.message.reply_text("🛑 **Alerts stopped.**")
    except Exception as e:
        logger.error(f"Error in stop_alerts: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

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
    
    app.add_error_handler(error_handler)

    logger.info("Starting bot polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
