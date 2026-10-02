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

# Import Flask application and persistence helpers
from keep_alive import (
    app,
    save_blueprint_data,
    get_blueprint_data,
    verify_recaptcha
)

# -------------------------------------------------------------
# SESSION PERSISTENCE & CONFIGURATION
# -------------------------------------------------------------
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent-2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

# Maintain persistent session lifecycle for active web logins
@app.before_request
def make_session_permanent():
    session.permanent = True

# -------------------------------------------------------------
# API BLUEPRINT PERSISTENCE ROUTES
# -------------------------------------------------------------
@app.route('/api/blueprint/<guild_id>', methods=['GET'])
def fetch_blueprint(guild_id):
    """Retrieve stored blueprint by guild ID."""
    data = get_blueprint_data(guild_id)
    if data:
        return jsonify(data), 200
    return jsonify({"error": "Blueprint not found"}), 404


@app.route('/api/blueprint/save', methods=['POST'])
def save_blueprint():
    """Save an incoming web blueprint."""
    payload = request.get_json() or {}
    guild_id = payload.get("target_guild_id")
    if not guild_id:
        return jsonify({"error": "Missing target_guild_id"}), 400

    save_blueprint_data(guild_id, payload)
    return jsonify({"status": "success", "guild_id": guild_id}), 200

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
            logger.info("✅ Extension 'cogs.orca' loaded successfully.")
        except Exception as e:
            logger.error("❌ Failed to load extension 'cogs.orca': %s", e)

        try:
            synced = await self.tree.sync()
            logger.info("🔄 Successfully synced %d slash command(s).", len(synced))
        except Exception as e:
            logger.error("❌ Failed to sync slash commands: %s", e)

    async def on_ready(self):
        if self.user:
            logger.info("🟢 Discord Bot active: %s (ID: %s)", self.user.name, self.user.id)


bot = OrcaBot()

# -------------------------------------------------------------
# BOT RUNNER & KEEP ALIVE BACKGROUND THREAD
# -------------------------------------------------------------
_bot_thread = None
_bot_thread_lock = threading.Lock()


def start_discord_bot():
    """Runs the Discord bot inside an isolated thread event loop."""
    if not DISCORD_BOT_TOKEN:
        logger.warning("⚠️ WARNING: 'DISCORD_BOT_TOKEN' environment variable is missing. Bot launch skipped.")
        return

    # Create and set loop specifically for this daemon thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        logger.info("⚡ Starting Discord Bot connection...")
        bot.run(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error("❌ Error encountered running Discord Bot: %s", e)


def keep_alive():
    """Starts the Discord bot in a background thread for WSGI/Gunicorn deployment."""
    global _bot_thread
    with _bot_thread_lock:
        if _bot_thread is None or not _bot_thread.is_alive():
            _bot_thread = threading.Thread(target=start_discord_bot, daemon=True)
            _bot_thread.start()
            logger.info("Keep-alive daemon thread successfully launched.")


# Auto-start bot thread when module is imported or executed
keep_alive()

# -------------------------------------------------------------
# APPLICATION ENTRY POINT
# -------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info("🚀 Starting Web Server on port %d...", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)
