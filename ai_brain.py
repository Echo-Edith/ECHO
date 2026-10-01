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

# Webhook Configuration
DESIGN_WEBHOOK_URL = os.environ.get("DESIGN_WEBHOOK_URL", os.environ.get("WEBHOOK_URL", "")).strip()

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
        logger.info("[INIT] Google GenAI client initialized successfully with API key.")
    except Exception as e:
        logger.error("[INIT ERROR] Failed to initialize Google GenAI client: %s", e, exc_info=True)
else:
    logger.warning("[INIT WARNING] GEMINI_API_KEY is missing. AI layout generation will use fallback defaults.")

# ---------------------------------------------------------------------------
# MONGODB DATABASE CONNECTION
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
mongo_client = None
db = None
designs_collection = None

if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI)
        db = mongo_client["bot_database"]
        designs_collection = db["designs"]
        logger.info("[INIT] MongoDB connection established successfully.")
    except Exception as e:
        logger.error("[INIT ERROR] MongoDB connection failed: %s", e, exc_info=True)
else:
    logger.warning("[INIT WARNING] No MONGO_URI found. Blueprint persistence will rely on local filesystem.")


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def get_blueprint_data(guild_id: str = None) -> dict:
    """Retrieves stored blueprint data for a given guild_id."""
    if not guild_id:
        logger.info("[BLUEPRINT GET] No guild_id provided; returning full in-memory cache (%d items).", len(BLUEPRINT_STORAGE))
        return BLUEPRINT_STORAGE

    guild_id = str(guild_id).strip()
    logger.info("[BLUEPRINT GET] Requesting blueprint for Guild ID: %s", guild_id)

    if guild_id in BLUEPRINT_STORAGE:
        logger.info("[BLUEPRINT GET] Found in-memory cache hit for Guild ID: %s", guild_id)
        return BLUEPRINT_STORAGE[guild_id]

    file_path = os.path.join(BLUEPRINT_DIR, f"{guild_id}.json")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                BLUEPRINT_STORAGE[guild_id] = data
                logger.info("[BLUEPRINT GET] Loaded blueprint from disk file: %s", file_path)
                return data
        except Exception as e:
            logger.error("[BLUEPRINT GET ERROR] Reading blueprint file on disk failed for Guild ID %s: %s", guild_id, e, exc_info=True)
    
    if designs_collection is not None:
        try:
            doc = designs_collection.find_one({"guild_id": guild_id}, sort=[("submitted_at", -1)], projection={"_id": 0})
            if doc:
                BLUEPRINT_STORAGE[guild_id] = doc
                logger.info("[BLUEPRINT GET] Retrieved blueprint from MongoDB for Guild ID: %s", guild_id)
                return doc
        except Exception as e:
            logger.error("[BLUEPRINT GET ERROR] MongoDB query error for Guild ID %s: %s", guild_id, e, exc_info=True)

    logger.warning("[BLUEPRINT GET MISS] No blueprint found on disk, cache, or MongoDB for Guild ID: %s", guild_id)
    return {}


def save_blueprint_data(guild_id: str, blueprint: dict):
    """Persists blueprint data both to local JSON files and MongoDB."""
    guild_id = str(guild_id).strip()
    logger.info("[BLUEPRINT SAVE] Persisting blueprint for Guild ID: %s (Keys: %s)", guild_id, list(blueprint.keys()))
    
    BLUEPRINT_STORAGE[guild_id] = blueprint
    file_path = os.path.join(BLUEPRINT_DIR, f"{guild_id}.json")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, indent=2)
        logger.info("[BLUEPRINT SAVE] Saved blueprint to local disk path: %s", file_path)
    except IOError as e:
        logger.error("[BLUEPRINT SAVE ERROR] Failed to save local file for Guild ID %s: %s", guild_id, e, exc_info=True)

    if designs_collection is not None:
        try:
            doc = dict(blueprint)
            doc["guild_id"] = guild_id
            doc["submitted_at"] = time.time()
            res = designs_collection.update_one({"guild_id": guild_id}, {"$set": doc}, upsert=True)
            logger.info("[BLUEPRINT SAVE] Saved to MongoDB for Guild ID %s (Matched: %d, Modified: %d, UpsertedId: %s)",
                        guild_id, res.matched_count, res.modified_count, res.upserted_id)
        except Exception as e:
            logger.error("[BLUEPRINT SAVE ERROR] Failed to update MongoDB for Guild ID %s: %s", guild_id, e, exc_info=True)


