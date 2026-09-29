import os
import json
import io
import time
import logging
import re
import requests
from threading import Thread
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, session, make_response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from google import genai
from google.genai import types
from pymongo import MongoClient

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("keep_alive")

app = Flask(__name__, template_folder='templates')

# Persistent Session Key and Lifespan (Prevents logging users out on redeploy/unban)
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
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
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
# HELPER FUNCTIONS & DATABASE PROXIES
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
        bans_collection.delete_one({"_id": user_ban["_id"]})
        return False, "", ""


def get_ban_details(discord_id: str) -> dict:
    """Retrieves full ban structured details for template rendering."""
    is_banned, reason, dev_message = is_user_banned_db(discord_id)
    return {
        "is_banned": is_banned,
        "reason": reason,
        "dev_message": dev_message
    }


def is_user_banned(discord_id: str) -> bool:
    """Boolean wrapper for user ban verification."""
    banned, _, _ = is_user_banned_db(discord_id)
    return banned


def ban_user_db(user_id: str, reason: str = "Automated website rate-limit ban", dev_message: str = "", duration_seconds: int = 0):
    """Inserts or updates a user ban record in MongoDB."""
    uid = str(user_id).strip()
    if bans_collection is not None:
        now = time.time()
        expires_at = now + duration_seconds if duration_seconds > 0 else 0
        bans_collection.update_one(
            {"discord_id": uid},
            {"$set": {"discord_id": uid, "reason": reason, "dev_message": dev_message or reason, "expires_at": expires_at}},
            upsert=True
        )


def check_lockdown_status_db() -> bool:
    """Synchronizes lockdown maintenance state with MongoDB or environment."""
    global IS_LOCKDOWN_ACTIVE
    if system_status_collection is not None:
        status_doc = system_status_collection.find_one({"_id": "global_status"})
        if status_doc:
            IS_LOCKDOWN_ACTIVE = status_doc.get("is_lockdown", False)
    return IS_LOCKDOWN_ACTIVE or os.environ.get("IS_LOCKDOWN", "false").lower() in ("true", "1", "yes")


def get_blueprint_data(guild_id: str = None) -> dict:
    """Retrieves stored blueprint data for a given guild_id."""
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
    
    if designs_collection is not None:
        doc = designs_collection.find_one({"guild_id": guild_id}, sort=[("submitted_at", -1)], projection={"_id": 0})
        if doc:
            BLUEPRINT_STORAGE[guild_id] = doc
            return doc

    return {}


def save_blueprint_data(guild_id: str, blueprint: dict):
    """Persists blueprint data locally and to MongoDB."""
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
    if not token or token == "1x0000000000000000000000000000000AA":
        return True  # Fallback for dev mode
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


def send_system_webhook(title: str, description: str, fields: list = None, color: int = 0x3B82F6, content: str = None):
    """Sends structured alert logs to Discord System Log Webhook."""
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


# ---------------------------------------------------------------------------
# MIDDLEWARE & SECURITY ENFORCEMENT
# ---------------------------------------------------------------------------

