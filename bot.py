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
            no_volume
