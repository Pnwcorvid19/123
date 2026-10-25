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

# --- Render Port Binder ---
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
        # mve_filter="exclude" is required by Kalshi API v2 when querying by series_ticker
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
                
                # Safe Strike Parsing
                raw_strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                strike = None
                if raw_strike is not None:
                    try:
                        strike = float(raw_strike)
                    except (ValueError, TypeError):
                        strike = None

                close_time_str = m.get("close_time")
                
                # Safe Bid/Ask Parsing
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
        else:
            logger.warning(f"Kalshi API returned status {resp.status_code} for series {series_ticker}: {resp.text}")
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
                if val is not None:
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
                    if val is not None:
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