def verify_google_recaptcha(token: str) -> bool:
    """Verifies CAPTCHA token with Google reCAPTCHA API (without logging IP address)."""
    if not token or token == "YOUR_RECAPTCHA_SITE_KEY":
        logger.warning("[RECAPTCHA] Development fallback triggered or missing token.")
        return True

    logger.info("[RECAPTCHA] Verifying reCAPTCHA token against Google API...")
    try:
        res = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": RECAPTCHA_SECRET_KEY,
                "response": token
            },
            timeout=5
        )
        data = res.json()
        success = data.get("success", False)
        score = data.get("score")
        action = data.get("action")
        hostname = data.get("hostname")
        error_codes = data.get("error-codes", [])

        logger.info(
            "[RECAPTCHA VERIFY] Success: %s | Score: %s | Action: %s | Hostname: %s | Errors: %s",
            success, score, action, hostname, error_codes
        )
        return success
    except Exception as e:
        logger.error("[RECAPTCHA ERROR] Google API verification call failed: %s", e, exc_info=True)
        return True


def get_discord_creation_time(user_id: str) -> datetime:
    """Calculates Discord account creation timestamp from snowflake ID."""
    try:
        snowflake = int(user_id)
        timestamp = ((snowflake >> 22) + 1420070400000) / 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except Exception as e:
        logger.error("[SNOWFLAKE ERROR] Failed to parse snowflake ID %s: %s", user_id, e)
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# MIDDLEWARE
# ---------------------------------------------------------------------------

@app.before_request
def make_session_permanent():
    session.permanent = True


# ---------------------------------------------------------------------------
# FRONTEND API ENDPOINTS
# ---------------------------------------------------------------------------

@app.route('/api/verify-server', methods=['POST'])
def verify_server():
    """Verifies Google reCAPTCHA token without capturing IP address."""
    data = request.get_json() or {}
    captcha_token = data.get("captcha_token", "").strip()

    user_info = session.get('user', {}).get('username', 'Anonymous')
    logger.info("[API /verify-server] Received verification request from User: %s", user_info)

    if captcha_token and not verify_google_recaptcha(captcha_token):
        logger.warning("[API /verify-server] Verification rejected for User: %s due to failed Google reCAPTCHA.", user_info)
        return jsonify({"valid": False, "error": "Google reCAPTCHA verification failed."}), 400

    logger.info("[API /verify-server] Verification succeeded for User: %s", user_info)
    return jsonify({"valid": True, "message": "Verification successful."})


