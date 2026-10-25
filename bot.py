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
            for
