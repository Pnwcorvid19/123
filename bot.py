import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from kalshi_python_sync import Configuration, KalshiClient

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
KALSHI_KEY_ID = os.getenv("KALSHI_KEY_ID")
KALSHI_PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY")

# Initialize Official Kalshi Client using Configuration object
kalshi_client = None
try:
    if KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PEM:
        formatted_key = KALSHI_PRIVATE_KEY_PEM.replace("\\n", "\n")
        
        config = Configuration(host="https://external-api.kalshi.com/trade-api/v2")
        config.api_key_id = KALSHI_KEY_ID
        config.private_key_pem = formatted_key
        
        kalshi_client = KalshiClient(config)
        logger.info("Kalshi client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Kalshi client: {e}")

# Mapping coins to their exact 15-minute Kalshi series tickers & CF Index IDs
SERIES_MAPPING = {
    "BTC": {"series": "KXBTC15M", "index": "BRTI"},
    "ETH": {"series": "KXETH15M", "index": "ETHUSD_RTI"},
    "SOL": {"series": "KXSOL15M", "index": "SOLUSD_RTI"},
    "XRP": {"series": "KXXRP15M", "index": "XRPUSD_RTI"},
    "DOGE": {"series": "KXDOGE15M", "index": "DOGEUSD_RTI"},
    "SUI": {"series": "KXSUI15M", "index": "SUIUSD_RTI"}
}

def fetch_15m_crypto_markets():
    """Fetches active 15-minute crypto markets by explicitly querying each coin's series ticker"""
    results = []
    for coin, info in SERIES_MAPPING.items():
        series_ticker = info["series"]
        try:
            resp = requests.get(
                "https://external-api.kalshi.com/trade-api/v2/markets",
                params={"series_ticker": series_ticker, "status": "open"},
                timeout=5
            )
            data = resp.json()
            markets = data.get("markets", [])
            
            for m in markets:
                ticker = str(m.get("ticker", "")).upper()
                title = str(m.get("title", ""))
                strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
                if isinstance(yes_bid, (int, float)) and yes_bid > 1.0:
                    yes_bid = yes_bid / 100.0

                results.append({
                    "coin": coin,
                    "ticker": ticker,
                    "title": title,
                    "strike": strike,
                    "yes_price": yes_bid
                })
        except Exception as e:
            logger.error(f"Error fetching markets for {series_ticker}: {e}")
            
    return results

def get_cf_index(index_id: str):
    """Fetches official CF Benchmarks value using the authenticated SDK client API call"""
    if not kalshi_client:
        return None
    try:
        response_data = kalshi_client.api_client.call_api(
            "/trade-api/v2/cfbenchmarks/values",
            "GET",
            query_params=[("id", index_id)],
            response_type=object
        )
        payload = response_data[0].get("data", {}).get("payload", {})
        val = payload.get("val")
        avg_60s = payload.get("avg_60s_data", {}).get("avg") or val
        return {"current": val, "avg_60s": avg_60s}
    except Exception as e:
        logger.error(f"Error pulling CF benchmark for {index_id}: {e}")
        return None

# --- Telegram Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Kalshi 15-Min Multi-Crypto Bot Active**\n\n"
        "Commands:\n"
        "• `/fetchstrikes` - View active 15m crypto target prices\n"
        "• `/analyze` - Run official settlement index analysis & predictions\n"
        "• `/startalerts` - Turn on 20s background push notifications\n"
        "• `/stopalerts` - Turn off background alerts"
    )

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markets = fetch_15m_crypto_markets()
    if not markets:
        await update.message.reply_text("⚠️ No active 15-minute crypto markets found right now.")
        return
        
    msg = "⏱ **Active 15-Min Crypto Contracts**\n\n"
    for m in markets[:8]:
        strike_str = f"${m['strike']:,.2f}" if isinstance(m['strike'], (int, float)) else "N/A"
        yes_str = f"${m['yes_price']:.2f}" if isinstance(m['yes_price'], (int, float)) else "N/A"
        msg += f"• **[{m['coin']}] {m['title']}**\n  Ticker: `{m['ticker']}`\n  Strike: **{strike_str}** | Yes: **{yes_str}**\n\n"
        
    await update.message.reply_text(msg
