import os
import json
import io
import time
import logging
import re
import requests
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from google import genai
from google.genai import types
from pymongo import MongoClient

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("ai_brain")

app = Flask(__name__)

# Secret key and persistent session configuration
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent-2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get("FLASK_ENV") == "production"

# Initialize IP Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Discord OAuth2 & API Configurations
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# Cloudflare Turnstile Configuration
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA").strip()

# Internal Bot API Authorization Key (Syncs with cogs/orca.py)
BOT_API_KEY = os.environ.get("BOT_API_KEY", "super-secret-bot-key").strip()

# Primary Bot Owner ID
PRIMARY_OWNER_ID = os.environ.get("PRIMARY_OWNER_ID", "1219266886143967245").strip()

# Base Web Builder URL (cleans trailing slash for redirect accuracy)
WEB_BUILDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com").strip().rstrip('/')

# Webhook Configurations
DESIGN_WEBHOOK_URL = os.environ.get("DESIGN_WEBHOOK_URL", os.environ.get("WEBHOOK_URL", "")).strip()
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", "").strip()

# Storage Directory Setup for JSON Blueprints
BLUEPRINT_STORAGE = {}
BLUEPRINT_DIR = os.path.join(os.getcwd(), "blueprints")
os.makedirs(BLUEPRINT_DIR, exist_ok=True)

# Gemini AI Client Initialization
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("Google GenAI client initialized successfully.")
    except Exception as e:
        logger.error("Failed to initialize Google GenAI client: %s", e)

# ---------------------------------------------------------------------------
# MONGODB DATABASE CONNECTION (Shared with cogs/orca.py)
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None

db = mongo_client["bot_database"] if mongo_client is not None else None
bans_collection = db["website_bans"] if db is not None else None
designs_collection = db["designs"] if db is not None else None
telemetry_collection = db["telemetry"] if db is not None else None
system_status_collection = db["system_status"] if db is not None else None

# In-Memory Cache Fallbacks & Security States
BANNED_IPS = {}               # { ip: ban_expiration_timestamp }
USER_DESIGN_TIMESTAMPS = {}   # { user_id: [timestamp1, timestamp2, ...] }
IS_LOCKDOWN_ACTIVE = False


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS & ORCA.PY INTERFACES
# ---------------------------------------------------------------------------

def is_user_banned_db(discord_id: str) -> tuple[bool, str, str]:
    """Queries MongoDB for active user bans, automatically clearing expired bans."""
    if bans_collection is None:
        return False, "", ""
    
    uid = str(discord_id).strip()
    user_ban = bans_collection.find_one({"$or": [{"discord_id": uid}, {"user_id": uid}]})
    if not user_ban:
        return False, "", ""

    expires_at = user_ban.get("expires_at", 0)
    now = time.time()
    
    if expires_at == 0 or now < expires_at:
        return True, user_ban.get("reason", "Account suspended."), user_ban.get("dev_message", "")
    else:
        # Expired ban cleanup
        bans_collection.delete_one({"_id": user_ban["_id"]})
        return False, "", ""


def check_lockdown_status_db() -> bool:
    """Synchronizes lockdown maintenance state with MongoDB."""
    global IS_LOCKDOWN_ACTIVE
    if system_status_collection is not None:
        status_doc = system_status_collection.find_one({"_id": "global_status"})
        if status_doc:
            IS_LOCKDOWN_ACTIVE = status_doc.get("is_lockdown", False)
    return IS_LOCKDOWN_ACTIVE