@app.before_request
def enforce_security_middleware():
    """Middleware enforcing site bans, IP bans, alt account locks, and maintenance restrictions."""
    session.permanent = True

    exempt_endpoints = {
        'static', 'discord_login', 'discord_callback', 'auth_me',
        'banned_page', 'health', 'api_security_ban', 'api_security_lockdown'
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
            return redirect('/banned')
        else:
            del BANNED_IPS[client_ip]

    user_data = session.get('user', {})
    user_id = str(user_data.get('id', '')).strip() if user_data else None

    # 2. ENFORCE DISCORD USER BANS (Database Sync)
    if user_id:
        is_banned, _, _ = is_user_banned_db(user_id)
        if is_banned:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Access Denied. Account suspended.", "is_banned": True}), 403
            return redirect('/banned')

        # Alt Account Detection (0-30 days)
        created_at = get_discord_creation_time(user_id)
        account_age_days = (datetime.now(timezone.utc) - created_at).days
        if 0 <= account_age_days <= 30:
            alt_reason = f"Alt Account Shield: Account age ({account_age_days} days) is under the 30-day requirement."
            ban_user_db(user_id, reason=alt_reason, dev_message=alt_reason)
            session.pop('user', None)
            if request.path.startswith('/api/'):
                return jsonify({"error": alt_reason, "is_banned": True}), 403
            return redirect('/banned')

    # 3. ENFORCE GLOBAL LOCKDOWN / MAINTENANCE
    if check_lockdown_status_db():
        if not user_id or user_id != PRIMARY_OWNER_ID:
            if request.path.startswith('/api/'):
                return jsonify({"error": "System Under Maintenance.", "is_lockdown": True}), 530
            return redirect('/maintenance')


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


@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(500)
@app.errorhandler(502)
@app.errorhandler(503)
def force_html_error(e):
    """Intercepts HTTP errors and returns index.html or JSON error payload."""
    if request.path.startswith('/api/'):
        return jsonify({
            "error": "An error occurred handling this API request.",
            "status_code": getattr(e, 'code', 500)
        }), getattr(e, 'code', 500)
    return make_response(render_template('index.html'), 200)


# ---------------------------------------------------------------------------
# BOT SECURE SYNC API ENDPOINTS (Called by cogs/orca.py)
# ---------------------------------------------------------------------------

@app.route('/api/security/ban', methods=['POST'])
def api_security_ban():
    """Receives ban/unban signals directly from orca.py."""
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
        ban_user_db(user_id, reason=reason, dev_message=dev_message, duration_seconds=duration)
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
# ROUTES & GLASS UI BANNED PAGE
# ---------------------------------------------------------------------------

@app.route('/')
@app.route('/maintenance')
def index():
    """Serves the primary UI container."""
    return render_template('index.html')


@app.route('/banned')
def banned_page():
    """Renders glassmorphism UI page for banned users."""
    user_data = session.get('user', {})
    user_id = str(user_data.get('id', ''))
    
    ban_info = get_ban_details(user_id) if user_id else {}
    dev_msg = ban_info.get('dev_message') or "You have been restricted from accessing this website."

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Access Denied - Banned</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }}
            body {{
                background: linear-gradient(135deg, #0f0c20 0%, #150a12 50%, #050508 100%);
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #ffffff;
                overflow: hidden;
            }}
            .glass-card {{
                background: rgba(255, 255, 255, 0.03);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.08);
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.6);
                border-radius: 20px;
                padding: 40px;
                max-width: 480px;
                width: 90%;
                text-align: center;
            }}
            h1 {{
                color: #ff3b30;
                font-size: 2.8rem;
                font-weight: 800;
                letter-spacing: 2px;
                margin-bottom: 12px;
                text-shadow: 0 0 20px rgba(255, 59, 48, 0.4);
            }}
            p.subtext {{
                color: #a0a0ab;
                font-size: 0.95rem;
                margin-bottom: 24px;
            }}
            .dev-box {{
                background: rgba(255, 59, 48, 0.08);
                border-left: 4px solid #ff3b30;
                border-radius: 8px;
                padding: 16px;
                margin-top: 15px;
                text-align: left;
            }}
            .dev-box label {{
                display: block;
                font-size: 0.75rem;
                color: #ff6b63;
                text-transform: uppercase;
                letter-spacing: 1px;
                font-weight: 700;
                margin-bottom: 6px;
            }}
            .dev-box p {{
                color: #e2e2e8;
                font-size: 0.9rem;
                line-height: 1.4;
            }}
        </style>
    </head>
    <body>
        <div class="glass-card">
            <h1>BANNED</h1>
            <p class="subtext">Your access to this application has been suspended.</p>
            <div class="dev-box">
                <label>Message from Developer</label>
                <p>{dev_msg}</p>
            </div>
        </div>
    </body>
    </html>
    """
    return make_response(html_content, 200)


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "online", "maintenance": check_lockdown_status_db()}), 200


@app.route('/blueprint/<guild_id>.json', methods=['GET'])
def serve_blueprint(guild_id):
    """Serves the generated layout JSON for /build command execution."""
    data = get_blueprint_data(guild_id)
    if data:
        return jsonify(data)
    return send_from_directory(BLUEPRINT_DIR, f"{guild_id}.json", mimetype='application/json')


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

        if 0 <= account_age_days <= 30:
            alt_reason = f"Alt Shield: Account age ({account_age_days} days) is under the 30-day requirement."
            ban_user_db(user_id, reason=alt_reason, dev_message=alt_reason)
            return redirect('/banned')

        is_banned, _, _ = is_user_banned_db(user_id)
        if is_banned:
            return redirect('/banned')

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
def logout():
    session.pop('user', None)
    return redirect('/')


@app.route('/api/auth/me')
def auth_me():
    """Client-side authentication & status verification endpoint."""
    try:
        user_data = session.get('user')
        user_id = str(user_data.get('id', '')).strip() if user_data else None

        is_lockdown = check_lockdown_status_db() and (not user_id or user_id != PRIMARY_OWNER_ID)
        if is_lockdown:
            return jsonify({"authenticated": False, "is_lockdown": True, "is_banned": False, "user": None}), 200

        if user_data and user_id:
            is_banned, _, dev_message = is_user_banned_db(user_id)
            if is_banned:
                session.pop('user', None)
                return jsonify({"authenticated": False, "is_lockdown": False, "is_banned": True, "dev_message": dev_message, "user": None}), 200

            return jsonify({"authenticated": True, "is_lockdown": False, "is_banned": False, "user": user_data}), 200

        return jsonify({"authenticated": False, "is_lockdown": False, "is_banned": False, "user": None}), 200

    except Exception as e:
        logger.error("Error encountered in auth_me route: %s", e)
        return jsonify({"authenticated": False, "is_lockdown": False, "is_banned": False, "error": "Internal state error"}), 500


# ---------------------------------------------------------------------------
# API: LOGGING, GENERATION, & SERVER LINK VERIFICATION
# ---------------------------------------------------------------------------

@app.route('/api/log_entry', methods=['POST'])
def log_entry():
    """Logs detailed user profile, account age, and connection metadata."""
    user = session.get('user', {})
    if not user:
        return jsonify({"status": "ignored"}), 200

    user_id = user.get('id', 'Unknown')
    username = user.get('username', 'Unknown')
    account_age = user.get('account_age_days', 0)

    if telemetry_collection is not None:
        telemetry_collection.insert_one({
            "user_id": user_id,
            "global_name": user.get('global_name'),
            "account_age_days": account_age,
            "ip_address": get_remote_address(),
            "user_agent": request.headers.get("User-Agent", "Unknown"),
            "logged_at": time.time()
        })

    fields = [
        {"name": "User", "value": f"@{username} (`{user_id}`)", "inline": True},
        {"name": "Account Age", "value": f"`{account_age} days`", "inline": True},
        {"name": "IP Address", "value": f"`{get_remote_address()}`", "inline": True}
    ]

    send_system_webhook(
        title="📥 Detailed Website Entry Log",
        description=f"User <@{user_id}> accessed the site.",
        fields=fields,
        color=0x3498DB
    )
    return jsonify({"status": "logged"}), 200


@app.route('/api/verify_and_generate', methods=['POST'])
@limiter.limit("5 per minute")
def verify_and_generate():
    """
    Verifies CAPTCHA, 24-hour invite link, guild match, Admin perms,
    enforces 3 designs / 10 min rate limit, and triggers Gemini AI layout generation.
    """
    data = request.json or {}
    captcha_token = data.get('captcha_token', '').strip()
    client_ip = get_remote_address()

    # 1. Verify CAPTCHA
    if not verify_turnstile_captcha(captcha_token, client_ip):
        return jsonify({"success": False, "message": "Security CAPTCHA verification failed."}), 400

    user_data = session.get('user')
    if not user_data:
        return jsonify({"success": False, "message": "Unauthorized. Please login with Discord."}), 401

    user_id = str(user_data.get('id', '')).strip()

    # 2. Rate Limiting Check (3 per 10 minutes -> 12-Hour Ban)
    now = time.time()
    timestamps = USER_DESIGN_TIMESTAMPS.get(user_id, [])
    valid_timestamps = [t for t in timestamps if now - t < 600]
    
    if len(valid_timestamps) >= 3:
        twelve_hours_later = time.time() + (12 * 3600)
        BANNED_IPS[client_ip] = twelve_hours_later
        ban_msg = "Exceeded 3 layout generations per 10 minutes. Banned for 12 hours."
        ban_user_db(user_id, reason=ban_msg, dev_message=ban_msg, duration_seconds=12 * 3600)

        send_system_webhook(
            title="🚨 INSTANT 12-HOUR BAN: Rate Limit Exceeded",
            description=f"User <@{user_id}> (`{user_id}`) exceeded limit and was banned for 12 hours.",
            fields=[{"name": "IP Address", "value": f"`{client_ip}`", "inline": True}],
            color=0xFF0000
        )
        return jsonify({
            "error": "Rate limit exceeded (3 layouts in 10 mins). You have been banned for 12 hours.",
            "is_banned": True,
            "ban_until": int(twelve_hours_later * 1000)
        }), 403

    valid_timestamps.append(now)
    USER_DESIGN_TIMESTAMPS[user_id] = valid_timestamps

    # 3. Extract payload
    server_id = str(data.get('server_id', '')).strip()
    server_invite = data.get('server_link', '').strip()
    prompt = data.get('prompt', '').strip()
    separator = data.get('separator', '-')

    if not server_id or not server_invite or not prompt:
        return jsonify({"success": False, "message": "Missing server ID, invite link, or prompt."}), 400

    invite_code = extract_invite_code(server_invite)

    # 4. Verify server link, server ID, duration, & Admin permissions
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"} if DISCORD_BOT_TOKEN else {}
    try:
        res = requests.get(f"{DISCORD_API_BASE_URL}/invites/{invite_code}?with_counts=true", headers=headers, timeout=5)
        if res.status_code == 200:
            invite_data = res.json()
            resolved_guild_id = str(invite_data.get('guild', {}).get('id', '')).strip()

            if resolved_guild_id != server_id:
                return jsonify({
                    "success": False,
                    "message": f"Verification failed: Invite belongs to Guild ID `{resolved_guild_id}`, not target ID `{server_id}`."
                }), 400

            max_age = invite_data.get("max_age", 0)
            if max_age != 0 and max_age < 86400:
                return jsonify({
                    "success": False,
                    "message": "Verification failed: Server invite link must be active for at least 24 hours (or infinite)."
                }), 400

            if not check_user_guild_admin(user_id, server_id):
                return jsonify({
                    "success": False,
                    "message": "Verification failed: You do not hold Administrator permissions in this target server."
                }), 403
        else:
            return jsonify({
                "success": False,
                "message": "Verification failed: Provided invite link is invalid or expired."
            }), 400
    except Exception as err:
        logger.error("Guild verification error: %s", err)

    # 5. Gemini Layout Generation using google-genai SDK (`gemini-2.5-flash`)
    system_instruction = (
        "Generate a raw JSON layout for a Discord server based on user prompt conforming strictly to this schema:\n"
        "{\"server_name\": \"String\", \"roles\": [\"String\"], \"categories\": [{\"name\": \"String\", \"channels\": [{\"emoji\": \"💬\", \"name\": \"string\", \"type\": \"text|voice|announcement\", \"topic\": \"string\"}]}]}"
    )

    layout_data = None
    if client:
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"Guild ID: {server_id}\nPrompt: {prompt}",
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

    layout_data["target_guild_id"] = server_id
    layout_data["server_link"] = server_invite
    layout_data["separator"] = separator
    layout_data["creator"] = user_data

    # Save blueprint automatically
    save_blueprint_data(server_id, layout_data)
    file_url = f"{WEB_BUILDER_URL}/blueprint/{server_id}.json"

    # Post Submission Webhook
    if DESIGN_WEBHOOK_URL:
        payload = {
            "embeds": [{
                "title": f"📥 Blueprint Generated & Submitted — Guild #{server_id}",
                "description": f"Deploy with: `/build file:{file_url}`",
                "color": 0x22C55E
            }]
        }
        json_bytes = json.dumps(layout_data, indent=2).encode('utf-8')
        files = {"files[0]": (f"blueprint_{server_id}.json", io.BytesIO(json_bytes), "application/json")}
        try:
            requests.post(DESIGN_WEBHOOK_URL, data={"payload_json": json.dumps(payload)}, files=files, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send submission webhook: {e}")

    return jsonify({
        "success": True,
        "message": "Layout generated successfully!",
        "file_url": file_url,
        "layout": layout_data
    }), 200


# ---------------------------------------------------------------------------
# SERVER EXECUTION
# ---------------------------------------------------------------------------

def run():
    """Runs the Flask web server."""
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)


def keep_alive():
    """Spawns the web server as a daemon thread."""
    server_thread = Thread(target=run, daemon=True)
    server_thread.start()
    logger.info("Keep-alive server thread initiated successfully.")


if __name__ == '__main__':
    keep_alive()
    # Keep the main process alive when executed directly
    while True:
        time.sleep(3600)
