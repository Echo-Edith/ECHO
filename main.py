import os
import json
import asyncio
import requests
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, session, url_for, jsonify, send_from_directory
from dotenv import load_dotenv
from ai_brain import generate_server_layout

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "default-fallback-secret-key")

BLUEPRINT_DIR = os.path.join(app.root_path, "blueprints")
os.makedirs(BLUEPRINT_DIR, exist_ok=True)

DASHBOARD_URL = os.getenv("DASHBOARD_URL") or os.getenv("RENDER_EXTERNAL_URL", "http://localhost:5000")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY")
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")
WEBSITE_WEBHOOK_URL = os.getenv("WEBSITE_WEBHOOK_URL")
DESIGN_WEBHOOK_URL = os.getenv("DESIGN_WEBHOOK_URL")

REDIRECT_URI = f"{DASHBOARD_URL.rstrip('/')}/callback"
DISCORD_AUTH_URL = (
    f"https://discord.com/api/oauth2/authorize"
    f"?client_id={DISCORD_CLIENT_ID}"
    f"&redirect_uri={requests.utils.quote(REDIRECT_URI)}"
    f"&response_type=code"
    f"&scope=identify"
)


def calculate_account_age(discord_id: str) -> str:
    """Calculates Discord account age in days/years from a snowflake ID."""
    try:
        snowflake = int(discord_id)
        # Discord epoch: 2015-01-01T00:00:00Z (1420070400000 ms)
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


def format_user_info_block(user_info: dict) -> str:
    """Formats user metrics cleanly into a 1-info-per-line markdown block."""
    if not user_info or not user_info.get("id"):
        return (
            "**User:** @Anonymous\n"
            "**Username:** `Anonymous`\n"
            "**User ID:** `N/A`\n"
            "**Account Age:** `N/A`"
        )

    user_id = str(user_info.get("id"))
    username = user_info.get("username", "Unknown")
    account_age = calculate_account_age(user_id)

    return (
        f"**User:** <@{user_id}>\n"
        f"**Username:** `{username}`\n"
        f"**User ID:** `{user_id}`\n"
        f"**Account Age:** `{account_age}`"
    )


def log_system_entry(user_info=None):
    """Sends a clean 1-info-per-line entry log to WEBSITE_WEBHOOK_URL."""
    if not WEBSITE_WEBHOOK_URL or not user_info:
        return

    if request.remote_addr == "127.0.0.1" and not request.headers.get('X-Forwarded-For'):
        return

    user_details_value = format_user_info_block(user_info)

    payload = {
        "username": "Echo Studio System Logger",
        "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
        "embeds": [{
            "title": "🌐 Dashboard Visit Logged",
            "color": 0x8b5cf6,
            "fields": [
                {
                    "name": "👤 User Information",
                    "value": user_details_value,
                    "inline": False
                },
                {
                    "name": "📌 Activity",
                    "value": "A logged-in user loaded the landing page.",
                    "inline": False
                }
            ],
            "footer": {
                "text": "Echo Studio System Logging"
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }
    try:
        requests.post(WEBSITE_WEBHOOK_URL, json=payload, timeout=3)
    except Exception as e:
        print(f"Logging error: {e}")


@app.route("/")
def index():
    user = session.get("user")
    log_system_entry(user)
    return render_template(
        "index.html",
        user=user,
        recaptcha_site_key=RECAPTCHA_SITE_KEY,
        discord_client_id=DISCORD_CLIENT_ID
    )


@app.route("/login")
def login():
    return redirect(DISCORD_AUTH_URL)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))

    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    
    token_resp = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    if token_resp.status_code != 200:
        return redirect(url_for("index"))

    access_token = token_resp.json().get("access_token")
    user_resp = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    
    if user_resp.status_code == 200:
        u_data = user_resp.json()
        avatar_url = f"https://cdn.discordapp.com/avatars/{u_data['id']}/{u_data['avatar']}.png" if u_data.get('avatar') else "https://cdn.discordapp.com/embed/avatars/0.png"
        session["user"] = {
            "id": u_data["id"],
            "username": u_data["username"],
            "avatar": avatar_url
        }

    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/verify-captcha", methods=["POST"])
def verify_captcha():
    token = request.json.get("token")
    if not token or not RECAPTCHA_SECRET_KEY:
        return jsonify({"success": False, "message": "Missing token or server configuration."}), 400

    resp = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        data={"secret": RECAPTCHA_SECRET_KEY, "response": token}
    )
    res_data = resp.json()
    if res_data.get("success"):
        session["captcha_verified"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "CAPTCHA verification failed."}), 400


