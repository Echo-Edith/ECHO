import os
import asyncio
import logging
import discord
from discord.ext import commands

# 1. Import Flask app from ai_brain.py
from ai_brain import app

# 2. Import keep_alive server startup if needed
from keep_alive import keep_alive

logging.basicConfig(level=logging.INFO)

# Initialize Discord Bot Intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logging.info(f"Logged in as Discord Bot: {bot.user} (ID: {bot.user.id})")

async def load_cogs():
    """Dynamically loads cogs like orca.py"""
    cogs_dir = "./cogs"
    if os.path.exists(cogs_dir):
        for file in os.listdir(cogs_dir):
            if file.endswith(".py") and not file.startswith("__"):
                extension = f"cogs.{file[:-3]}"
                try:
                    await bot.load_extension(extension)
                    logging.info(f"Loaded extension: {extension}")
                except Exception as e:
                    logging.error(f"Failed to load extension {extension}: {e}")

async def main():
    # Start web server background thread
    keep_alive()

    # Load Discord Cogs (orca.py)
    async with bot:
        await load_cogs()
        token = os.environ.get("DISCORD_TOKEN")
        if token:
            await bot.start(token)
        else:
            logging.warning("DISCORD_TOKEN environment variable is missing. Bot started in web-only mode.")

if __name__ == '__main__':
    # If running with Gunicorn on Render, expose the Flask app from ai_brain
    port = int(os.environ.get("PORT", 10000))
    
    # Run Discord Bot + KeepAlive
    token = os.environ.get("DISCORD_TOKEN")
    if token:
        asyncio.run(main())
    else:
        # Fallback to pure web server if token isn't passed directly
        app.run(host="0.0.0.0", port=port)
