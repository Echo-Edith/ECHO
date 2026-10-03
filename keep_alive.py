import os
import json
import requests
from datetime import datetime, timezone
from threading import Thread
from flask import Flask, redirect, request, session, render_template, jsonify
from pymongo import MongoClient
from google import genai
from google.genai import types

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key-fallback")

# Environment Variable Mapping
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", os.getenv("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com"))
REDIRECT_URI = f"{DASHBOARD_URL.rstrip('/')}/callback"
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")
RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY")
SYSTEM_LOG_WEBHOOK_URL = os.getenv("SYSTEM_LOG_WEBHOOK_URL")
MONGO_URI = os.getenv("MONGO_URI")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# Model sequence for automatic error fallback
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

# MongoDB Ban Database Setup
db_bans = None
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI)
        db = mongo_client.get_default_database()
        db_bans = db["bans"]
    except Exception as e:
        print(f"[MongoDB Warning] Could not connect to Mongo: {e}")

WEBSITE_BANS = {}

def calculate_account_age(discord_id: str) -> str:
    try:
        snowflake = int(discord_id)
        timestamp_ms = (snowflake >> 22) + 1420070400000
        created_at = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        
        days_old = (now - created_at).days
        if days_old >= 365:
            years = days_old // 365
            rem_days = days_old % 365
            return f"{years} yr{'' if years == 1 else 's'}, {rem_days} day{'' if rem_days == 1 else 's'} ({days_old} days total)"
        return f"{days_old} day{'' if days_old == 1 else 's'}"
    except Exception:
        return "Unknown"

def is_user_banned(user_id):
    user_id_str = str(user_id)
    if db_bans is not None:
        return db_bans.find_one({"user_id": user_id_str, "location": "website"})
    return WEBSITE_BANS.get(user_id_str)

def log_system_event(title: str, user_data: dict, action_desc: str = "Authenticated and accessed dashboard.", color: int = 0x8b5cf6, extra_fields: dict = None):
    if not SYSTEM_LOG_WEBHOOK_URL:
        return
    
    user_data = user_data or {}
    user_id = str(user_data.get("id", "0"))
    username = user_data.get("username", "Anonymous / Unauthenticated")
    account_age = calculate_account_age(user_id) if user_id != "0" else "Unknown"
    
    user_info_block = (
        f"**User:** <@{user_id}>\n"
        f"**Username:** `{username}`\n"
        f"**User ID:** `{user_id}`\n"
        f"**Account Age:** `{account_age}`"
    )

    fields = [
        {"name": "👤 User Information", "value": user_info_block, "inline": False},
        {"name": "📌 Activity", "value": action_desc, "inline": False}
    ]

    if extra_fields:
        for k, v in extra_fields.items():
            fields.append({
                "name": f"🔹 {k}",
                "value": f"```\n{str(v)[:1000]}\n```" if len(str(v)) > 80 else f"`{v}`",
                "inline": False
            })

    payload = {
        "username": "ORCA System Logger",
        "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
        "embeds": [{
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {"text": "Echo Studio Logging System"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }
    try:
        requests.post(SYSTEM_LOG_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[Log Error] Webhook fail: {e}")

@app.route("/")
def home():
    user = session.get("user")
    
    if user:
        ban_info = is_user_banned(user["id"])
        if ban_info:
            return render_template(
                "index.html",
                is_banned=True,
                user=user,
                dev_message=ban_info.get("message_from_dev", "Suspended by developer."),
                recaptcha_site_key=RECAPTCHA_SITE_KEY,
                discord_client_id=CLIENT_ID
            )

    return render_template(
        "index.html",
        is_banned=False,
        user=user,
        recaptcha_site_key=RECAPTCHA_SITE_KEY,
        discord_client_id=CLIENT_ID
    )

@app.route("/login")
def login():
    discord_auth_url = (
        f"{DISCORD_API_BASE_URL}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={requests.utils.quote(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20email"
    )
    return redirect(discord_auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect("/")

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    
    token_response = requests.post(f"{DISCORD_API_BASE_URL}/oauth2/token", data=data, headers=headers)
    if token_response.status_code != 200:
        return f"OAuth Error: {token_response.text}", 400

    tokens = token_response.json()
    access_token = tokens.get("access_token")

    user_headers = {"Authorization": f"Bearer {access_token}"}
    user_response = requests.get(f"{DISCORD_API_BASE_URL}/users/@me", headers=user_headers)
    
    if user_response.status_code == 200:
        user_data = user_response.json()
        avatar_hash = user_data.get("avatar")
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{avatar_hash}.png" if avatar_hash else "https://cdn.discordapp.com/embed/avatars/0.png"
        
        session["user"] = {
            "id": user_data["id"],
            "username": user_data["username"],
            "discriminator": user_data.get("discriminator", "0"),
            "avatar": avatar_url
        }

        log_system_event(
            "🌐 Website Entry Logged",
            user_data=user_data,
            action_desc="Authenticated via Discord OAuth2 and accessed the dashboard."
        )

    return redirect("/")

@app.route("/api/verify-captcha", methods=["POST"])
def verify_captcha():
    data = request.get_json() or {}
    token = data.get("token")
    if not token or not RECAPTCHA_SECRET_KEY:
        return jsonify({"success": True}), 200

    verify_res = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        data={"secret": RECAPTCHA_SECRET_KEY, "response": token}
    ).json()

    return jsonify({"success": verify_res.get("success", False)})

@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    server_id = data.get("server_id", "")

    if not prompt:
        return jsonify({"success": False, "error": "Prompt description is required."}), 400

    if not GEMINI_API_KEY:
        return jsonify({"success": False, "error": "GEMINI_API_KEY missing on server."}), 500

    client = genai.Client(api_key=GEMINI_API_KEY)

    system_instruction = (
        " You are an expert Discord architect. Output ONLY valid JSON representing a Discord server layout."
        " Required structure:\n"
        "{\n"
        '  "server_name": "String",\n'
        '  "roles": [{"name": "Role Name", "color": "#HexColor"}],\n'
        '  "categories": [\n'
        "    {\n"
        '      "name": "CATEGORY NAME",\n'
        '      "emoji": "📌",\n'
        '      "channels": [\n'
        "        {\n"
        '          "name": "channel-name",\n'
        '          "emoji": "💬",\n'
        '          "type": "text|voice|announcement",\n'
        '          "topic": "Description",\n'
        '          "read_only": false,\n'
        '          "permissions": {"view": true, "send": true, "embed": true, "attach": true}\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    last_error = None
    layout_data = None
    used_model = None

    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=f"Create layout for: {prompt}",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.7
                )
            )
            layout_data = json.loads(response.text)
            used_model = model_name
            break
        except Exception as e:
            last_error = e
            continue

    if not layout_data:
        return jsonify({
            "success": False,
            "error": f"All Gemini models experienced error. Last Error: {str(last_error)}"
        }), 503

    layout_data["build_meta"] = {
        "target_server_id": server_id,
        "prompt": prompt,
        "model_used": used_model
    }

    user = session.get("user")
    log_system_event(
        "⚡ AI Blueprint Generated",
        user_data=user,
        action_desc=f"Generated server layout using model `{used_model}`.",
        extra_fields={"Target Server ID": server_id, "Prompt": prompt}
    )

    return jsonify({"success": True, "data": layout_data, "model_used": used_model})

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/health")
def health():
    return jsonify({"status": "alive"}), 200

def run():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
