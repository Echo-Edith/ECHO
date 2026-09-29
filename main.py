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

# Import Flask application from ai_brain.py
from ai_brain import app

# -------------------------------------------------------------
# SESSION PERSISTENCE & SECURITY CONFIGURATION
# -------------------------------------------------------------
# Ensures user sessions survive re-deploys, lockdown toggles, and unbans
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent")
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
# 0. WEBSITE BAN & LOCKDOWN BACKEND SYSTEM INTEGRATION (PYMONGO)
# -------------------------------------------------------------
BOT_API_KEY = os.environ.get("BOT_API_KEY", "").strip()
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", "").strip()
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA").strip()

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
        logger.warning("SYSTEM_LOG_WEBHOOK_URL not configured. Skipping webhook alert.")
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


def verify_turnstile_captcha(token: str, remote_ip: str) -> bool:
    """Verifies Cloudflare Turnstile token with Cloudflare API."""
    if not token:
        return False
    try:
        res = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": remote_ip
            },
            timeout=5
        )
        data = res.json()
        return data.get("success", False)
    except Exception as e:
        logger.error("Turnstile CAPTCHA verification error: %s", e)
        return False


def extract_invite_code(url_or_code: str) -> str:
    """Extracts clean invite code from Discord URL."""
    match = re.search(r'(?:discord\.gg/|discord\.com/invite/)([a-zA-Z0-9-]+)', url_or_code)
    return match.group(1) if match else url_or_code.strip()


def check_user_guild_admin(user_id: str, guild_id: str) -> bool:
    """Verifies whether the user holds Administrator permissions in target guild via Bot API."""
    if not DISCORD_BOT_TOKEN:
        return True

    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        res = requests.get(f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}", headers=headers, timeout=5)
        if res.status_code != 200:
            return False

        member_data = res.json()
        roles_res = requests.get(f"https://discord.com/api/v10/guilds/{guild_id}/roles", headers=headers, timeout=5)
        if roles_res.status_code != 200:
            return False

        guild_roles = {r["id"]: int(r["permissions"]) for r in roles_res.json()}
        user_role_ids = member_data.get("roles", [])

        # Check for ADMINISTRATOR bitflag (0x8) or MANAGE_GUILD (0x20)
        for r_id in user_role_ids:
            perms = guild_roles.get(r_id, 0)
            if (perms & 0x8) == 0x8 or (perms & 0x20) == 0x20:
                return True
        return False
    except Exception as e:
        logger.error("Error checking admin permissions: %s", e)
        return True


def get_user_ban_record(discord_id: str) -> dict | None:
    """Retrieves ban details for a given Discord user ID from MongoDB."""
    if bans_collection is None or not discord_id:
        return None
    try:
        return bans_collection.find_one({"discord_id": str(discord_id)})
    except PyMongoError as e:
        logger.error("Database query error checking ban record for %s: %s", discord_id, e)
        return None


def is_user_banned_db(discord_id: str) -> bool:
    """Checks if a user ID is banned in MongoDB."""
    return get_user_ban_record(discord_id) is not None


