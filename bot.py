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

# Minimum safety distance from strike to filter out market noise
MIN_BUFFERS = {
    "BTC": 30.0,
    "ETH": 4.0,
    "SOL": 0.5,
    "BNB": 1.0,
    "NEAR": 0.02,
    "XRP": 0.01,
    "ZEC": 0.2,
    "HYPE": 0.05
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

def evaluate_forward_prediction(coin, spot, strike, yes_bid, yes_ask, vel, ob_ratio, secs_left, avg_market_beta):
    if spot is None or not isinstance(strike, (int, float)) or strike <= 0:
        return "PENDING", "Insufficient market data for forecasting."
        
    diff = spot - strike
    abs_diff = abs(diff)
    pct_diff = (diff / strike) * 100
    yes_cents = int(yes_bid * 100) if isinstance(yes_bid, (int, float)) else 50
    spread = abs(yes_ask - yes_bid) if isinstance(yes_ask, (int, float)) and isinstance(yes_bid, (int, float)) else 0.05
    mins_left = secs_left // 60
    
    # Minimum buffer distance check (filters out noise when spot is too close to strike)
    min_buf = MIN_BUFFERS.get(coin, 1.0)
    is_within_noise_zone = abs_diff < min_buf
    
    momentum_bias = vel * 2.5
    depth_bias = (ob_ratio - 0.5) * 3.0
    beta_bias = 0.4 if avg_market_beta > 0 else -0.4
    
    if secs_left <= 90: # Final 90 seconds execution window
        if is_within_noise_zone:
            return "TOSS-UP ⚪", f"Too close to strike (${abs_diff:.4f} < min buffer${min_buf}). High risk of noise reversal."
        
        if diff > 0:
            score = 5.0 + (pct_diff * 100) + momentum_bias
        else:
            score = -5.0 + (pct_diff * 100) + momentum_bias
    else:
        score = (pct_diff * 40) + (momentum_bias * 2.5) + (depth_bias * 2.0) + beta_bias
        
    prediction = "PENDING"
    rationale = ""
    
    if is_within_noise_zone and secs_left <= 90:
        prediction = "TOSS-UP ⚪"
        rationale = f"Within noise buffer (${abs_diff:.4f} gap <${min_buf}). Too close to call."
    elif score > 0.6:
        prediction = "YES 🟢"
        if secs_left <= 90 and yes_cents <= 75 and spread <= 0.08 and not is_within_noise_zone:
            rationale = f"**FINAL 90S HIGH EDGE** (+${diff:.4f} above strike, safe buffer). **BUY YES at {yes_cents}¢**"
        else:
            rationale = f"Bullish trajectory | Spot +${diff:.4f} above strike | Vel: `{vel:+.2f}%`, OB: `{int(ob_ratio*100)}% YES`"
    elif score < -0.6:
        prediction = "NO 🔴"
        if secs_left <= 90 and yes_cents >= 25 and spread <= 0.08 and not is_within_noise_zone:
            rationale = f"**FINAL 90S HIGH EDGE** (-${abs_diff:.4f} below strike, safe buffer). **BUY NO / YES is {yes_cents}¢**"
        else:
            rationale = f"Bearish trajectory | Spot -${abs_diff:.4f} below strike | Vel: `{vel:+.2f}%`, OB: `{int(ob_ratio*100)}% YES`"
    else:
        prediction = "TOSS-UP ⚪"
        rationale = f
