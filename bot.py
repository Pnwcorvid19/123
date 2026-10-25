import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Setup logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
KALSHI_API_URL = "https://external-api.kalshi.com/trade-api/v2/markets"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"🤖 **Kalshi Live ETH Market & Probability Bot**\n\n"
        f"Commands:\n"
        f"• `/fetchstrikes` - List live Kalshi ETH markets and active strikes\n"
        f"• `/analyze` - Calculate probability vs spot price discrepancies"
    )

def fetch_kalshi_eth_markets():
    """Queries Kalshi's public API for active Ethereum markets"""
    try:
        response = requests.get(KALSHI_API_URL, params={"status": "open", "limit": 100}, timeout=10)
        data = response.json()
        markets = data.get("markets", [])
        
        eth_markets = []
        for m in markets:
            title = m.get("title", "").lower()
            ticker = m.get("ticker", "").lower()
            
            if "ethereum" in title or "eth" in ticker or "eth" in title:
                strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike") or None
                yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid") or None
                
                eth_markets.append({
                    "ticker": m.get("ticker"),
                    "title": m.get("title"),
                    "yes_price": yes_bid,
                    "strike": strike
                })
        return eth_markets
    except Exception as e:
        logger.error(f"Error fetching from Kalshi API: {
