import io
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import Flask, jsonify, redirect, render_template, request, session
from google import genai
from google.genai import types
from pymongo import MongoClient
import requests

# ---------------------------------------------------------------------------
# LOGGING CONFIGURATION
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("ai_brain")

app = Flask(__name__)

# Basic session configuration
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent-2026")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# Discord OAuth2 Configurations
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# Google reCAPTCHA Configuration
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe").strip()

# Base Web Builder URL
WEB_BUILDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com").strip().rstrip('/')

# Webhook Configurations
DESIGN_WEBHOOK_URL = os.environ.get("DESIGN_WEBHOOK_URL", os.environ.get("WEBHOOK_URL", "")).strip()
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", os.environ.get("SYSTEM_LOG_WEBHOOK_URL", DESIGN_WEBHOOK_URL)).strip()

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
        logger.info("[INIT] Google GenAI client initialized successfully.")
    except Exception as e:
        logger.error("[INIT ERROR] Failed to initialize Google GenAI client: %s", e, exc_info=True)
else:
    logger.warning("[INIT WARNING] GEMINI_API_KEY missing. Fallback defaults will be used.")

# ---------------------------------------------------------------------------
# MONGODB DATABASE CONNECTION
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None
db = mongo_client["bot_database"] if mongo_client is not None else None
designs_collection = db["designs"] if db is not None else None


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def send_discord_webhook(webhook_url: str, payload: dict):
    if not webhook_url:
        return
    try:
        res = requests.post(webhook_url, json=payload, timeout=5)
        logger.info("[WEBHOOK LOG] Dispatched alert to Discord. Status: %d", res.status_code)
    except Exception as e:
        logger.error("[WEBHOOK ERROR] Failed to dispatch webhook alert: %s", e)


def get_blueprint_data(guild_id: str = None) -> dict:
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
            logger.error("[BLUEPRINT GET ERROR] %s", e)

    if designs_collection is not None:
        try:
            doc = designs_collection.find_one({"guild_id": guild_id}, sort=[("submitted_at", -1)], projection={"_id": 0})
            if doc:
                BLUEPRINT_STORAGE[guild_id] = doc
                return doc
        except Exception as e:
            logger.error("[BLUEPRINT GET ERROR] MongoDB error: %s", e)

    return {}


def save_blueprint_data(guild_id: str, blueprint: dict):
    guild_id = str(guild_id).strip()
    BLUEPRINT_STORAGE[guild_id] = blueprint
    file_path = os.path.join(BLUEPRINT_DIR, f"{guild_id}.json")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, indent=2)
    except IOError as e:
        logger.error("[BLUEPRINT SAVE ERROR] %s", e)

    if designs_collection is not None:
        try:
            doc = dict(blueprint)
            doc["guild_id"] = guild_id
            doc["submitted_at"] = time.time()
            designs_collection.update_one({"guild_id": guild_id}, {"$set": doc}, upsert=True)
        except Exception as e:
            logger.error("[BLUEPRINT SAVE ERROR] MongoDB update error: %s", e)


def verify_google_recaptcha(token: str) -> bool:
    if not token or token == "YOUR_RECAPTCHA_SITE_KEY":
        return True
    try:
        res = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": RECAPTCHA_SECRET_KEY, "response": token},
            timeout=5
        )
        return res.json().get("success", False)
    except Exception as e:
        logger.error("[RECAPTCHA ERROR] Verification call failed: %s", e)
        return True


# ---------------------------------------------------------------------------
# MIDDLEWARE & ENDPOINTS
# ---------------------------------------------------------------------------

@app.before_request
def make_session_permanent():
    session.permanent = True


@app.route('/api/verify-server', methods=['POST'])
def verify_server():
    data = request.get_json() or {}
    captcha_token = data.get("captcha_token", "").strip()
    if captcha_token and not verify_google_recaptcha(captcha_token):
        return jsonify({"valid": False, "error": "Google reCAPTCHA verification failed."}), 400
    return jsonify({"valid": True, "message": "Verification successful."})