@app.route("/api/generate", methods=["POST"])
def generate_layout():
    payload = request.json or {}
    user_prompt = payload.get("prompt", "Create a modern Discord community server layout.")
    server_id = payload.get("server_id", "")
    server_link = payload.get("server_link", "")
    user = session.get("user")

    result = asyncio.run(generate_server_layout(
        prompt=user_prompt,
        server_id=server_id,
        server_link=server_link,
        user_info=user,
        webhook_url=WEBSITE_WEBHOOK_URL
    ))

    if result.get("success"):
        return jsonify({"success": True, "data": result.get("data")})
    return jsonify({"success": False, "message": result.get("error", "Generation failed.")}), 500


@app.route("/blueprint/<filename>")
def get_blueprint(filename):
    return send_from_directory(BLUEPRINT_DIR, filename)


@app.route("/api/submit-design", methods=["POST"])
def submit_design():
    payload = request.json or {}
    user = session.get("user")
    
    target_server_id = str(payload.get("target_server_id", "1554280544605438054")).strip()
    server_name = payload.get("server_name", "Echo Studio Server")
    invite_link = payload.get("invite_link", "https://discord.gg/6mTr8sr7v")
    categories_count = payload.get("categories_count", 0)
    channels_count = payload.get("channels_count", 0)
    roles_count = payload.get("roles_count", 0)
    layout = payload.get("layout", {})

    blueprint_content = {
        "build_meta": {
            "target_server_id": target_server_id,
            "server_name": server_name,
            "submitted_by": user.get("username") if user else "Anonymous"
        },
        "roles": layout.get("roles", []),
        "categories": layout.get("categories", [])
    }

    filename = f"blueprint_{target_server_id}.json"
    file_path = os.path.join(BLUEPRINT_DIR, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(blueprint_content, f, indent=2)

    blueprint_url = f"{DASHBOARD_URL.rstrip('/')}/blueprint/{filename}"
    user_details_value = format_user_info_block(user)

    if DESIGN_WEBHOOK_URL:
        embed = {
            "title": f"📩 New Server Layout Submitted — #{target_server_id}",
            "description": (
                "A new blueprint layout was generated and is ready for staff deployment.\n\n"
                "⚠️ **Note:** Running `/build` will automatically nuke all existing channels and roles in the server first!\n\n"
                f"🔑 **Build Command:** `/build url: {blueprint_url}`"
            ),
            "color": 0x2ecc71,
            "fields": [
                {
                    "name": "👤 User Information",
                    "value": user_details_value,
                    "inline": False
                },
                {
                    "name": "⚙️ Blueprint Details",
                    "value": (
                        f"**Target Server ID:** `{target_server_id}`\n"
                        f"**Server Name:** `{server_name}`\n"
                        f"**Invite Link:** {invite_link}\n"
                        f"**Layout Size:** `{categories_count} Categories` | `{channels_count} Channels` | `{roles_count} Roles`"
                    ),
                    "inline": False
                }
            ],
            "footer": {
                "text": "Echo Studio Automated Server Infrastructure"
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        try:
            with open(file_path, "rb") as bf:
                files = {
                    "file": (filename, bf, "application/json"),
                    "payload_json": (None, json.dumps({"embeds": [embed]}), "application/json")
                }
                requests.post(DESIGN_WEBHOOK_URL, files=files, timeout=10)
        except Exception as e:
            print(f"Webhook dispatch error: {e}")

    return jsonify({"success": True, "file_url": blueprint_url})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
