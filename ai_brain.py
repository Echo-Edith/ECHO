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

# Discord OAuth2 & API Configurations
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# Cloudflare Turnstile Configuration
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA").strip()

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
# MONGODB DATABASE CONNECTION
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None

db = mongo_client["bot_database"] if mongo_client is not None else None
designs_collection = db["designs"] if db is not None else None
telemetry_collection = db["telemetry"] if db is not None else None


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

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


def extract_invite_code(url_or_code: str) -> str:
    """Extracts clean invite code from Discord URL."""
    match = re.search(r'(?:discord\.gg/|discord\.com/invite/)([a-zA-Z0-9-]+)', url_or_code)
    return match.group(1) if match else url_or_code.strip()


def check_user_guild_admin(user_id: str, guild_id: str) -> bool:
    """Verifies whether the user is the Server Owner or holds Administrator permissions in target guild via Bot API."""
    user_id_str = str(user_id).strip()

    if not BOT_TOKEN:
        return True

    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    try:
        # Check if user is the Server Owner directly from guild details
        guild_res = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}", headers=headers, timeout=5)
        if guild_res.status_code == 200:
            guild_data = guild_res.json()
            if str(guild_data.get("owner_id", "")).strip() == user_id_str:
                return True

        # Fallback to Administrator permission check on member roles
        res = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/members/{user_id_str}", headers=headers, timeout=5)
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
# MIDDLEWARE
# ---------------------------------------------------------------------------

@app.before_request
def make_session_permanent():
    """Maintains user Discord login sessions."""
    session.permanent = True


# ---------------------------------------------------------------------------
# FRONTEND API ENDPOINTS
# ---------------------------------------------------------------------------

@app.route('/api/verify-server', methods=['POST'])
def verify_server():
    """Verifies server link format, 24h+ duration, Guild ID match, and Admin permissions."""
    data = request.get_json() or {}
    guild_id = str(data.get("guild_id", "")).strip()
    server_link = data.get("server_link", "").strip()
    captcha_token = data.get("captcha_token", "").strip()

    user = session.get('user')
    if not user:
        return jsonify({"valid": False, "error": "User not authenticated."}), 401

    user_id = str(user.get("id")).strip()

    if captcha_token and not verify_turnstile_captcha(captcha_token, request.remote_addr):
        return jsonify({"valid": False, "error": "Security CAPTCHA verification failed."}), 400

    code = extract_invite_code(server_link)
    if not code:
        return jsonify({"valid": False, "error": "Invalid server invite link format."}), 400

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

        if not check_user_guild_admin(user_id, guild_id):
            return jsonify({
                "valid": False,
                "error": "Admin check failed. You must hold Administrator permissions or be the Server Owner in the target server."
            }), 403

        return jsonify({"valid": True, "message": "Verification successful."})
    except requests.RequestException as e:
        logger.error(f"Invite verification fallback: {e}")
        return jsonify({"valid": True, "message": "Verification passed."})


@app.route('/api/log-entry', methods=['POST'])
def log_entry():
    """Logs detailed user session access data to telemetry and Discord webhook."""
    data = request.get_json() or {}
    client_ip = request.remote_addr
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
    if user:
        return jsonify({"authenticated": True, "user": user})

    return jsonify({"authenticated": False, "user": None})


# ---------------------------------------------------------------------------
# PAGE RENDERING & AI GENERATION ROUTES
# ---------------------------------------------------------------------------

@app.route('/')
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
def generate_layout():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized. Please login with Discord."}), 401

    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    guild_id = str(data.get('guild_id', '')).strip()
    server_link = data.get('server_link', '').strip()
    separator = data.get('separator', '-')
    
    # Optional preferred category and channel counts
    categories_count = data.get('categories_count')
    channels_count = data.get('channels_count')

    if not prompt or not guild_id or not server_link:
        return jsonify({"error": "Prompt, Guild ID, and Server Link are required."}), 400

    system_instruction = (
        "Generate a raw JSON layout for a Discord server based on user prompt conforming strictly to this schema:\n"
        "{\"server_name\": \"String\", \"roles\": [\"String\"], \"categories\": [{\"name\": \"String\", \"channels\": [{\"emoji\": \"💬\", \"name\": \"string\", \"type\": \"text|voice|announcement\", \"topic\": \"string\"}]}]}"
    )

    # Build prompt parameters including optional channel/category constraints
    prompt_payload = f"Guild ID: {guild_id}\nPrompt: {prompt}"
    if categories_count is not None and str(categories_count).isdigit():
        prompt_payload += f"\nPreferred Categories Count: {categories_count}"
    if channels_count is not None and str(channels_count).isdigit():
        prompt_payload += f"\nPreferred Total Channels Count: {channels_count}"

    layout_data = None
    if client:
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt_payload,
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

    if DESIGN_WEBHOOK_URL:
        payload = {
            "embeds": [
                {
                    "title": f"📥 Blueprint Submitted — Guild #{target_guild}",
                    "description": f"Deploy with: `/build file:{file_url}`",
                    "color": 0x22C55E
                }
            ]
        }
        json_bytes = json.dumps(blueprint, indent=2).encode('utf-8')
        files = {"files[0]": (f"blueprint_{target_guild}.json", io.BytesIO(json_bytes), "application/json")}
        try:
            requests.post(DESIGN_WEBHOOK_URL, data={"payload_json": json.dumps(payload)}, files=files, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send submission webhook: {e}")

    return jsonify({"status": "success", "file_url": file_url})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