@app.route('/api/log-entry', methods=['POST'])
def log_entry():
    """Detailed site entry logger without capturing IP addresses."""
    data = request.get_json() or {}
    user_agent = request.headers.get('User-Agent', 'Unknown')
    referrer = request.headers.get('Referer', 'Direct/None')
    user = session.get('user')
    user_id = user.get('id') if user else 'Unauthenticated'
    username = user.get('username') if user else 'Guest'

    path = data.get('path', '/')
    event_type = data.get('event', 'page_view')

    logger.info(
        "[SITE ENTRY LOG] Event: %s | User: %s (ID: %s) | Path: %s | Referrer: %s | User-Agent: %s | Details: %s",
        event_type, username, user_id, path, referrer, user_agent, data
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
    logger.info("[OAUTH LOGIN] Initiating Discord OAuth login. Redirect URI: %s", redirect_uri)
    return redirect(oauth_url)


@app.route('/api/auth/discord/callback')
def discord_callback():
    """Handles OAuth2 authorization code exchange and logs detailed progress."""
    code = request.args.get('code')
    if not code:
        logger.error("[OAUTH CALLBACK ERROR] Callback reached without code parameter.")
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

    logger.info("[OAUTH CALLBACK] Exchanging code for access token...")
    try:
        token_res = requests.post(f"{DISCORD_API_BASE_URL}/oauth2/token", data=token_data, headers=headers, timeout=10)
        token_res.raise_for_status()
        tokens = token_res.json()
        access_token = tokens.get('access_token')

        logger.info("[OAUTH CALLBACK] Token exchange successful. Fetching user profile from /users/@me...")
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

        session.permanent = True
        session['user'] = {
            'id': user_id,
            'username': username,
            'global_name': global_name,
            'avatar': avatar,
            'avatar_url': avatar_url,
            'created_at': created_at.strftime('%Y-%m-%d %H:%M:%S UTC')
        }

        logger.info("[OAUTH SUCCESS] Authenticated User: %s (ID: %s) | Account Created: %s", username, user_id, created_at)
        return redirect('/')
    except Exception as e:
        logger.error("[OAUTH FAILURE] OAuth authentication failed: %s", e, exc_info=True)
        return "Authentication failed.", 500


@app.route('/api/auth/logout')
def discord_logout():
    user = session.pop('user', None)
    if user:
        logger.info("[OAUTH LOGOUT] User logged out: %s (ID: %s)", user.get('username'), user.get('id'))
    else:
        logger.info("[OAUTH LOGOUT] Logout endpoint called by unauthenticated session.")
    return redirect('/')


@app.route('/api/auth/me')
def get_current_user():
    user = session.get('user')
    if user:
        logger.info("[AUTH CHECK] Session active for User: %s (ID: %s)", user.get('username'), user.get('id'))
        return jsonify({"authenticated": True, "user": user})

    logger.info("[AUTH CHECK] Session unauthenticated.")
    return jsonify({"authenticated": False, "user": None})


# ---------------------------------------------------------------------------
# PAGE RENDERING & AI GENERATION ROUTES
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    user = session.get('user', {})
    logger.info("[PAGE LOAD] Rendering index.html for User: %s", user.get('username', 'Anonymous'))
    return render_template('index.html')


@app.route('/blueprint/<guild_id>.json', methods=['GET'])
def serve_blueprint(guild_id):
    """Serves the generated layout JSON."""
    logger.info("[BLUEPRINT SERVE] Request for blueprint file: %s.json", guild_id)
    data = get_blueprint_data(guild_id)
    if data:
        return jsonify(data)
    return send_from_directory(BLUEPRINT_DIR, f"{guild_id}.json", mimetype='application/json')


@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    user = session.get('user')
    user_name = user.get('username') if user else 'Anonymous'

    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    guild_id = str(data.get('guild_id', '')).strip()
    server_link = data.get('server_link', '').strip()
    separator = data.get('separator', '-')
    categories_count = data.get('categories_count')
    channels_count = data.get('channels_count')

    logger.info(
        "[GENERATE LAYOUT] Request received | User: %s | Guild ID: %s | Prompt: '%s' | Categories Requested: %s | Channels Requested: %s | Separator: '%s'",
        user_name, guild_id, prompt, categories_count, channels_count, separator
    )

    system_instruction = (
        "Generate a raw JSON layout for a Discord server based on user prompt conforming strictly to this schema:\n"
        "{\"server_name\": \"String\", \"roles\": [\"String\"], \"categories\": [{\"name\": \"String\", \"channels\": [{\"emoji\": \"💬\", \"name\": \"string\", \"type\": \"text|voice|announcement\", \"topic\": \"string\"}]}]}"
    )

    prompt_payload = f"Guild ID: {guild_id}\nPrompt: {prompt}"
    if categories_count is not None and str(categories_count).isdigit():
        prompt_payload += f"\nPreferred Categories Count: {categories_count}"
    if channels_count is not None and str(channels_count).isdigit():
        prompt_payload += f"\nPreferred Total Channels Count: {channels_count}"

    layout_data = None
    start_time = time.time()

    if client:
        try:
            logger.info("[GENERATE LAYOUT AI] Sending request to Gemini 2.5 Flash model...")
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt_payload,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json"
                )
            )
            elapsed = time.time() - start_time
            layout_data = json.loads(response.text)
            logger.info("[GENERATE LAYOUT AI SUCCESS] Generated layout in %.2fs. Roles: %d, Categories: %d",
                        elapsed, len(layout_data.get('roles', [])), len(layout_data.get('categories', [])))
        except Exception as e:
            logger.warning("[GENERATE LAYOUT AI ERROR] Generation via Gemini API failed: %s", e, exc_info=True)

    if not layout_data:
        logger.info("[GENERATE LAYOUT FALLBACK] Using standard template defaults.")
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
        logger.warning("[SUBMIT DESIGN REJECTED] No blueprint JSON body provided.")
        return jsonify({"error": "No blueprint provided"}), 400

    target_guild = str(blueprint.get("target_guild_id", "Unknown")).strip()
    creator = blueprint.get("creator", {})
    creator_name = creator.get("username", "Anonymous") if creator else "Anonymous"

    logger.info("[SUBMIT DESIGN] Processing blueprint submission for Guild ID: %s by User: %s", target_guild, creator_name)

    save_blueprint_data(target_guild, blueprint)

    file_url = f"{WEB_BUILDER_URL}/blueprint/{target_guild}.json"

    if DESIGN_WEBHOOK_URL:
        logger.info("[SUBMIT DESIGN WEBHOOK] Dispatching submission notification to Discord webhook...")
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
            resp = requests.post(DESIGN_WEBHOOK_URL, data={"payload_json": json.dumps(payload)}, files=files, timeout=10)
            logger.info("[SUBMIT DESIGN WEBHOOK] Webhook responded with status: %d", resp.status_code)
        except Exception as e:
            logger.error("[SUBMIT DESIGN WEBHOOK ERROR] Webhook call failed: %s", e, exc_info=True)

    logger.info("[SUBMIT DESIGN SUCCESS] Blueprint ready for deployment at: %s", file_url)
    return jsonify({"status": "success", "file_url": file_url})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    logger.info("[SERVER START] Launching Flask server on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port)
