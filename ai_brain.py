import os
import json
import io
import time
import logging
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, session, url_for
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# Secret key for Flask session signing
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-change-this-in-production")

# Discord OAuth2 Configuration
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_API_BASE_URL = "https://discord.com/api/v10"

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


def send_system_log(title: str, description: str, color: int = 0x3B82F6, fields: list = None):
    """Sends a standard system/activity log embed to the dedicated system log channel."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        logging.warning("SYSTEM_LOG_WEBHOOK_URL not configured. Skipping system log.")
        return

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields or [],
                "footer": {"text": "ORCA System Logger"}
            }
        ]
    }

    try:
        requests.post(
            SYSTEM_LOG_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
    except Exception as e:
        logging.error(f"Failed to post system log webhook: {e}")


def save_blueprint_data(guild_id: str, blueprint: dict):
    """Saves blueprint data mapped to its target Guild ID."""
    BLUEPRINT_STORAGE[guild_id] = blueprint
    file_path = os.path.join(BLUEPRINT_DIR, f"{guild_id}.json")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save blueprint file for guild {guild_id}: {e}")


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
        except Exception as e:
            logging.error(f"Failed to read blueprint file for guild {guild_id}: {e}")
            
    return None


def generate_tier_3_fallback(prompt: str, guild_id: str, server_link: str, separator: str, user: dict) -> dict:
    """
    TIER 3 FALLBACK: Local dynamic template generation.
    Used when both Primary and Secondary remote AI endpoints fail or hit 503 limits.
    """
    clean_prompt = prompt.strip()[:25] if prompt else "Community"
    return {
        "server_name": f"ORCA — {clean_prompt.title()} Server",
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
# DISCORD OAUTH2 AUTHENTICATION ROUTES
# ==========================================

@app.route('/api/auth/discord/login')
def discord_login():
    """Redirects the user to Discord OAuth2 authorization URL."""
    redirect_uri = f"{WEB_BUILDER_URL}/api/auth/discord/callback"
    
    oauth_url = (
        f"{DISCORD_API_BASE_URL}/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={requests.utils.quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope=identify"
    )
    logging.info(f"Initiating OAuth2 authorization with Redirect URI: {redirect_uri}")
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

        # Save identity in session cookie
        session['user'] = {
            'id': user_profile.get('id'),
            'username': user_profile.get('username'),
            'avatar': user_profile.get('avatar'),
            'discriminator': user_profile.get('discriminator')
        }

        # Send activity alert to System Log Webhook
        send_system_log(
            title="🔑 User Authenticated",
            description=f"User **@{user_profile.get('username')}** (`{user_profile.get('id')}`) logged into the web dashboard.",
            color=0x3B82F6
        )

        return redirect('/')
    except Exception as e:
        logging.error(f"OAuth2 authentication failure: {e}")
        return "Authentication failed. Please check your credentials and try again.", 500


@app.route('/api/auth/me')
def get_current_user():
    """Returns details of the currently authenticated session."""
    user = session.get('user')
    if user:
        return jsonify({"authenticated": True, "user": user})
    return jsonify({"authenticated": False, "user": None})


# ==========================================
# PAGE & BLUEPRINT ROUTES
# ==========================================

@app.route('/')
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
    guild_id = data.get('guild_id', '')
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
            logging.info("[ORCA AI] Executing Tier 1 generation (gemini-2.5-flash)...")
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
            logging.info("[ORCA AI] Tier 1 layout generation succeeded.")
        except Exception as e1:
            logging.warning(f"[ORCA AI] Tier 1 Failed ({e1}). Escalating to Tier 2...")

    # --- TIER 2: SECONDARY / LIGHTWEIGHT BACKUP MODEL (gemini-1.5-flash) ---
    if not layout_data and client:
        try:
            time.sleep(0.5)  # Backoff delay before hit to secondary endpoint
            logging.info("[ORCA AI] Executing Tier 2 generation (gemini-1.5-flash)...")
            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents=full_user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.3,
                )
            )
            layout_data = json.loads(response.text)
            logging.info("[ORCA AI] Tier 2 layout generation succeeded.")
        except Exception as e2:
            logging.warning(f"[ORCA AI] Tier 2 Failed ({e2}). Escalating to Tier 3...")

    # --- TIER 3: DYNAMIC HARDCODED FALLBACK ---
    if not layout_data:
        logging.info("[ORCA AI] Applying Tier 3 dynamic hardcoded fallback layout.")
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
                    "footer": {"text": "ORCA AI Automated Server Infrastructure"}
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
            logging.info(f"Design Webhook Response Status: {log_res.status_code}")
        except Exception as e:
            logging.error(f"Failed to post embed + file to design webhook: {e}")
    else:
        logging.warning("DESIGN_WEBHOOK_URL environment variable is not set!")

    return jsonify({
        "status": "success", 
        "message": "Blueprint submitted and logged successfully", 
        "file_url": file_url
    }), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