def get_blueprint_data(guild_id: str = None) -> dict:
    """
    Retrieves stored blueprint data for a given guild_id.
    Exported for direct consumption by cogs/orca.py.
    """
    if not guild_id:
        return BLUEPRINT_STORAGE

    guild_id = str(guild_id).strip()
    if guild_id in BLUEPRINT_STORAGE:
        return BLUEPRINT_STORAGE[guild_id]

    file_path = os.path.join(BLUEPRINT_DIR, f"{guild_id}.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                BLUEPRINT_STORAGE[guild_id] = data
                return data
        except Exception as e:
            logger.error("Error reading blueprint file for guild %s: %s", guild_id, e)
    
    # Fallback search in MongoDB
    if designs_collection is not None:
        doc = designs_collection.find_one({"guild_id": guild_id}, sort=[("submitted_at", -1)], projection={"_id": 0})
        if doc:
            BLUEPRINT_STORAGE[guild_id] = doc
            return doc

    return {}


def save_blueprint_data(guild_id: str, blueprint: dict):
    """Persists blueprint data both to local JSON files and MongoDB."""
    guild_id = str(guild_id).strip()
    BLUEPRINT_STORAGE[guild_id] = blueprint
    file_path = os.path.join(BLUEPRINT_DIR, f"{guild_id}.json")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, indent=2)
    except IOError as e:
        logger.error("Failed to save local blueprint file for guild %s: %s", guild_id, e)

    if designs_collection is not None:
        try:
            doc = dict(blueprint)
            doc["guild_id"] = guild_id
            doc["submitted_at"] = time.time()
            designs_collection.update_one({"guild_id": guild_id}, {"$set": doc}, upsert=True)
        except Exception as e:
            logger.error("Failed to save blueprint to MongoDB for guild %s: %s", guild_id, e)


def verify_turnstile_captcha(token: str, remote_ip: str) -> bool:
    """Verifies Cloudflare Turnstile token with Cloudflare API."""
    if not token or token == "YOUR_TURNSTILE_SITE_KEY":
        return True  # Fallback for dev/testing mode
    try:
        res = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SECRET_KEY, "response": token, "remoteip": remote_ip},
            timeout=5
        )
        data = res.json()
        return data.get("success", False)
    except Exception as e:
        logger.error(f"Turnstile CAPTCHA verification error: {e}")
        return True


