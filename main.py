import os
import asyncio
import threading
import logging
import atexit
import time
import requests
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from flask import request, jsonify, render_template, session, make_response, redirect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
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

# Import Flask application and helpers from keep_alive.py / ai_brain.py
from keep_alive import (
    app,
    is_user_banned_db,
    check_lockdown_status_db,
    save_blueprint_data,
    get_blueprint_data,
    verify_turnstile_captcha,
    extract_invite_code,
    check_user_guild_admin
)

# -------------------------------------------------------------
# SESSION PERSISTENCE & SECURITY CONFIGURATION
# -------------------------------------------------------------
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent-2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get("FLASK_ENV") == "production"

# Initialize IP Rate Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# -------------------------------------------------------------
# WEBSITE BAN & LOCKDOWN BACKEND SYSTEM INTEGRATION
# -------------------------------------------------------------
BOT_API_KEY = os.environ.get("BOT_API_KEY", "").strip()
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", os.environ.get("WEBHOOK_LOG_URL", "")).strip()
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
PRIMARY_OWNER_ID = os.environ.get("PRIMARY_OWNER_ID", "1219266886143967245").strip()

mongo_client = None
db = None
bans_collection = None
telemetry_collection = None

if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = mongo_client["bot_database"]
        bans_collection = db["website_bans"]
        telemetry_collection = db["telemetry"]
        logger.info("MongoDB client initialized successfully in main.py.")
    except Exception as e:
        logger.error("Failed to initialize MongoDB client in main.py: %s", e)

# Trackers
USER_GENERATION_TIMESTAMPS = defaultdict(list)
BANNED_IPS = {}  # { ip: expiration_timestamp }


def calculate_account_age(discord_id: str) -> tuple[int, str]:
    """Calculates Discord account age in days and creation date from snowflake ID."""
    try:
        snowflake = int(discord_id)
        timestamp = ((snowflake >> 22) + 1420070400000) / 1000.0
        created_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created_at).days
        return age_days, created_at.strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return 0, "Unknown"


