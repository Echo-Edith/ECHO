import os
import asyncio
import threading
import logging
import atexit
from flask import request, jsonify, render_template, session
import discord
from discord.ext import commands
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Import Flask application from ai_brain.py
from ai_brain import app

# -------------------------------------------------------------
# 0. WEBSITE BAN & LOCKDOWN BACKEND SYSTEM INTEGRATION (PYMONGO)
# -------------------------------------------------------------
BOT_API_KEY = os.environ.get("BOT_API_KEY", "").strip()
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")

# Primary Owner ID exempted from website maintenance restrictions
PRIMARY_OWNER_ID = "1219266886143967245"

mongo_client = None
db = None
bans_collection = None

if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = mongo_client["bot_database"]
        bans_collection = db["website_bans"]
        logger.info("MongoDB client connected successfully.")
    except Exception as e:
        logger.error("Failed to initialize MongoDB client: %s", e)

IS_LOCKDOWN = False


def is_user_banned_db(discord_id: str) -> bool:
    """Checks if a user ID is banned in MongoDB with fallback safety."""
    if bans_collection is None or not discord_id:
        return False
    try:
        user = bans_collection.find_one({"discord_id": str(discord_id)})
        return user is not None
    except PyMongoError as e:
        logger.error("Database query error checking ban status for %s: %s", discord_id, e)
        return False


# --- Security API Routes for Bot Communication ---

@app.route('/api/security/ban', methods=['POST'])
def api_security_ban():
    """Endpoint called by /ban command in cogs/orca.py"""
    auth_header = request.headers.get('X-Bot-Auth', '').strip()
    if BOT_API_KEY and auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    if bans_collection is None:
        return jsonify({"error": "Database connection not initialized"}), 500

    data = request.get_json() or {}
    user_id = str(data.get('user_id', '')).strip()
    action = data.get('action', 'ban').lower()
    reason = data.get('reason', 'No reason provided')

    if not user_id:
        return jsonify({"error": "Missing user_id parameter"}), 400

    try:
        if action == "ban":
            bans_collection.update_one(
                {"discord_id": user_id},
                {"$set": {"discord_id": user_id, "reason": reason}},
                upsert=True
            )
            logger.info("🚫 Web Ban applied to user: %s", user_id)
        elif action == "unban":
            bans_collection.delete_one({"discord_id": user_id})
            logger.info("✅ Web Unban applied to user: %s", user_id)

        total_bans = bans_collection.count_documents({})
        return jsonify({"success": True, "banned_count": total_bans}), 200
    except PyMongoError as e:
        logger.error("Database write error during ban operation: %s", e)
        return jsonify({"error": "Database write operation failed"}), 500


@app.route('/api/security/bans', methods=['GET'])
@app.route('/api/bans', methods=['GET'])
def api_security_bans():
    """Endpoint called by /ban-list command in cogs/orca.py"""
    auth_header = request.headers.get('X-Bot-Auth', '').strip()
    if BOT_API_KEY and auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    if bans_collection is None:
        return jsonify({"banned_users": []}), 200

    try:
        banned_users = list(bans_collection.find({}, {"_id": 0}))
        return jsonify({"banned_users": banned_users}), 200
    except PyMongoError as e:
        logger.error("Database query error retrieving bans: %s", e)
        return jsonify({"error": "Failed to fetch bans from database"}), 500


@app.route('/api/security/lockdown', methods=['POST'])
def api_security_lockdown():
    """Endpoint called by /lockdown command in cogs/orca.py"""
    global IS_LOCKDOWN
    auth_header = request.headers.get('X-Bot-Auth', '').strip()
    if BOT_API_KEY and auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    IS_LOCKDOWN = bool(data.get('enable', False))
    logger.info("🔒 Maintenance mode state updated to: %s", IS_LOCKDOWN)
    return jsonify({"success": True, "is_lockdown": IS_LOCKDOWN}), 200


# --- Global Request Interceptor for Ban & Lockdown Enforcement ---

@app.before_request
def enforce_security_and_maintenance():
    # Exclude internal API routes, health checks, and static assets from lockdown/ban checks
    exempt_prefixes = ('/api/security', '/static', '/api/auth/discord')
    exempt_paths = ('/health', '/ping')

    if any(request.path.startswith(p) for p in exempt_prefixes) or request.path in exempt_paths:
        return None

    user = session.get('user', {})
    current_user_id = str(user.get('id', '')).strip() if user else ""

    # 1. Check if the current user is Banned from website via MongoDB
    if current_user_id and is_user_banned_db(current_user_id):
        reason = 'Violating platform rules'
        if bans_collection is not None:
            try:
                ban_entry = bans_collection.find_one({"discord_id": current_user_id})
                if ban_entry and ban_entry.get('reason'):
                    reason = ban_entry['reason']
            except PyMongoError as e:
                logger.error("Error retrieving ban reason for %s: %s", current_user_id, e)

        session.pop('user', None)  # Clear session for banned users
        if request.path.startswith('/api/'):
            return jsonify({"error": f"Access Denied. Banned: {reason}", "is_banned": True}), 403

        return render_template(
            'index.html',
            is_banned=True,
            ban_reason=reason,
            is_lockdown=False,
            user=None
        ), 403

    # 2. Check if Website Lockdown is Active (Exempt Primary Owner)
    if IS_LOCKDOWN:
        if not current_user_id or current_user_id != PRIMARY_OWNER_ID:
            if request.path.startswith('/api/'):
                return jsonify({"error": "System Under Maintenance. Dashboard is locked.", "is_lockdown": True}), 530

            return render_template(
                'index.html',
                is_lockdown=True,
                is_banned=False,
                user=user
            ), 530


# -------------------------------------------------------------
# 1. DISCORD BOT INITIALIZATION
# -------------------------------------------------------------
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

# Enable required privileged intents for tracking & moderation
intents = discord.Intents.default()
intents.message_content = True  # Required for tracking message rate
intents.members = True          # Required for member management
intents.guilds = True


class OrcaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Loads extension cogs and syncs application slash commands."""
        try:
            # Dynamically load cogs/orca.py
            await self.load_extension("cogs.orca")
            logger.info("✅ Cog 'cogs.orca' loaded successfully.")
        except Exception as e:
            logger.error("❌ Failed to load cog 'cogs.orca': %s", e)

        # Sync slash commands with Discord API
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
# 2. BOT RUNNER & KEEP ALIVE THREAD
# -------------------------------------------------------------
_bot_thread = None
_bot_thread_lock = threading.Lock()


def start_discord_bot():
    if not BOT_TOKEN:
        logger.warning("⚠️ WARNING: 'DISCORD_BOT_TOKEN' environment variable is missing. Bot launch skipped.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        logger.info("⚡ Starting Discord Bot connection...")
        bot.run(BOT_TOKEN)
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


def cleanup_resources():
    """Gracefully closes open client connections upon process exit."""
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB client connection closed cleanly.")


atexit.register(cleanup_resources)

# Auto-start bot thread when module is loaded under WSGI / app servers
keep_alive()

# -------------------------------------------------------------
# 3. APPLICATION ENTRY POINT
# -------------------------------------------------------------
if __name__ == '__main__':
    # Start Flask Web Server on target port
    port = int(os.environ.get("PORT", 10000))
    logger.info("🚀 Starting Web Server on port %d...", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)
