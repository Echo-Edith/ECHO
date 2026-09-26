import os
import threading
import asyncio
import discord
from discord.ext import commands

# Import your Flask app from ai_brain.py
from ai_brain import app

# -------------------------------------------------------------
# 1. DISCORD BOT SETUP
# -------------------------------------------------------------
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

intents = discord.Intents.default()
intents.message_content = True  # Make sure this is toggled ON in Discord Developer Portal!

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Discord Bot is ONLINE as: {bot.user.name} (ID: {bot.user.id})")

def start_discord_bot():
    if not BOT_TOKEN:
        print("⚠️ WARNING: 'DISCORD_BOT_TOKEN' environment variable is not set. Bot skipped.")
        return
    
    # Create a new event loop for discord.py in this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        print("⚡ Starting Discord Bot connection...")
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"❌ Failed to start Discord Bot: {e}")

# -------------------------------------------------------------
# 2. RUN BOT THREAD & FLASK WEB SERVER
# -------------------------------------------------------------
if __name__ == '__main__':
    # Start the bot on a separate daemon thread so it doesn't block Flask
    bot_thread = threading.Thread(target=start_discord_bot, daemon=True)
    bot_thread.start()

    # Start Flask Web Server
    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Web Dashboard on port {port}...")
    app.run(host="0.0.0.0", port=port)