def ban_user_in_db(discord_id: str, reason: str = "Automated Security Ban", dev_message: str = "No dev message provided."):
    """Inserts or updates a user ban record in MongoDB."""
    if bans_collection is None:
        return
    try:
        bans_collection.update_one(
            {"discord_id": str(discord_id)},
            {"$set": {
                "discord_id": str(discord_id),
                "reason": reason,
                "dev_message": dev_message,
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

@app.errorhandler(429)
def ratelimit_handler(e):
    """Handles Flask-Limiter IP rate limit triggers and applies 1-hour ban with active timer."""
    client_ip = get_remote_address()
    one_hour_later = time.time() + 3600
    BANNED_IPS[client_ip] = one_hour_later

    logger.warning("🚨 [SECURITY] IP %s exceeded rate limit. Applied 1-hour ban.", client_ip)
    return jsonify({
        "error": "Rate limit exceeded. You have been placed on a 1-hour IP ban.",
        "is_banned": True,
        "ban_until": int(one_hour_later * 1000)
    }), 429


@app.before_request
def enforce_security_and_maintenance():
    session.permanent = True  # Maintain Discord login state across redeploys

    client_ip = get_remote_address()
    now = time.time()

    # IP Ban Enforcement
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

    # Exclude security API routes, health checks, and static assets from lockdown/ban interceptor
    exempt_prefixes = ('/api/security', '/static', '/api/auth/discord')
    exempt_paths = ('/health', '/ping', '/banned')

    if any(request.path.startswith(p) for p in exempt_prefixes) or request.path in exempt_paths:
        return None

    user = session.get('user', {})
    current_user_id = str(user.get('id', '')).strip() if user else ""

    # Alt Account Detection (0-30 days old)
    if current_user_id:
        age_days, _ = calculate_account_age(current_user_id)
        if 0 <= age_days <= 30:
            dev_msg = f"Alt Account Auto-Ban: Discord account age is {age_days} days old (Minimum required: 30 days)."
            ban_user_in_db(current_user_id, reason="Alt Account Auto-Ban", dev_message=dev_msg)
            session.pop('user', None)

            if request.path.startswith('/api/'):
                return jsonify({"error": dev_msg, "is_banned": True}), 403
            return render_glass_banned_page(dev_message=dev_msg, reason="Alt Account Detection")

    # 1. Check if the current user is Banned from website via MongoDB
    if current_user_id and is_user_banned_db(current_user_id):
        ban_record = get_user_ban_record(current_user_id) or {}
        reason = ban_record.get('reason', 'Violating platform rules')
        dev_message = ban_record.get('dev_message', 'No specific developer message provided.')

        session.pop('user', None)  # Clear session for banned users

        if request.path.startswith('/api/'):
            return jsonify({
                "error": f"Access Denied. Banned: {reason}",
                "is_banned": True,
                "dev_message": dev_message
            }), 403

        return render_glass_banned_page(dev_message=dev_message, reason=reason)

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
# SECURITY & API ROUTES FOR BOT & WEBSITE
# -------------------------------------------------------------

@app.route('/banned')
def banned_route():
    """Explicit endpoint to serve the banned Glass UI."""
    user = session.get('user', {})
    user_id = str(user.get('id', '')).strip() if user else ""
    ban_record = get_user_ban_record(user_id) if user_id else None

    dev_msg = ban_record.get('dev_message', 'No specific developer message provided.') if ban_record else "Your access has been suspended."
    reason = ban_record.get('reason', 'Violation of terms.') if ban_record else "Account banned."

    return render_glass_banned_page(dev_message=dev_msg, reason=reason)


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
    dev_message = data.get('dev_message', 'No message provided by developer.')

    if not user_id:
        return jsonify({"error": "Missing user_id parameter"}), 400

    try:
        if action == "ban":
            ban_user_in_db(user_id, reason=reason, dev_message=dev_message)
            logger.info("🚫 Web Ban applied to user %s with dev message: %s", user_id, dev_message)
        elif action == "unban":
            bans_collection.delete_one({"discord_id": user_id})
            logger.info("✅ Web Unban applied to user: %s", user_id)

        total_bans = bans_collection.count_documents({})
        return jsonify({"success": True, "banned_count": total_bans, "dev_message": dev_message}), 200
    except PyMongoError as e:
        logger.error("Database write error during ban operation: %s", e)
        return jsonify({"error": "Database write operation failed"}), 500


@app.route('/api/verify-server', methods=['POST'])
@limiter.limit("5 per minute")
def verify_server_match():
    """
    Verifies CAPTCHA, 24-hour server invite link duration, server ID match,
    and user Administrator permissions via Discord REST API.
    """
    data = request.get_json() or {}
    captcha_token = data.get('captcha_token', '').strip()
    server_id = str(data.get('server_id', '')).strip()
    server_link = str(data.get('server_link', '')).strip()
    client_ip = get_remote_address()

    # 1. CAPTCHA Check
    if not captcha_token or not verify_turnstile_captcha(captcha_token, client_ip):
        return jsonify({"valid": False, "message": "Security CAPTCHA verification failed."}), 400

    if not server_id or not server_link:
        return jsonify({"valid": False, "message": "Missing server_id or server_link parameters."}), 400

    invite_code = extract_invite_code(server_link)
    user_data = session.get('user', {})
    user_id = str(user_data.get('id', '')).strip() if user_data else ""

    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"} if DISCORD_BOT_TOKEN else {}

    try:
        # Fetch invite details with counts/metadata
        res = requests.get(f"https://discord.com/api/v10/invites/{invite_code}?with_counts=true", headers=headers, timeout=5)
        if res.status_code != 200:
            return jsonify({"valid": False, "message": "The provided server invite link is invalid or expired."}), 400

        invite_data = res.json()
        linked_guild_id = str(invite_data.get('guild', {}).get('id', '')).strip()

        # Guild ID Verification
        if linked_guild_id != server_id:
            return jsonify({
                "valid": False,
                "message": f"Server ID Mismatch! Link belongs to server `{linked_guild_id}`, not `{server_id}`."
            }), 400

        # Active for at least 24 Hours (86400 seconds or 0 for infinite)
        max_age = invite_data.get("max_age", 0)
        if max_age != 0 and max_age < 86400:
            return jsonify({
                "valid": False,
                "message": "Invite link duration is too short. Server link must be active for at least 24 hours."
            }), 400

        # Admin Permission Verification
        if user_id and not check_user_guild_admin(user_id, server_id):
            return jsonify({
                "valid": False,
                "message": "Verification failed: You do not have Administrator permissions in this server."
            }), 403

        return jsonify({"valid": True, "message": "Server link, duration, and Admin permissions verified successfully."}), 200
    except requests.RequestException as e:
        logger.error("Discord API error during verification: %s", e)
        return jsonify({"valid": False, "message": "Verification failed due to Discord API timeout."}), 500


@app.route('/api/check-design-limit', methods=['POST'])
def check_design_limit():
    """Enforces 3 designs per 10 minutes limit with 12-hour ban duration and live countdown."""
    user = session.get('user', {})
    user_id = str(user.get('id', '')).strip() if user else None
    client_ip = get_remote_address()

    key = user_id or client_ip
    now = time.time()
    ten_minutes_ago = now - 600

    # Clean old timestamps
    USER_GENERATION_TIMESTAMPS[key] = [t for t in USER_GENERATION_TIMESTAMPS[key] if t > ten_minutes_ago]
    USER_GENERATION_TIMESTAMPS[key].append(now)

    # Check threshold (Exceeding 3 requests = 12-hour ban)
    if len(USER_GENERATION_TIMESTAMPS[key]) > 3:
        twelve_hours_later = time.time() + (12 * 3600)
        BANNED_IPS[client_ip] = twelve_hours_later

        dev_msg = "Automated Security Ban: Exceeded design creation limit (more than 3 designs in 10 minutes)."
        reason = "Automated Rate Limit Ban (12 Hours)"

        if user_id:
            ban_user_in_db(user_id, reason=reason, dev_message=dev_msg)

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


@app.route('/api/log-entry', methods=['POST'])
def log_detailed_user_entry():
    """Logs detailed Discord profile information upon user website login."""
    user = session.get('user', {})
    if not user or not user.get('id'):
        return jsonify({"status": "ignored"}), 200

    user_id = str(user.get('id'))
    username = user.get('username', 'Unknown')
    global_name = user.get('global_name') or username
    avatar = user.get('avatar')
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"

    age_days, created_date = calculate_account_age(user_id)

    send_system_log(
        title="📥 Detailed User Entry Logged",
        description=f"User **{global_name}** (`@{username}`) accessed the Echo Studio Portal.",
        color=0x3B82F6,
        fields=[
            {"name": "User Identity", "value": f"<@{user_id}> (`{user_id}`)", "inline": True},
            {"name": "Global / Username", "value": f"`{global_name}` (`@{username}`)", "inline": True},
            {"name": "Account Age", "value": f"`{age_days} days old`", "inline": True},
            {"name": "Creation Date", "value": f"`{created_date}`", "inline": False},
            {"name": "User-Agent", "value": f"`{request.headers.get('User-Agent', 'Unknown')[:100]}`", "inline": False},
            {"name": "IP Address", "value": f"`{request.remote_addr}`", "inline": True},
            {"name": "Avatar URL", "value": f"[View Avatar]({avatar_url})", "inline": True}
        ]
    )
    return jsonify({"status": "logged"}), 200


# -------------------------------------------------------------
# 1. DISCORD BOT INITIALIZATION
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
# 2. BOT RUNNER & KEEP ALIVE THREAD
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
# 3. APPLICATION ENTRY POINT
# -------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info("🚀 Starting Web Server on port %d...", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)
