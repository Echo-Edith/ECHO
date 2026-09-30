import os
import asyncio
import threading
import logging
import atexit
from datetime import timedelta
from flask import request, jsonify, render_template, session, make_response, redirect
import discord
from discord.ext import commands

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Import Flask application and helpers from keep_alive.py
from keep_alive import (
    app,
    save_blueprint_data,
    get_blueprint_data,
    verify_turnstile_captcha,
    extract_invite_code,
    check_user_guild_admin
)

# -------------------------------------------------------------
# SESSION PERSISTENCE & CONFIGURATION
# -------------------------------------------------------------
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent-2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get("FLASK_ENV") == "production"

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

# Maintain persistent session lifecycle for active Discord logins
@app.before_request
def make_session_permanent():
    session.permanent = True

# -------------------------------------------------------------
# DISCORD BOT INITIALIZATION & COG LOADING
# -------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True


class OrcaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Loads extension cogs and syncs application slash commands."""
        try:
            await self.load_extension("cogs.orca")
            logger.info("✅ Cog 'cogs.orca' loaded successfully.")
        except Exception as e:
            logger.error("❌ Failed to load cog 'cogs.orca': %s", e)

        try:
            synced = await self.tree.sync()
            logger.info("🔄 Successfully synced %d slash command(s).", len(synced))
        except Exception as e:
            logger.error("❌ Failed to sync slash commands: %s", e)

    async def on_ready(self):
        if self.user:
            logger.info("🟢 Discord Bot logged in as: %s (ID: %s)", self.user.name, self.user.id)


bot = OrcaBot()

# -------------------------------------------------------------
# BOT RUNNER & KEEP ALIVE BACKGROUND THREAD
# -------------------------------------------------------------
_bot_thread = None
_bot_thread_lock = threading.Lock()


def start_discord_bot():
    if not DISCORD_BOT_TOKEN:
        logger.warning("⚠️ WARNING: 'DISCORD_BOT_TOKEN' environment variable is missing. Bot launch skipped.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        logger.info("⚡ Starting Discord Bot connection...")
        bot.run(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error("❌ Failed to run Discord Bot: %s", e)


def keep_alive():
    """Starts the Discord bot in a background thread for WSGI/Gunicorn integration."""
    global _bot_thread
    with _bot_thread_lock:
        if _bot_thread is None or not _bot_thread.is_alive():
            _bot_thread = threading.Thread(target=start_discord_bot, daemon=True)
            _bot_thread.start()
            logger.info("Keep-alive thread started for Discord bot.")


# Auto-start bot thread when module is loaded under WSGI / app servers
keep_alive()

# -------------------------------------------------------------
# APPLICATION ENTRY POINT
# -------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info("🚀 Starting Web Server on port %d...", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)