@app.route('/api/log-entry', methods=['POST'])
def log_entry():
    data = request.get_json() or {}
    user = session.get('user')
    user_name = user.get('username') if user else 'Guest / Anonymous'
    path = data.get('path', '/')
    event_type = data.get('event', 'Page Entry')

    logger.info("[SITE ENTRY] Event: %s | User: %s | Path: %s", event_type, user_name, path)

    send_discord_webhook(
        SYSTEM_LOG_WEBHOOK_URL,
        {
            "embeds": [
                {
                    "title": "🌐 Website Entry Logged",
                    "description": f"**Event:** `{event_type}`\n**User:** `{user_name}`\n**Path Visited:** `{path}`",
                    "color": 0x3498DB,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            ]
        }
    )
    return jsonify({"logged": True})


# ---------------------------------------------------------------------------
# DISCORD OAUTH2 ROUTES
# ---------------------------------------------------------------------------

@app.route('/api/auth/discord/login')
def discord_login():
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

        user_res = requests.get(
            f"{DISCORD_API_BASE_URL}/users/@me",
            headers={'Authorization': f"Bearer {tokens.get('access_token')}"},
            timeout=10
        )
        user_res.raise_for_status()
        user_profile = user_res.json()

        user_id = str(user_profile.get('id')).strip()
        username = user_profile.get('username')
        avatar = user_profile.get('avatar')

        session.permanent = True
        session['user'] = {
            'id': user_id,
            'username': username,
            'avatar_url': f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
        }
        return redirect('/')
    except Exception as e:
        logger.error("[OAUTH FAILURE] %s", e, exc_info=True)
        return "Authentication failed.", 500


@app.route('/api/auth/logout')
def discord_logout():
    session.pop('user', None)
    return redirect('/')


@app.route('/api/auth/me')
def get_current_user():
    user = session.get('user')
    return jsonify({"authenticated": bool(user), "user": user})


# ---------------------------------------------------------------------------
# AI GENERATION & DESIGN SUBMISSION
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/blueprint/<guild_id>.json', methods=['GET'])
def serve_blueprint(guild_id):
    data = get_blueprint_data(guild_id)
    if data:
        return jsonify(data)
    return send_from_directory(BLUEPRINT_DIR, f"{guild_id}.json", mimetype='application/json')


@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    user = session.get('user')
    data = request.get_json() or {}
    prompt = data.get('prompt', 'Community Discord Server')
    guild_id = str(data.get('guild_id', '')).strip()
    server_link = data.get('server_link', '').strip()
    separator = data.get('separator', '|').strip() or '|'
    categories_count = data.get('categories_count', 4)
    channels_count = data.get('channels_count', 12)

    logger.info("[AI GENERATE] User: %s | Prompt: %s | Separator: %s", user, prompt, separator)

    system_instruction = (
        "You are an expert Discord server architect. Generate a creative, detailed Discord server template as a raw JSON object.\n"
        "Strict Requirements:\n"
        "1. Include server roles and organized category lists.\n"
        "2. Keep channel 'name' as clean text (e.g., 'rules' or 'announcements'). Do not insert separators inside the name itself.\n"
        "3. Provide relevant emojis for every channel.\n"
        "Output Schema JSON strictly:\n"
        "{\n"
        '  "server_name": "String",\n'
        '  "roles": ["String"],\n'
        '  "categories": [\n'
        '    {\n'
        '      "name": "CATEGORY NAME",\n'
        '      "channels": [\n'
        '        {"emoji": "📌", "name": "rules", "type": "text|voice|announcement", "topic": "Description"}\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        '}'
    )

    prompt_payload = (
        f"Design a complete server layout based on this theme: '{prompt}'.\n"
        f"Required Target Categories Count: {categories_count}\n"
        f"Required Target Channels Count: {channels_count}"
    )

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
            logger.error("[GEMINI ERROR] %s", e, exc_info=True)

    if not layout_data or "categories" not in layout_data:
        layout_data = {
            "server_name": f"{prompt.title()} Community",
            "roles": ["Admin", "Moderator", "VIP", "Member"],
            "categories": [
                {
                    "name": "📌 INFORMATION",
                    "channels": [
                        {"emoji": "📌", "name": "rules", "type": "text", "topic": "Server guidelines"},
                        {"emoji": "📢", "name": "announcements", "type": "announcement", "topic": "Official updates"}
                    ]
                },
                {
                    "name": "💬 GENERAL CHATS",
                    "channels": [
                        {"emoji": "💬", "name": "general", "type": "text", "topic": "Main chat room"},
                        {"emoji": "🔊", "name": "lounge", "type": "voice", "topic": "General voice chat"}
                    ]
                }
            ]
        }

    # Format Channel Names as "Emoji | Channel Name" (e.g. 📌 | rules)
    for cat in layout_data.get("categories", []):
        for ch in cat.get("channels", []):
            raw_name = ch.get("name", "channel").lower().strip()
            emoji = ch.get("emoji", "").strip()
            if emoji:
                ch["formatted_name"] = f"{emoji} {separator} {raw_name}"
            else:
                ch["formatted_name"] = raw_name

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

    send_discord_webhook(
        DESIGN_WEBHOOK_URL,
        {
            "embeds": [
                {
                    "title": f"📥 Blueprint Submitted — Guild #{target_guild}",
                    "description": f"**Server Name:** {blueprint.get('server_name', 'Custom Server')}\n**Deploy Command:** `/build file:{file_url}`",
                    "color": 0x2ECC71,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            ]
        }
    )

    return jsonify({"status": "success", "file_url": file_url})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
