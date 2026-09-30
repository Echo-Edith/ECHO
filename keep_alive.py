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
from google import genai
from google.genai import types
from pymongo import MongoClient

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("keep_alive")

app = Flask(__name__, template_folder='templates')

# Persistent Session Key and Lifespan
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent-2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get("FLASK_ENV") == "production"

# Discord OAuth2 & API Configurations
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
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
# MIDDLEWARE
# ---------------------------------------------------------------------------

@app.before_request
def make_session_permanent():
    """Maintains user Discord login sessions."""
    session.permanent = True


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
# MAIN ROUTES
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Serves the primary UI container."""
    return render_template('index.html')


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "online"}), 200


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

        session.permanent = True
        session['user'] = {
            'id': user_id,
            'username': username,
            'global_name': global_name,
            'avatar': avatar,
            'avatar_url': avatar_url
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
    """Client-side authentication status verification endpoint."""
    try:
        user_data = session.get('user')
        if user_data:
            return jsonify({"authenticated": True, "user": user_data}), 200

        return jsonify({"authenticated": False, "user": None}), 200

    except Exception as e:
        logger.error("Error encountered in auth_me route: %s", e)
        return jsonify({"authenticated": False, "error": "Internal state error"}), 500


# ---------------------------------------------------------------------------
# API: LOGGING & GENERATION
# ---------------------------------------------------------------------------

@app.route('/api/log_entry', methods=['POST'])
def log_entry():
    """Logs user profile access to telemetry database and webhooks."""
    user = session.get('user', {})
    if not user:
        return jsonify({"status": "ignored"}), 200

    user_id = user.get('id', 'Unknown')
    username = user.get('username', 'Unknown')

    if telemetry_collection is not None:
        telemetry_collection.insert_one({
            "user_id": user_id,
            "global_name": user.get('global_name'),
            "user_agent": request.headers.get("User-Agent", "Unknown"),
            "logged_at": time.time()
        })

    fields = [
        {"name": "User", "value": f"@{username} (`{user_id}`)", "inline": True}
    ]

    send_system_webhook(
        title="📥 Website Entry Log",
        description=f"User <@{user_id}> accessed the site.",
        fields=fields,
        color=0x3498DB
    )
    return jsonify({"status": "logged"}), 200


@app.route('/api/verify_and_generate', methods=['POST'])
def verify_and_generate():
    """
    Verifies CAPTCHA, 24-hour invite link, guild match, Admin perms,
    and triggers Gemini AI layout generation.
    """
    data = request.json or {}
    captcha_token = data.get('captcha_token', '').strip()

    # 1. Verify CAPTCHA
    if not verify_turnstile_captcha(captcha_token, request.remote_addr):
        return jsonify({"success": False, "message": "Security CAPTCHA verification failed."}), 400

    user_data = session.get('user')
    if not user_data:
        return jsonify({"success": False, "message": "Unauthorized. Please login with Discord."}), 401

    user_id = str(user_data.get('id', '')).strip()

    # 2. Extract payload
    server_id = str(data.get('server_id', '')).strip()
    server_invite = data.get('server_link', '').strip()
    prompt = data.get('prompt', '').strip()
    separator = data.get('separator', '-')

    if not server_id or not server_invite or not prompt:
        return jsonify({"success": False, "message": "Missing server ID, invite link, or prompt."}), 400

    invite_code = extract_invite_code(server_invite)

    # 3. Verify server link, server ID, duration, & Admin permissions
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

    # 4. Gemini Layout Generation using google-genai SDK (`gemini-2.5-flash`)
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
