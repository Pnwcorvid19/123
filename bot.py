import os
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Define all the Kalshi contract strike prices you want to monitor
KALSHI_STRIKES = [3000.0, 3100.0, 3200.0, 3300.0]

# Set how often to check in seconds (e.g., 30 seconds for faster updates)
CHECK_INTERVAL_SECONDS = 30 

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    strikes_formatted = ", ".join([f"${s}" for s in KALSHI_STRIKES])
    await update.message.reply_text(
        f"🤖 **Kalshi Multi-Strike Alert Bot Connected!**\n\n"
        f"🎯 Active Targets: {strikes_formatted}\n"
        f"⏱ Check Frequency: Every {CHECK_INTERVAL_SECONDS} seconds\n\n"
        f"Type `/setalert` to start live tracking."
    )

async def get_eth_price():
    """Fetches the live Ethereum price from CoinGecko API"""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
        response = requests.get(url, timeout=10)
        data = response.json()
        return float(data["ethereum"]["usd"])
    except Exception as e:
        logger.error(f"Error fetching ETH price: {e}")
        return None

async def check_all_strikes(context: ContextTypes.DEFAULT_TYPE):
    """Checks live ETH spot price against all active Kalshi strike targets"""
    chat_id = context.job.chat_id
    current_price = await get_eth_price()
    
    if current_price is None:
        return

    logger.info(f"Checked Spot ETH: ${current_price}")

    # Track which alerts have already triggered to avoid spamming
    if "triggered_alerts" not in context.bot_data:
        context.bot_data["triggered_alerts"] = set()

    for strike in KALSHI_STRIKES:
        # Check if price dropped below or reached a lower strike
        if current_price <= strike and f"UNDER_{strike}" not in context.bot_data["triggered_alerts"]:
            context.bot_data["triggered_alerts"].add(f"UNDER_{strike}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 **KALSHI STRIKE CROSSED BELOW!** 🚨\n\n"
                     f"Current ETH Spot: **${current_price}**\n"
                     f"Strike Price: **${strike}**\n\n"
                     f"⚡ Check Kalshi contract immediately!"
            )
        
        # Check if price broke above an upper strike
        elif current_price >= strike and f"ABOVE_{strike}" not in context.bot_data["triggered_alerts"]:
            context.bot_data["triggered_alerts"].add(f"ABOVE_{strike}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚀 **KALSHI STRIKE CROSSED ABOVE!** 🚀\n\n"
                     f"Current ETH Spot: **${current_price}**\n"
                     f"Strike Price: **${strike}**\n\n"
                     f"⚡ Check Kalshi contract immediately!"
            )

async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the multi-strike background tracking loop"""
    chat_id = update.effective_chat.id
    
    # Remove any existing scheduled jobs
    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in current_jobs:
        job.schedule_removal()

    # Schedule the recurring check interval
    context.job_queue.run_repeating(
        check_all_strikes, 
        interval=CHECK_INTERVAL_SECONDS, 
        first=0, 
        chat_id=chat_id,
        name=str(chat_id)
    )
    
    strikes_formatted = ", ".join([f"${s}" for s in KALSHI_STRIKES])
    await update.message.reply_text(
        f"✅ **Multi-Strike Alerts Enabled!**\n"
        f"Tracking: {strikes_formatted}\n"
        f"Interval: Every {CHECK_INTERVAL_SECONDS} seconds."
    )

def main():
    if not BOT_TOKEN:
        raise ValueError("No BOT_TOKEN environment variable found!")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setalert", set_alert))

    logger.info("Bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
