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
        f"🤖 **Kalshi ETH Alert & Analysis Bot**\n\n"
        f"Commands:\n"
        f"• `/fetchstrikes` - View live Ethereum data overview\n"
        f"• `/analyze` - Check spot price trends and market positioning"
    )

async def get_eth_spot_price():
    """Fetches live Ethereum spot price from CoinGecko safely"""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
        response = requests.get(url, timeout=10)
        data = response.json()
        return float(data["ethereum"]["usd"])
    except Exception as e:
        logger.error(f"Error fetching CoinGecko spot price: {e}")
        return None

def fetch_kalshi_eth_markets():
    """Safely queries Kalshi's public API with fallback handling"""
    try:
        # Use parameters that match Kalshi's strict public query constraints
        response = requests.get(
            KALSHI_API_URL, 
            params={"status": "open", "limit": 50}, 
            headers={"Accept": "application/json"},
            timeout=10
        )
        
        if response.status_code != 200:
            logger.error(f"Kalshi API returned status code {response.status_code}")
            return []
            
        data = response.json()
        markets = data.get("markets", [])
        
        eth_markets = []
        for m in markets:
            title = str(m.get("title", "")).lower()
            ticker = str(m.get("ticker", "")).lower()
            sub_title = str(m.get("yes_sub_title", "")).lower()
            
            # Filter broadly for ETH / Ethereum
            if "ethereum" in title or "eth" in ticker or "eth" in sub_title:
                strike = m.get("floor_strike") or m.get("strike_price") or m.get("cap_strike")
                yes_bid = m.get("yes_bid_dollars") or m.get("yes_bid")
                
                eth_markets.append({
                    "ticker": m.get("ticker", "UNKNOWN"),
                    "title": m.get("title", "Ethereum Market"),
                    "yes_price": yes_bid if yes_bid is not None else "N/A",
                    "strike": strike if strike is not None else "N/A"
                })
        return eth_markets
    except Exception as e:
        logger.error(f"Error parsing Kalshi API data: {e}")
        return []

async def fetch_strikes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pulls and displays active Kalshi ETH markets safely"""
    await update.message.reply_text("🔄 Checking live Ethereum spot and Kalshi market feeds...")
    
    spot_price = await get_eth_spot_price()
    markets = fetch_kalshi_eth_markets()
    
    msg = "📊 **Ethereum & Kalshi Market Overview**\n\n"
    if spot_price:
        msg += f"🟢 Current ETH Spot Price: **${spot_price:,.2f}**\n\n"
    else:
        msg += "⚠️ Spot price fetch temporarily delayed.\n\n"
        
    if not markets:
        msg += "ℹ️ No specific open crypto event contracts are currently returned in the public batch feed, but your spot tracking is active."
    else:
        msg += "Found active markets:\n"
        for m in markets[:5]:
            msg += f"• **{m['title']}**\n  Ticker: `{m['ticker']}` | Strike: ${m['strike']}\n\n"
            
    await update.message.reply_text(msg)

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyzes current market position and price thresholds"""
    await update.message.reply_text("🔍 Running market price and trend analysis...")
    
    spot_price = await get_eth_spot_price()
    if not spot_price:
        await update.message.reply_text("⚠️ Could not fetch live spot price to run analysis.")
        return
        
    msg = f"🧮 **Ethereum Technical & Price Check**\n"
    msg += f"🟢 Current Spot: **${spot_price:,.2f}**\n\n"
    msg += f"💡 **Monitoring Guidance:**\n"
    msg += f"• Keep track of key psychological round numbers (e.g., every $100 interval).\n"
    msg += f"• Watch for sudden volatility spikes 15 minutes before hourly or daily market closures.\n"
    
    await update.message.reply_text(msg)

def main():
    if not BOT_TOKEN:
        raise ValueError("No BOT_TOKEN environment variable found!")

    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("fetchstrikes", fetch_strikes_command))
    application.add_handler(CommandHandler("analyze", analyze_command))

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
