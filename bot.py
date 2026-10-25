import os
import time
import math
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

def start_health_check_server():
    """Starts an HTTP server on 0.0.0.0:PORT so Render Web Service passes health checks."""
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            
        def log_message(self, format, *args):
            pass

    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
        logger.info(f"Health check HTTP server successfully bound on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to bind web server on port {PORT}: {e}")

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

def norm_cdf(x):
    """Standard normal cumulative distribution function using math.erf"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_realized_volatility(coin: str) -> float:
    """Calculates annualized volatility sigma from recent SPOT_HISTORY buffer"""
    history = SPOT_HISTORY.get(coin, [])
    if len(history) < 4:
        return 0.75  # Default baseline annualized volatility for crypto (75%)
    
    prices = [p[1] for p in history]
    returns = []
    for i in range(1, len(
