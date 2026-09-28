import os
import asyncio
import threading
import logging
import discord
from discord.ext import commands

# Import Flask application from ai_brain.py
from ai_brain import app

logging.basicConfig(level=logging.INFO)

# -------------------------------------------------------------
# 1. DISCORD BOT INITIALIZATION
# -------------------------------------------------------------
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

# Enable required privileged intents for spam tracking & moderation
intents = discord.Intents.default()
intents.message_content = True  # Required for tracking message spam rate
intents.members = True          # Required for member timeouts and bans
intents.guilds = True


class OrcaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Loads extension cogs and syncs application slash commands."""
        try:
            # Dynamically load cogs/orca.py
            await self.load_extension("cogs.orca")
            logging.info("✅ Cog 'cogs.orca' loaded successfully.")
        except Exception as e:
            logging.error(f"❌ Failed to load cog 'cogs.orca': {e}")

        # Sync slash commands with Discord API
        try:
            synced = await self.tree.sync()
            logging.info(f"🔄 Successfully synced {len(synced)} slash command(s).")
        except Exception as e:
            logging.error(f"❌ Failed to sync slash commands: {e}")

    async def on_ready(self):
        logging.info(f"🟢 Discord Bot logged in as: {self.user.name} (ID: {self.user.id})")


bot = OrcaBot()

# -------------------------------------------------------------
# 2. BOT RUNNER THREAD
# -------------------------------------------------------------
def start_discord_bot():
    if not BOT_TOKEN:
        logging.warning("⚠️ WARNING: 'DISCORD_BOT_TOKEN' environment variable is missing. Bot launch skipped.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        logging.info("⚡ Starting Discord Bot connection...")
        bot.run(BOT_TOKEN)
    except Exception as e:
        logging.error(f"❌ Failed to run Discord Bot: {e}")


# -------------------------------------------------------------
# 3. APPLICATION ENTRY POINT
# -------------------------------------------------------------
if __name__ == '__main__':
    # Start Discord Bot in a background daemon thread
    bot_thread = threading.Thread(target=start_discord_bot, daemon=True)
    bot_thread.start()

    # Start Flask Web Server on the target port
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"🚀 Starting Web Server on port {port}...")
    app.run(host="0.0.0.0", port=port)
