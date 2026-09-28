import os
import json
import io
import time
import logging
import requests
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, session
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Secret key for Flask session signing
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-change-this-in-production")

# Discord OAuth2 Configuration
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# Bot API Auth Key for internal bot-to-web API security
BOT_API_KEY = os.environ.get("BOT_API_KEY", "super-secret-bot-key").strip()

# Storage directory setup
BLUEPRINT_STORAGE = {}
BLUEPRINT_DIR = os.path.join(os.getcwd(), "blueprints")
os.makedirs(BLUEPRINT_DIR, exist_ok=True)

# Webhook Configurations
DESIGN_WEBHOOK_URL = os.environ.get("DESIGN_WEBHOOK_URL", os.environ.get("WEBHOOK_URL", "")).strip()
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", "").strip()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# Base domain cleanup (removes trailing slash to keep redirect URIs exact)
WEB_BUILDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com").strip().rstrip('/')

client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# WEBSITE SECURITY & ACCESS CONTROL STATE
# ==========================================
# In-memory storage for banned Discord User IDs
BANNED_USER_IDS = set()

# Global Lockdown Flag (True = Dashboard restricted to authorized admins)
IS_LOCKDOWN_ACTIVE = False

# Hardcoded Primary Owner ID exempted from website lockdown restrictions
PRIMARY_OWNER_ID = "1219266886143967245"


@app.before_request
def enforce_security_middleware():
    """Middleware enforcing site bans and lockdown restrictions prior to handling requests."""
    # Exempt essential static resources and OAuth login callback routes
    exempt_endpoints = {'static', 'discord_login', 'discord_callback', 'handle_ban_user', 'handle_lockdown', 'get_current_user'}
    if request.endpoint in exempt_endpoints:
        return None

    user = session.get('user')
    user_id = str(user.get('id')).strip() if user else None

    # 1. ENFORCE WEBSITE BANS
    if user_id and user_id in BANNED_USER_IDS:
        session.pop('user', None)  # Wipe session
        if request.path.startswith('/api/'):
            return jsonify({"error": "Access Denied. You are banned from utilizing the Echo Studio Portal.", "is_banned": True}), 403
        return render_template('index.html'), 200

    # 2. ENFORCE GLOBAL LOCKDOWN
    if IS_LOCKDOWN_ACTIVE:
        if not user_id or user_id != PRIMARY_OWNER_ID:
            if request.path.startswith('/api/'):
                return jsonify({"error": "System Under Maintenance. The dashboard is currently locked.", "is_lockdown": True}), 530
            return render_template('index.html'), 200


