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

def start_health_check_server():
    """Starts an HTTP server on 0.0.0.0:PORT so Render Web Service passes health checks."""
    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            
        def log_message(self, format, *args):
            pass  # Suppress HTTP health log spam

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
                if
