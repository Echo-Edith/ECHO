import os
import sys
import asyncio
import threading
import logging
from datetime import timedelta
from flask import request, jsonify, session
import discord
from discord.ext import commands

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

# Import Flask app and persistence helpers from keep_alive
try:
    from keep_alive import (
        app,
        save_blueprint_data,
        get_blueprint_data,
        verify_recaptcha
    )
except Exception as e:
    logger.critical(f"Failed to import from keep_alive: {e}", exc_info=True)
    sys.exit(1)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "echo-studio-secret-2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()


@app.before_request
def make_session_permanent():
    session.permanent = True


@app.route('/api/blueprint/<guild_id>', methods=['GET'])
def fetch_blueprint(guild_id):
    data = get_blueprint_data(guild_id)
    if data:
        return jsonify(data), 200
    return jsonify({"error": "Blueprint not found"}), 404


@app.route('/api/blueprint/save', methods=['POST'])
def save_blueprint():
    payload = request.get_json() or {}
    guild_id = payload.get("target_guild_id")
    if not guild_id:
        return jsonify({"error": "Missing target_guild_id"}), 400

    save_blueprint_data(guild_id, payload)
    return jsonify({"status": "success", "guild_id": guild_id}), 200


# Initialize Discord Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True


class OrcaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
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
            logger.info("🟢 Discord Bot connected as: %s (ID: %s)", self.user.name, self.user.id)


bot = OrcaBot()

_bot_thread = None
_bot_thread_lock = threading.Lock()


def start_discord_bot():
    if not DISCORD_BOT_TOKEN:
        logger.warning("⚠️ 'DISCORD_BOT_TOKEN' is missing. Discord bot will not start.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        logger.info("⚡ Connecting to Discord API...")
        bot.run(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.error("❌ Discord Bot crashed: %s", e, exc_info=True)


def keep_alive():
    global _bot_thread
    with _bot_thread_lock:
        if _bot_thread is None or not _bot_thread.is_alive():
            _bot_thread = threading.Thread(target=start_discord_bot, daemon=True)
            _bot_thread.start()
            logger.info("Keep-alive background thread started.")


# Launch the Discord bot background thread upon module load
keep_alive()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info("🚀 Starting Flask web server on port %d...", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)