def send_system_log(title: str, description: str, fields: list = None, content: str = None, color: int = 0xFF0000):
    """Sends webhook alerts to SYSTEM_LOG_WEBHOOK_URL."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        return

    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields or [],
            "footer": {"text": "Echo Studio Security System"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }
    if content:
        payload["content"] = content

    try:
        requests.post(SYSTEM_LOG_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        logger.error("Failed to send system log webhook: %s", e)


def ban_user_in_db(discord_id: str, reason: str = "Automated Security Ban", dev_message: str = "No dev message provided.", duration_seconds: int = 0):
    """Inserts or updates a user ban record in MongoDB."""
    if bans_collection is None or str(discord_id).strip() == PRIMARY_OWNER_ID:
        return
    try:
        expires_at = time.time() + duration_seconds if duration_seconds > 0 else 0
        bans_collection.update_one(
            {"discord_id": str(discord_id)},
            {"$set": {
                "discord_id": str(discord_id),
                "reason": reason,
                "dev_message": dev_message,
                "expires_at": expires_at,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )
        logger.info("🚫 Database Ban recorded for user: %s", discord_id)
    except PyMongoError as e:
        logger.error("Failed to write ban record to database for %s: %s", discord_id, e)


def render_glass_banned_page(dev_message: str = "Access to this website has been suspended by an administrator.", reason: str = "Violation of platform terms."):
    """Renders a clean glassmorphism UI page for banned users with red text header."""
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Access Denied - Banned</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
            body {{
                background: #08080c;
                background-image: radial-gradient(circle at 50% 30%, #1a080c 0%, #08080c 70%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #ffffff;
                padding: 20px;
            }}
            .glass-card {{
                background: rgba(20, 20, 28, 0.6);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border: 1px solid rgba(255, 59, 48, 0.2);
                border-radius: 24px;
                padding: 48px;
                max-width: 520px;
                width: 100%;
                text-align: center;
                box-shadow: 0 20px 50px rgba(0, 0, 0, 0.8), 0 0 30px rgba(255, 59, 48, 0.1);
            }}
            .badge {{
                display: inline-block;
                background: rgba(255, 59, 48, 0.15);
                border: 1px solid rgba(255, 59, 48, 0.3);
                color: #ff3b30;
                padding: 6px 16px;
                border-radius: 50px;
                font-size: 0.75rem;
                font-weight: 700;
                letter-spacing: 1.5px;
                text-transform: uppercase;
                margin-bottom: 20px;
            }}
            h1 {{
                color: #ff3b30;
                font-size: 3rem;
                font-weight: 900;
                letter-spacing: 3px;
                margin-bottom: 12px;
                text-shadow: 0 0 20px rgba(255, 59, 48, 0.4);
            }}
            .subtext {{
                color: #8e8e93;
                font-size: 0.95rem;
                margin-bottom: 28px;
            }}
            .dev-box {{
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-left: 4px solid #ff3b30;
                border-radius: 12px;
                padding: 20px;
                text-align: left;
                margin-top: 10px;
            }}
            .dev-box-title {{
                color: #ff6b63;
                font-size: 0.75rem;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 1px;
                margin-bottom: 8px;
            }}
            .dev-box-content {{
                color: #e5e5ea;
                font-size: 0.95rem;
                line-height: 1.5;
                word-break: break-word;
            }}
            .reason-text {{
                color: #a1a1aa;
                font-size: 0.85rem;
                margin-top: 8px;
            }}
        </style>
    </head>
    <body>
        <div class="glass-card">
            <div class="badge">Security Alert</div>
            <h1>BANNED</h1>
            <p class="subtext">Your account has been restricted from accessing Echo Studio Portal.</p>
            <div class="dev-box">
                <div class="dev-box-title">Message from Developer</div>
                <div class="dev-box-content">{dev_message}</div>
                <div class="reason-text"><strong>Reason:</strong> {reason}</div>
            </div>
        </div>
    </body>
    </html>
    """
    return make_response(html, 403)


# -------------------------------------------------------------
# ERROR HANDLERS & MIDDLEWARE
# -------------------------------------------------------------

@app.before_request
def enforce_security_and_maintenance_main():
    session.permanent = True  # Maintain Discord login state across redeploys

    exempt_prefixes = ('/api/security', '/static', '/api/auth/discord')
    exempt_paths = ('/health', '/ping', '/banned', '/maintenance')

    if any(request.path.startswith(p) for p in exempt_prefixes) or request.path in exempt_paths:
        return None

    user = session.get('user', {})
    current_user_id = str(user.get('id', '')).strip() if user else ""

    # PRIMARY OWNER IMMUNITY: Skip all restrictions for the owner
    if current_user_id and current_user_id == PRIMARY_OWNER_ID:
        return None

    client_ip = get_remote_address()
    now = time.time()

    # 1. IP Ban Enforcement
    if client_ip in BANNED_IPS:
        ban_expiry = BANNED_IPS[client_ip]
        if now < ban_expiry:
            if request.path.startswith('/api/'):
                return jsonify({
                    "error": "IP Ban Active. Access restricted.",
                    "is_banned": True,
                    "ban_until": int(ban_expiry * 1000)
                }), 403
            return redirect('/banned')
        else:
            del BANNED_IPS[client_ip]

    # 2. Alt Account Auto-Ban Check (0-30 days old)
    if current_user_id:
        age_days, _ = calculate_account_age(current_user_id)
        if 0 <= age_days <= 30:
            dev_msg = f"Alt Account Auto-Ban: Discord account age is {age_days} days old (Minimum required: 30 days)."
            ban_user_in_db(current_user_id, reason="Alt Account Auto-Ban", dev_message=dev_msg)
            session.pop('user', None)

            if request.path.startswith('/api/'):
                return jsonify({"error": dev_msg, "is_banned": True}), 403
            return render_glass_banned_page(dev_message=dev_msg, reason="Alt Account Detection")

    # 3. User Ban Check via MongoDB
    if current_user_id:
        is_banned, reason, dev_msg = is_user_banned_db(current_user_id)
        if is_banned:
            session.pop('user', None)
            if request.path.startswith('/api/'):
                return jsonify({
                    "error": f"Access Denied. Banned: {reason}",
                    "is_banned": True,
                    "dev_message": dev_msg
                }), 403
            return render_glass_banned_page(dev_message=dev_msg, reason=reason)

    # 4. Lockdown Maintenance Check
    if check_lockdown_status_db():
        if request.path.startswith('/api/'):
            return jsonify({"error": "System Under Maintenance. Dashboard is locked.", "is_lockdown": True}), 530

        return render_template('index.html'), 530