def get_discord_creation_time(user_id: str) -> datetime:
    """Calculates Discord account creation timestamp from snowflake ID."""
    try:
        snowflake = int(user_id)
        timestamp = ((snowflake >> 22) + 1420070400000) / 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def send_system_log(title: str, description: str, color: int = 0x3B82F6, fields: list = None, content: str = None):
    """Sends a system log embed to the dedicated system log channel."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        return

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields or [],
                "footer": {"text": "Echo Studio System Logger"},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    if content:
        payload["content"] = content

    try:
        requests.post(SYSTEM_LOG_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=5)
    except Exception as e:
        logger.error("Failed to post system log webhook: %s", e)


def send_detailed_submission_webhook(webhook_url: str, user_data: dict, blueprint_json: dict, client_ip: str, file_url: str):
    """Dispatches full detailed embed metadata along with raw JSON attachment to Discord webhook."""
    if not webhook_url:
        return

    user_id = user_data.get('id', 'N/A')
    username = user_data.get('username', 'Unknown')
    global_name = user_data.get('global_name', username)
    avatar_url = user_data.get('avatar_url', '')
    account_age = user_data.get('account_age_days', 'N/A')

    target_guild_id = blueprint_json.get('target_guild_id', 'N/A')
    server_name = blueprint_json.get('server_name', 'Unnamed Server')
    server_link = blueprint_json.get('server_link', 'None')
    separator = blueprint_json.get('separator', '-')

    categories = blueprint_json.get('categories', [])
    roles = blueprint_json.get('roles', [])

    total_categories = len(categories)
    total_channels = sum(len(cat.get('channels', [])) for cat in categories)
    total_roles = len(roles)

    category_summary = []
    for cat in categories[:5]:
        ch_count = len(cat.get('channels', []))
        category_summary.append(f"• **{cat.get('name', 'Unnamed')}** (`{ch_count}` channels)")

    category_preview = "\n".join(category_summary) if category_summary else "No categories found."
    if len(categories) > 5:
        category_preview += f"\n*...and {len(categories) - 5} more categories.*"

    embed = {
        "title": f"📥 Blueprint Submitted — {server_name}",
        "description": f"Deploy with command: `/build file:{file_url}`",
        "color": 0x22C55E,
        "thumbnail": {"url": avatar_url} if avatar_url else None,
        "fields": [
            {
                "name": "👤 Creator Profile",
                "value": f"**User:** <@{user_id}>\n**Username:** `{username}` ({global_name})\n**User ID:** `{user_id}`",
                "inline": True
            },
            {
                "name": "🏰 Target Server Details",
                "value": f"**Guild ID:** `{target_guild_id}`\n**Invite/Link:** `{server_link}`\n**Separator:** `{separator}`",
                "inline": True
            },
            {
                "name": "🛡️ Security Context",
                "value": f"**Account Age:** `{account_age}` days\n**Client IP:** `{client_ip}`",
                "inline": True
            },
            {
                "name": "📊 Architecture Metrics",
                "value": f"• **Categories:** `{total_categories}`\n• **Channels:** `{total_channels}`\n• **Roles:** `{total_roles}`",
                "inline": False
            },
            {
                "name": "📂 Category Structure Preview",
                "value": category_preview,
                "inline": False
            }
        ],
        "footer": {
            "text": "Echo Studio Layout Engine • Full JSON Blueprint Attached Below"
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    json_bytes = json.dumps(blueprint_json, indent=2).encode('utf-8')
    payload_data = {
        "content": f"🔔 **New Submission Alert** from <@{user_id}> for Guild `{target_guild_id}`",
        "embeds": [embed]
    }

    files = {
        "files[0]": (f"blueprint_{target_guild_id}_{int(time.time())}.json", io.BytesIO(json_bytes), "application/json")
    }

    try:
        requests.post(
            webhook_url,
            data={"payload_json": json.dumps(payload_data)},
            files=files,
            timeout=10
        )
    except Exception as e:
        logger.error(f"Failed to post submission webhook: {e}")


def extract_invite_code(url_or_code: str) -> str:
    """Extracts clean invite code from Discord URL."""
    match = re.search(r'(?:discord\.gg/|discord\.com/invite/)([a-zA-Z0-9-]+)', url_or_code)
    return match.group(1) if match else url_or_code.strip()


def check_user_guild_admin(user_id: str, guild_id: str) -> bool:
    """Verifies whether the user holds Administrator permissions in target guild via Bot API."""
    if not BOT_TOKEN:
        return True

    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    try:
        res = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/members/{user_id}", headers=headers, timeout=5)
        if res.status_code != 200:
            return False

        member_data = res.json()
        roles_res = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/roles", headers=headers, timeout=5)
        if roles_res.status_code != 200:
            return False

        guild_roles = {r["id"]: int(r["permissions"]) for r in roles_res.json()}
        user_role_ids = member_data.get("roles", [])

        for r_id in user_role_ids:
            perms = guild_roles.get(r_id, 0)
            if (perms & 0x8) == 0x8 or (perms & 0x20) == 0x20:
                return True
        return False
    except Exception as e:
        logger.error(f"Error checking admin permissions: {e}")
        return True


def check_and_update_rate_limit(user_id: str) -> tuple[bool, int]:
    """Enforces rate limiting of 3 design requests per 10 minutes."""
    now = time.time()
    window_start = now - 600

    timestamps = USER_DESIGN_TIMESTAMPS.get(user_id, [])
    valid_timestamps = [t for t in timestamps if t > window_start]
    
    if len(valid_timestamps) >= 3:
        USER_DESIGN_TIMESTAMPS[user_id] = valid_timestamps
        return True, len(valid_timestamps)

    valid_timestamps.append(now)
    USER_DESIGN_TIMESTAMPS[user_id] = valid_timestamps
    return False, len(valid_timestamps)


# ---------------------------------------------------------------------------
# MIDDLEWARE & SECURITY ENFORCEMENT
# ---------------------------------------------------------------------------

@app.before_request
def enforce_security_middleware():
    """Middleware enforcing site bans, IP bans, and maintenance lockdown restrictions."""
    session.permanent = True

    exempt_endpoints = {
        'static', 'discord_login', 'discord_callback', 'get_current_user',
        'check_ip_status', 'api_security_ban', 'api_security_lockdown'
    }
    if request.endpoint in exempt_endpoints:
        return None

    client_ip = get_remote_address()
    now = time.time()

    # 1. ENFORCE IP BANS
    if client_ip in BANNED_IPS:
        ban_expiry = BANNED_IPS[client_ip]
        if now < ban_expiry:
            if request.path.startswith('/api/'):
                return jsonify({
                    "error": "IP Banning Active. Your IP has been temporarily restricted.",
                    "is_banned": True,
                    "ban_until": int(ban_expiry * 1000)
                }), 403
            return render_template('index.html'), 200
        else:
            del BANNED_IPS[client_ip]

    user = session.get('user')
    user_id = str(user.get('id')).strip() if user else None

    # 2. ENFORCE DISCORD USER BANS (Database Sync)
    if user_id:
        is_banned, reason, dev_msg = is_user_banned_db(user_id)
        if is_banned:
            if request.path.startswith('/api/'):
                return jsonify({
                    "error": "Access Denied. You are banned from utilizing the Echo Studio Portal.",
                    "is_banned": True,
                    "ban_reason": reason,
                    "dev_message": dev_msg
                }), 403
            return render_template('index.html'), 200

    # 3. ENFORCE GLOBAL LOCKDOWN / MAINTENANCE
    lockdown_active = check_lockdown_status_db()
    if lockdown_active:
        if not user_id or user_id != PRIMARY_OWNER_ID:
            if request.path.startswith('/api/'):
                return jsonify({
                    "error": "System Under Maintenance. The dashboard is currently locked.",
                    "is_lockdown": True
                }), 530
            return render_template('index.html'), 200


@app.errorhandler(429)
def ratelimit_handler(e):
    """Handles Flask-Limiter IP rate limit triggers and applies 1-hour IP ban."""
    client_ip = get_remote_address()
    one_hour_later = time.time() + 3600
    BANNED_IPS[client_ip] = one_hour_later

    logger.warning(f"[SECURITY] IP {client_ip} exceeded rate limit and was placed on 1-hour ban.")
    return jsonify({
        "error": "Rate limit exceeded. You have been placed on a 1-hour IP ban.",
        "is_banned": True,
        "ban_until": int(one_hour_later * 1000)
    }), 429


# ---------------------------------------------------------------------------
# BOT SECURE SYNC API ENDPOINTS (Called by cogs/orca.py)
# ---------------------------------------------------------------------------

@app.route('/api/security/ban', methods=['POST'])
def api_security_ban():
    """Receives ban/unban enforcement signals directly from orca.py."""
    auth_header = request.headers.get("X-Bot-Auth", "")
    if auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized Bot API call"}), 401

    data = request.get_json() or {}
    user_id = str(data.get("user_id", "")).strip()
    action = data.get("action", "ban")

    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    if action == "ban":
        reason = data.get("reason", "Banned by Staff")
        dev_message = data.get("dev_message", "Account suspended.")
        duration = data.get("duration_seconds", 0)
        
        if bans_collection is not None:
            now = time.time()
            expires_at = now + duration if duration > 0 else 0
            bans_collection.update_one(
                {"discord_id": user_id},
                {"$set": {"discord_id": user_id, "reason": reason, "dev_message": dev_message, "expires_at": expires_at}},
                upsert=True
            )
        return jsonify({"success": True, "action": "banned", "user_id": user_id})

    elif action == "unban":
        if bans_collection is not None:
            bans_collection.delete_one({"discord_id": user_id})
        return jsonify({"success": True, "action": "unbanned", "user_id": user_id})

    return jsonify({"error": "Invalid action"}), 400


@app.route('/api/security/lockdown', methods=['POST'])
def api_security_lockdown():
    """Receives lockdown maintenance signals directly from orca.py."""
    auth_header = request.headers.get("X-Bot-Auth", "")
    if auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized Bot API call"}), 401

    data = request.get_json() or {}
    enable = data.get("enable", False)

    global IS_LOCKDOWN_ACTIVE
    IS_LOCKDOWN_ACTIVE = enable

    if system_status_collection is not None:
        system_status_collection.update_one(
            {"_id": "global_status"},
            {"$set": {"is_lockdown": enable, "updated_at": time.time()}},
            upsert=True
        )

    return jsonify({"success": True, "is_lockdown": IS_LOCKDOWN_ACTIVE})


# ---------------------------------------------------------------------------
# FRONTEND API ENDPOINTS (index.html Integration)
# ---------------------------------------------------------------------------

@app.route('/api/check-ip-status', methods=['GET'])
def check_ip_status():
    """Returns current IP ban status and timer expiration for frontend rendering."""
    client_ip = get_remote_address()
    now = time.time()
    if client_ip in BANNED_IPS:
        ban_expiry = BANNED_IPS[client_ip]
        if now < ban_expiry:
            return jsonify({
                "banned": True,
                "ban_until": int(ban_expiry * 1000),
                "reason": "IP Rate Limit Exceeded. You have been placed on a temporary ban."
            })
        else:
            del BANNED_IPS[client_ip]
    return jsonify({"banned": False})


@app.route('/api/verify-server', methods=['POST'])
def verify_server():
    """Verifies server link format, 24h+ duration, Guild ID match, and Admin permissions."""
    data = request.get_json() or {}
    guild_id = str(data.get("guild_id", "")).strip()
    server_link = data.get("server_link", "").strip()
    captcha_token = data.get("captcha_token", "").strip()
    client_ip = get_remote_address()

    if captcha_token and not verify_turnstile_captcha(captcha_token, client_ip):
        return jsonify({"valid": False, "error": "Security CAPTCHA verification failed."}), 400

    code = extract_invite_code(server_link)
    if not code:
        return jsonify({"valid": False, "error": "Invalid server invite link format."}), 400

    user = session.get('user')
    if not user:
        return jsonify({"valid": False, "error": "User not authenticated."}), 401

    try:
        res = requests.get(f"{DISCORD_API_BASE_URL}/invites/{code}?with_counts=true", timeout=5)
        if res.status_code == 404:
            return jsonify({"valid": False, "error": "The Discord invite link is invalid or expired."}), 400
        res.raise_for_status()

        invite_data = res.json()
        guild_info = invite_data.get("guild", {})
        linked_guild_id = str(guild_info.get("id", "")).strip()

        if linked_guild_id != guild_id:
            return jsonify({
                "valid": False,
                "error": f"Mismatch: Invite belongs to Guild ID `{linked_guild_id}`, not target ID `{guild_id}`."
            }), 400

        max_age = invite_data.get("max_age", 0)
        if max_age != 0 and max_age < 86400:
            return jsonify({
                "valid": False,
                "error": "Invite duration is under 24 hours. Please generate an invite valid for at least 24 hours or infinite."
            }), 400

        user_id = str(user.get("id")).strip()
        if not check_user_guild_admin(user_id, guild_id):
            return jsonify({
                "valid": False,
                "error": "Admin check failed. You must hold Administrator permissions in the target server."
            }), 403

        return jsonify({"valid": True, "message": "Verification successful."})
    except requests.RequestException as e:
        logger.error(f"Invite verification fallback: {e}")
        return jsonify({"valid": True, "message": "Verification passed."})


@app.route('/api/auto-ban', methods=['POST'])
def auto_ban_trigger():
    """Endpoint triggered by frontend when security limits or alt accounts are detected."""
    data = request.get_json() or {}
    reason = data.get("reason", "Automated Security Violation")
    duration_ms = data.get("ban_duration_ms", 12 * 3600 * 1000)
    user_id = data.get("user_id")
    client_ip = get_remote_address()

    ban_until = time.time() + (duration_ms / 1000.0)
    BANNED_IPS[client_ip] = ban_until

    if user_id:
        if bans_collection is not None:
            bans_collection.update_one(
                {"discord_id": str(user_id)},
                {"$set": {"discord_id": str(user_id), "reason": reason, "dev_message": reason, "expires_at": ban_until}},
                upsert=True
            )

    send_system_log(
        title="🚨 AUTOMATED BAN TRIGGERED",
        description=f"Automated ban applied: `{reason}`",
        color=0xEF4444,
        content=f"<@{PRIMARY_OWNER_ID}> 🚨 **Security Shield Alert**"
    )

    return jsonify({"status": "banned", "ban_until": int(ban_until * 1000)})


@app.route('/api/log-entry', methods=['POST'])
def log_entry():
    """Logs detailed user session access data to telemetry and Discord webhook."""
    data = request.get_json() or {}
    client_ip = get_remote_address()
    user_agent = request.headers.get("User-Agent", "Unknown")

    if telemetry_collection is not None:
        telemetry_collection.insert_one({
            "user_id": data.get("user_id"),
            "global_name": data.get("global_name"),
            "account_age_days": data.get("account_age_days"),
            "ip_address": client_ip,
            "user_agent": user_agent,
            "logged_at": time.time()
        })

    send_system_log(
        title="🔑 Web Access Log",
        description=f"User **{data.get('global_name')}** (`@{data.get('username')}`) connected.",
        color=0x3B82F6,
        fields=[
            {"name": "User ID", "value": f"`{data.get('user_id')}`", "inline": True},
            {"name": "Account Age", "value": f"`{data.get('account_age_days')} days`", "inline": True},
            {"name": "IP Address", "value": f"`{client_ip}`", "inline": True}
        ]
    )
    return jsonify({"logged": True})


# ---------------------------------------------------------------------------
# DISCORD OAUTH2 ROUTES
# ---------------------------------------------------------------------------

@app.route('/api/auth/discord/login')
def discord_login():
    """Redirects user to Discord OAuth2 authorization URL."""
    redirect_uri = f"{WEB_BUILDER_URL}/api/auth/discord/callback"
    oauth_url = (
        f"{DISCORD_API_BASE_URL}/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope=identify"
    )
    return redirect(oauth_url)


@app.route('/api/auth/discord/callback')
def discord_callback():
    """Handles OAuth2 authorization code exchange."""
    code = request.args.get('code')
    if not code:
        return "Missing OAuth2 code from Discord.", 400

    redirect_uri = f"{WEB_BUILDER_URL}/api/auth/discord/callback"
    token_data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    try:
        token_res = requests.post(f"{DISCORD_API_BASE_URL}/oauth2/token", data=token_data, headers=headers, timeout=10)
        token_res.raise_for_status()
        tokens = token_res.json()
        access_token = tokens.get('access_token')

        user_res = requests.get(
            f"{DISCORD_API_BASE_URL}/users/@me",
            headers={'Authorization': f"Bearer {access_token}"},
            timeout=10
        )
        user_res.raise_for_status()
        user_profile = user_res.json()

        user_id = str(user_profile.get('id')).strip()
        username = user_profile.get('username')
        global_name = user_profile.get('global_name') or username
        avatar = user_profile.get('avatar')

        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"

        created_at = get_discord_creation_time(user_id)
        account_age_days = (datetime.now(timezone.utc) - created_at).days

        # ALT ACCOUNT DETECTION (0-30 DAYS)
        if 0 <= account_age_days <= 30:
            alt_reason = f"Alt Shield: Account age ({account_age_days} days) is under the 30-day requirement."
            if bans_collection is not None:
                bans_collection.update_one(
                    {"discord_id": user_id},
                    {"$set": {"discord_id": user_id, "reason": alt_reason, "dev_message": alt_reason, "expires_at": 0}},
                    upsert=True
                )
            return redirect('/banned')

        is_banned, _, _ = is_user_banned_db(user_id)
        if is_banned:
            return redirect('/banned')

        # Set persistent session
        session.permanent = True
        session['user'] = {
            'id': user_id,
            'username': username,
            'global_name': global_name,
            'avatar': avatar,
            'avatar_url': avatar_url,
            'created_at': created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
            'account_age_days': account_age_days
        }

        return redirect('/')
    except Exception as e:
        logger.error("OAuth2 authentication failure: %s", e)
        return "Authentication failed.", 500


@app.route('/api/auth/logout')
def discord_logout():
    session.pop('user', None)
    return redirect('/')


@app.route('/api/auth/me')
def get_current_user():
    user = session.get('user')
    user_id = str(user.get('id')).strip() if user else None

    is_banned = False
    dev_message = ""
    if user_id:
        is_banned, _, dev_message = is_user_banned_db(user_id)

    is_lockdown = check_lockdown_status_db() and (not user_id or user_id != PRIMARY_OWNER_ID)

    if is_banned:
        return jsonify({"authenticated": False, "is_banned": True, "dev_message": dev_message, "user": None})

    if is_lockdown:
        return jsonify({"authenticated": False, "is_lockdown": True, "user": None})

    if user:
        return jsonify({"authenticated": True, "is_banned": False, "is_lockdown": False, "user": user})

    return jsonify({"authenticated": False, "is_banned": False, "is_lockdown": False, "user": None})


# ---------------------------------------------------------------------------
# PAGE RENDERING & AI GENERATION ROUTES
# ---------------------------------------------------------------------------

@app.route('/')
@app.route('/banned')
@app.route('/maintenance')
def index():
    return render_template('index.html')


@app.route('/blueprint/<guild_id>.json', methods=['GET'])
def serve_blueprint(guild_id):
    """Serves the generated layout JSON for /build command execution."""
    data = get_blueprint_data(guild_id)
    if data:
        return jsonify(data)
    return send_from_directory(BLUEPRINT_DIR, f"{guild_id}.json", mimetype='application/json')


@app.route('/api/generate-layout', methods=['POST'])
@limiter.limit("5 per minute")
def generate_layout():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized. Please login with Discord."}), 401

    user_id = str(user.get('id')).strip()

    # Rate Limit Check (Max 3 attempts in 10 minutes)
    is_exceeded, _ = check_and_update_rate_limit(user_id)
    if is_exceeded:
        dev_msg = "Automated Security Ban: Exceeded 3 design generations in 10 minutes."
        ban_until = time.time() + (12 * 3600)
        
        if bans_collection is not None:
            bans_collection.update_one(
                {"discord_id": user_id},
                {"$set": {"discord_id": user_id, "reason": dev_msg, "dev_message": dev_msg, "expires_at": ban_until}},
                upsert=True
            )

        client_ip = get_remote_address()
        BANNED_IPS[client_ip] = ban_until

        return jsonify({
            "error": "Generation rate limit reached (3 per 10 mins). You have been temporarily restricted for 12 hours.",
            "is_banned": True,
            "dev_message": dev_msg
        }), 403

    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    guild_id = str(data.get('guild_id', '')).strip()
    server_link = data.get('server_link', '').strip()
    separator = data.get('separator', '-')

    if not prompt or not guild_id or not server_link:
        return jsonify({"error": "Prompt, Guild ID, and Server Link are required."}), 400

    system_instruction = (
        "Generate a raw JSON layout for a Discord server based on user prompt conforming strictly to this schema:\n"
        "{\"server_name\": \"String\", \"roles\": [\"String\"], \"categories\": [{\"name\": \"String\", \"channels\": [{\"emoji\": \"💬\", \"name\": \"string\", \"type\": \"text|voice|announcement\", \"topic\": \"string\"}]}]}"
    )

    layout_data = None
    if client:
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"Guild ID: {guild_id}\nPrompt: {prompt}",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json"
                )
            )
            layout_data = json.loads(response.text)
        except Exception as e:
            logger.warning(f"AI Generation failed: {e}")

    if not layout_data:
        layout_data = {
            "server_name": "Echo Studio — Community Layout",
            "roles": ["Administrator", "Moderator", "Member"],
            "categories": [
                {
                    "name": "📌 INFORMATION",
                    "channels": [{"emoji": "📜", "name": f"rules{separator}info", "type": "text", "topic": "Server rules"}]
                }
            ]
        }

    layout_data["target_guild_id"] = guild_id
    layout_data["server_link"] = server_link
    layout_data["separator"] = separator
    layout_data["creator"] = user
    return jsonify(layout_data)


@app.route('/api/submit-design', methods=['POST'])
def submit_design():
    blueprint = request.get_json()
    if not blueprint:
        return jsonify({"error": "No blueprint provided"}), 400

    target_guild = str(blueprint.get("target_guild_id", "Unknown")).strip()
    save_blueprint_data(target_guild, blueprint)

    file_url = f"{WEB_BUILDER_URL}/blueprint/{target_guild}.json"
    client_ip = get_remote_address()
    user = session.get('user', {})

    if DESIGN_WEBHOOK_URL:
        send_detailed_submission_webhook(
            webhook_url=DESIGN_WEBHOOK_URL,
            user_data=user,
            blueprint_json=blueprint,
            client_ip=client_ip,
            file_url=file_url
        )

    return jsonify({"status": "success", "file_url": file_url})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
