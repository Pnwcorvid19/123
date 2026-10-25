import os
import time
import logging
import datetime
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
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

def fetch_single_market(coin, series_ticker):
    results = []
    try:
        resp = requests.get(
            f"{KALSHI_BASE_URL}/trade-api/v2/markets",
            params={"series_ticker": series_ticker, "status": "open"},
            timeout=4
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
    """Fetches active 15-minute crypto markets concurrently across all coins"""
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_single_market, coin, ticker): coin for coin, ticker in SERIES_MAPPING.items()}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.extend(res)
    return results

def fetch_kalshi_orderbook_imbalance(ticker: str) -> dict:
    """Queries Kalshi's public order book to measure YES vs NO depth imbalance and spread"""
    try:
        resp = requests.get(f"{KALSHI_BASE_URL}/trade-api/v2/markets/{ticker}/orderbook", timeout=2)
        if resp.status_code == 200:
            data = resp.json().get("orderbook", {})
            yes_orders = data.get("yes", [])
            no_orders = data.get("no", [])
            
            yes_volume = sum([o[1] for o in yes_orders if len(o) >= 2])
            no_volume = sum([o[1] for o in no_orders if len(o) >= 2])
            
            total_vol = yes_volume + no_volume
            ratio = (yes_volume / total_vol) if total_vol > 0 else 0.5
            return {"yes_vol": yes_volume, "no_vol": no_volume, "ratio": ratio}
    except Exception as e:
        logger.error(f"Error fetching orderbook for {ticker}: {e}")
    return {"yes_vol": 0, "no_vol": 0, "ratio": 0.5}

def get_live_spot_price(coin: str):
    """Multi-source fallback spot price fetcher with 1-minute velocity tracking"""
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

    # Record spot history for velocity calculation
    now = time.time()
    if current_price:
        if coin not in SPOT_HISTORY:
            SPOT_HISTORY[coin] = []
        SPOT_HISTORY[coin].append((now, current_price))
        SPOT_HISTORY[coin] = [p for p in SPOT_HISTORY[coin] if now - p[0] <= 300]

    return current_price

def get_spot_velocity(coin: str) -> float:
    """Calculates 1-minute rate of price change (%)"""
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