# -------------------------------------------------------------
# ADDITIONAL FRONTEND API ROUTES
# -------------------------------------------------------------

@app.route('/banned')
def banned_route():
    """Explicit endpoint to serve the banned Glass UI."""
    user = session.get('user', {})
    user_id = str(user.get('id', '')).strip() if user else ""
    
    is_banned, reason, dev_msg = False, "Account suspended.", "Access to this website has been restricted."
    if user_id:
        is_banned, reason, dev_msg = is_user_banned_db(user_id)

    return render_glass_banned_page(dev_message=dev_msg, reason=reason)


@app.route('/api/check-design-limit', methods=['POST'])
def check_design_limit():
    """Enforces 3 designs per 10 minutes limit with 12-hour ban duration and live countdown."""
    user = session.get('user', {})
    user_id = str(user.get('id', '')).strip() if user else None

    # Owner immunity check
    if user_id and user_id == PRIMARY_OWNER_ID:
        return jsonify({"allowed": True, "remaining": 999}), 200

    client_ip = get_remote_address()

    key = user_id or client_ip
    now = time.time()
    ten_minutes_ago = now - 600

    USER_GENERATION_TIMESTAMPS[key] = [t for t in USER_GENERATION_TIMESTAMPS[key] if t > ten_minutes_ago]
    USER_GENERATION_TIMESTAMPS[key].append(now)

    if len(USER_GENERATION_TIMESTAMPS[key]) > 3:
        twelve_hours_later = time.time() + (12 * 3600)
        BANNED_IPS[client_ip] = twelve_hours_later

        dev_msg = "Automated Security Ban: Exceeded design creation limit (more than 3 designs in 10 minutes)."
        reason = "Automated Rate Limit Ban (12 Hours)"

        if user_id:
            ban_user_in_db(user_id, reason=reason, dev_message=dev_msg, duration_seconds=12*3600)

        send_system_log(
            title="🚨 AUTOMATED 12-HOUR BAN TRIGGERED",
            description=f"User <@{user_id}> (`{user_id}`) exceeded the design limit (3 actions / 10 min) and was banned for 12 hours.",
            content=f"<@{PRIMARY_OWNER_ID}> 🚨 **Security Alert: User Auto-Banned**",
            fields=[
                {"name": "User ID / IP", "value": f"`{key}`", "inline": True},
                {"name": "Trigger Reason", "value": "Exceeded 3 layout generations per 10 minutes", "inline": False}
            ]
        )

        return jsonify({
            "allowed": False,
            "is_banned": True,
            "dev_message": dev_msg,
            "ban_until": int(twelve_hours_later * 1000),
            "error": "You exceeded the 3 layout design limit per 10 minutes and have been banned for 12 hours."
        }), 403

    return jsonify({"allowed": True, "remaining": 3 - len(USER_GENERATION_TIMESTAMPS[key])}), 200


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


def cleanup_resources():
    """Gracefully closes open client connections upon process exit."""
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB client connection closed cleanly.")


atexit.register(cleanup_resources)

# Auto-start bot thread when module is loaded under WSGI / app servers
keep_alive()

# -------------------------------------------------------------
# APPLICATION ENTRY POINT
# -------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info("🚀 Starting Web Server on port %d...", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)
