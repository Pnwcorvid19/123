import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration (Replace with your actual tokens or use Environment Variables)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2" # or production API endpoint

def get_active_eth_15m_market():
    """Fetches the active Kalshi 15-minute Ethereum market."""
    try:
        # Searching for active 15-min ETH series ticker (e.g., KXETH15M)
        url = f"{KALSHI_API_BASE}/markets?series_ticker=KXETH15M&status=open"
        response = requests.get(url, timeout=10)
        data = response.json()
        markets = data.get("markets", [])
        if not markets:
            return None
        # Return the nearest expiring market
        return markets[0]
    except Exception as e:
        logger.error(f"Error fetching Kalshi market: {e}")
        return None

def get_current_eth_price():
    """
    Fetches the current ETH price from a reliable high-frequency spot source 
    or CF Benchmarks proxy feed.
    """
    try:
        # Using Coinbase/Binance public ticker as a fast proxy to CF Benchmarks spot behavior
        res = requests.get("https://api.coinbase.com/v2/prices/ETH-USD/spot", timeout=5)
        data = res.json()
        return float(data["data"]["amount"])
    except Exception as e:
        logger.error(f"Error fetching ETH price: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message when the command /start is issued."""
    await update.message.reply_text(
        "🤖 **Kalshi ETH 15-Min Sniper Bot Initialized**\n\n"
        "Use /check to analyze the active 15-minute Ethereum market right now."
    )

async def check_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyzes the active market and returns YES/NO prediction guidance."""
    msg = await update.message.reply_text("🔍 Fetching latest Kalshi strike and live ETH data...")
    
    market = get_active_eth_15m_market()
    current_price = get_current_eth_price()
    
    if not market or not current_price:
        await msg.edit_text("❌ Error: Could not fetch data from Kalshi or Price Feed APIs.")
        return

    title = market.get("title", "ETH 15-min Market")
    strike_price = market.get("floor_strike", None)
    
    # Fallback parsing if strike is embedded in subtitle/title if floor_strike key differs
    if strike_price is None:
        # Kalshi API structure adjustment if necessary
        strike_price = market.get("strike_price", 0.0)

    # Comparison logic
    is_above = current_price > float(strike_price) if strike_price else False
    prediction = "YES (Above Strike)" if is_above else "NO (Below Strike)"
    spread = abs(current_price - float(strike_price)) if strike_price else 0.0

    report = (
        f"📊 **Kalshi ETH 15-Min Market Status**\n\n"
        f"• **Market:** {title}\n"
        f"• **Strike Target:** ${strike_price}\n"
        f"• **Live Reference Price:** ${current_price:,.2f}\n"
        f"• **Spread:** ${spread:.2f}\n\n"
        f"🎯 **Current Lean:** **{prediction}**"
    )
    
    await msg.edit_text(report, parse_mode="Markdown")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_market))
    
    logger.info("Bot is polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