def send_system_log(title: str, description: str, color: int = 0x3B82F6, fields: list = None):
    """Sends a standard system/activity log embed to the dedicated system log channel."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        logger.warning("SYSTEM_LOG_WEBHOOK_URL not configured. Skipping system log.")
        return

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields or [],
                "footer": {"text": "Echo Studio System Logger"}
            }
        ]
    }

    try:
        response = requests.post(
            SYSTEM_LOG_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to post system log webhook: %s", e)


def save_blueprint_data(guild_id: str, blueprint: dict):
    """Saves blueprint data mapped to its target Guild ID."""
    guild_id = str(guild_id).strip()
    BLUEPRINT_STORAGE[guild_id] = blueprint
    file_path = os.path.join(BLUEPRINT_DIR, f"{guild_id}.json")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, indent=2)
    except IOError as e:
        logger.error("Failed to save blueprint file for guild %s: %s", guild_id, e)


def get_blueprint_data(guild_id: str):
    """Retrieves saved blueprint data using target Guild ID."""
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
        except (IOError, json.JSONDecodeError) as e:
            logger.error("Failed to read blueprint file for guild %s: %s", guild_id, e)
            
    return None


def generate_tier_3_fallback(prompt: str, guild_id: str, server_link: str, separator: str, user: dict) -> dict:
    """
    TIER 3 FALLBACK: Local dynamic template generation.
    Used when primary and secondary remote AI endpoints fail or hit quota limits.
    """
    clean_prompt = prompt.strip()[:25] if prompt else "Community"
    return {
        "server_name": f"Echo Studio — {clean_prompt.title()} Server",
        "target_guild_id": guild_id,
        "server_link": server_link,
        "separator": separator,
        "creator": user,
        "roles": ["Administrator", "Moderator", "VIP Member", "Member"],
        "categories": [
            {
                "name": "📌 INFORMATION",
                "channels": [
                    {"emoji": "👋", "name": f"welcome{separator}info", "type": "text", "topic": "Welcome to the server!"},
                    {"emoji": "📜", "name": f"rules{separator}guidelines", "type": "text", "topic": "Server rules"},
                    {"emoji": "📢", "name": "announcements", "type": "announcement", "topic": "Official updates"}
                ]
            },
            {
                "name": "💬 COMMUNITY HUB",
                "channels": [
                    {"emoji": "💬", "name": f"general{separator}chat", "type": "text", "topic": "Main conversation area"},
                    {"emoji": "🤖", "name": f"bot{separator}commands", "type": "text", "topic": "Execute commands here"}
                ]
            },
            {
                "name": "🎙️ VOICE LOUNGES",
                "channels": [
                    {"emoji": "🔊", "name": "General Lounge", "type": "voice", "topic": ""},
                    {"emoji": "🎮", "name": "Gaming Lounge", "type": "voice", "topic": ""}
                ]
            }
        ]
    }


# ==========================================
# BOT SECURITY CONTROL ENDPOINTS
# ==========================================

@app.route('/api/security/ban', methods=['POST'])
def handle_ban_user():
    """API endpoint called by bot moderation (/ban) to ban/unban users on the website."""
    auth_header = request.headers.get("X-Bot-Auth")
    if auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized endpoint access."}), 401

    data = request.get_json() or {}
    target_user_id = str(data.get("user_id", "")).strip()
    action = data.get("action", "ban").lower()

    if not target_user_id:
        return jsonify({"error": "Missing user_id parameter."}), 400

    if action == "ban":
        BANNED_USER_IDS.add(target_user_id)
        logger.info("[SECURITY] User ID %s was banned from website access.", target_user_id)
        return jsonify({"status": "success", "message": f"User {target_user_id} banned from portal."}), 200

    BANNED_USER_IDS.discard(target_user_id)
    logger.info("[SECURITY] User ID %s unbanned from website access.", target_user_id)
    return jsonify({"status": "success", "message": f"User {target_user_id} unbanned."}), 200


@app.route('/api/security/lockdown', methods=['POST'])
def handle_lockdown():
    """API endpoint called by bot management (/lockdown) to toggle website maintenance mode."""
    auth_header = request.headers.get("X-Bot-Auth")
    if auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized endpoint access."}), 401

    global IS_LOCKDOWN_ACTIVE
    data = request.get_json() or {}
    enable_lockdown = data.get("enable", True)

    IS_LOCKDOWN_ACTIVE = bool(enable_lockdown)
    status_str = "ENABLED" if IS_LOCKDOWN_ACTIVE else "DISABLED"
    logger.info("[SECURITY] Website maintenance lockdown is now %s.", status_str)

    return jsonify({
        "status": "success",
        "lockdown": IS_LOCKDOWN_ACTIVE,
        "message": f"Website lockdown mode has been {status_str.lower()}."
    }), 200


# ==========================================
# DISCORD OAUTH2 AUTHENTICATION ROUTES
# ==========================================

@app.route('/api/auth/discord/login')
def discord_login():
    """Redirects the user to Discord OAuth2 authorization URL."""
    redirect_uri = f"{WEB_BUILDER_URL}/api/auth/discord/callback"
    
    oauth_url = (
        f"{DISCORD_API_BASE_URL}/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope=identify"
    )
    logger.info("Initiating OAuth2 authorization with Redirect URI: %s", redirect_uri)
    return redirect(oauth_url)


@app.route('/api/auth/discord/callback')
def discord_callback():
    """Handles OAuth2 code exchange with Discord API."""
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
        # Exchange authorization code for access token
        token_res = requests.post(f"{DISCORD_API_BASE_URL}/oauth2/token", data=token_data, headers=headers, timeout=10)
        token_res.raise_for_status()
        tokens = token_res.json()
        access_token = tokens.get('access_token')

        # Retrieve user profile
        user_res = requests.get(
            f"{DISCORD_API_BASE_URL}/users/@me",
            headers={'Authorization': f"Bearer {access_token}"},
            timeout=10
        )
        user_res.raise_for_status()
        user_profile = user_res.json()

        user_id = str(user_profile.get('id')).strip()

        # Immediately reject login if user is banned
        if user_id in BANNED_USER_IDS:
            return redirect('/banned')

        # Save identity in session cookie
        session['user'] = {
            'id': user_id,
            'username': user_profile.get('username'),
            'avatar': user_profile.get('avatar'),
            'discriminator': user_profile.get('discriminator')
        }

        # Send activity alert to System Log Webhook
        send_system_log(
            title="🔑 User Authenticated",
            description=f"User **@{user_profile.get('username')}** (`{user_id}`) logged into the web dashboard.",
            color=0x3B82F6
        )

        return redirect('/')
    except requests.RequestException as e:
        logger.error("OAuth2 authentication failure: %s", e)
        return "Authentication failed. Please check your credentials and try again.", 500


@app.route('/api/auth/logout')
def discord_logout():
    """Clears user session and redirects to home."""
    session.pop('user', None)
    return redirect('/')


@app.route('/api/auth/me')
def get_current_user():
    """Returns details of the currently authenticated session, ban state, and lockdown state."""
    user = session.get('user')
    user_id = str(user.get('id')).strip() if user else None

    is_banned = bool(user_id and user_id in BANNED_USER_IDS)
    is_lockdown = bool(IS_LOCKDOWN_ACTIVE and (not user_id or user_id != PRIMARY_OWNER_ID))

    if is_banned:
        session.pop('user', None)
        return jsonify({"authenticated": False, "is_banned": True, "user": None})

    if is_lockdown:
        return jsonify({"authenticated": False, "is_lockdown": True, "user": None})

    if user:
        return jsonify({"authenticated": True, "is_banned": False, "is_lockdown": False, "user": user})
        
    return jsonify({"authenticated": False, "is_banned": False, "is_lockdown": False, "user": None})


# ==========================================
# PAGE & BLUEPRINT ROUTES
# ==========================================

@app.route('/')
@app.route('/banned')
@app.route('/maintenance')
def index():
    return render_template('index.html')


@app.route('/blueprint/<filename>')
def serve_blueprint(filename):
    """Direct URL access to download/read raw JSON blueprints."""
    if not filename.endswith('.json'):
        filename = f"{filename}.json"
    return send_from_directory(BLUEPRINT_DIR, filename, mimetype='application/json')


@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    # Require login check
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized. You must log in with Discord first."}), 401

    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    guild_id = str(data.get('guild_id', '')).strip()
    server_link = data.get('server_link', '')
    separator = data.get('separator', '-')

    if not prompt or not guild_id:
        return jsonify({"error": "Prompt and Guild ID are required."}), 400

    system_instruction = (
        "You are an expert Discord server architect. "
        "Generate a structured JSON layout for a Discord server based on the user's prompt. "
        "Return strictly raw JSON conforming to this schema:\n"
        "{\n"
        '  "server_name": "String",\n'
        '  "target_guild_id": "String",\n'
        '  "server_link": "String",\n'
        '  "separator": "String",\n'
        '  "roles": ["Role 1", "Role 2"],\n'
        '  "categories": [\n'
        '    {\n'
        '      "name": "CATEGORY NAME",\n'
        '      "channels": [\n'
        '        {\n'
        '          "emoji": "💬",\n'
        '          "name": "channel-name",\n'
        '          "type": "text|voice|announcement",\n'
        '          "topic": "Description"\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        "}"
    )

    full_user_prompt = (
        f"Target Guild ID: {guild_id}\n"
        f"Server Invite Link: {server_link}\n"
        f"Channel Separator Character: {separator}\n"
        f"Server Purpose / Theme: {prompt}"
    )

    layout_data = None

    # --- TIER 1: PRIMARY AI MODEL (gemini-2.5-flash) ---
    if client:
        try:
            logger.info("[Echo AI] Executing Tier 1 generation (gemini-2.5-flash)...")
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.3,
                )
            )
            layout_data = json.loads(response.text)
            logger.info("[Echo AI] Tier 1 layout generation succeeded.")
        except Exception as e1:
            logger.warning("[Echo AI] Tier 1 Failed (%s). Escalating to Tier 2...", e1)

    # --- TIER 2: SECONDARY AI MODEL (gemini-2.5-flash fallback / alt config) ---
    if not layout_data and client:
        try:
            time.sleep(0.5)  # Backoff delay before retry
            logger.info("[Echo AI] Executing Tier 2 generation (gemini-2.5-flash retry)...")
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.4,
                )
            )
            layout_data = json.loads(response.text)
            logger.info("[Echo AI] Tier 2 layout generation succeeded.")
        except Exception as e2:
            logger.warning("[Echo AI] Tier 2 Failed (%s). Escalating to Tier 3...", e2)

    # --- TIER 3: DYNAMIC HARDCODED FALLBACK ---
    if not layout_data:
        logger.info("[Echo AI] Applying Tier 3 dynamic hardcoded fallback layout.")
        layout_data = generate_tier_3_fallback(prompt, guild_id, server_link, separator, user)

    # Enforce standard tracking fields on payload
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
    server_link = blueprint.get("server_link", "N/A")
    server_name = blueprint.get("server_name", "Discord Server")
    categories = blueprint.get("categories", [])
    roles = blueprint.get("roles", [])
    creator = blueprint.get("creator", session.get('user', {}))
    creator_tag = f"@{creator.get('username', 'Unknown')}" if creator else "Anonymous"

    total_channels = sum(len(cat.get("channels", [])) for cat in categories)

    # Save blueprint internally using target_guild ID
    save_blueprint_data(target_guild, blueprint)

    filename = f"blueprint_{target_guild}.json"
    file_url = f"{WEB_BUILDER_URL}/blueprint/{target_guild}.json"

    if DESIGN_WEBHOOK_URL:
        # Prepare file bytes
        json_bytes = json.dumps(blueprint, indent=2).encode('utf-8')
        
        payload = {
            "embeds": [
                {
                    "title": f"📥 New Server Layout Submitted — #{target_guild}",
                    "description": (
                        "A new blueprint layout was generated and is ready for staff deployment.\n\n"
                        f"🔑 **Build Command:** `/build file: {file_url}`"
                    ),
                    "color": 0x22C55E,
                    "fields": [
                        {"name": "Submitted By", "value": f"`{creator_tag}`", "inline": True},
                        {"name": "Target Server ID", "value": f"`{target_guild}`", "inline": True},
                        {"name": "Server Name", "value": f"`{server_name}`", "inline": True},
                        {"name": "Server Invite Link", "value": f"{server_link}", "inline": False},
                        {"name": "Categories & Channels", "value": f"`{len(categories)} Categories` | `{total_channels} Channels`", "inline": True},
                        {"name": "Configured Roles", "value": f"`{len(roles)} Roles`", "inline": True}
                    ],
                    "footer": {"text": "Echo Studio Automated Server Infrastructure"}
                }
            ]
        }

        files = {
            "files[0]": (filename, io.BytesIO(json_bytes), "application/json")
        }

        try:
            log_res = requests.post(
                DESIGN_WEBHOOK_URL,
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=10
            )
            log_res.raise_for_status()
            logger.info("Design Webhook Response Status: %s", log_res.status_code)
        except requests.RequestException as e:
            logger.error("Failed to post embed + file to design webhook: %s", e)
    else:
        logger.warning("DESIGN_WEBHOOK_URL environment variable is not set!")

    return jsonify({
        "status": "success", 
        "message": "Blueprint submitted and logged successfully", 
        "file_url": file_url
    }), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
