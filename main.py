import os
import requests
from flask import Flask, render_template, request, redirect, session, jsonify, url_for
from pymongo import MongoClient
import ai_brain  # Your custom AI logic handler

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fallback-secret-key-12345")

# Environment Variables
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
DESIGN_WEBHOOK_URL = os.environ.get("DESIGN_WEBHOOK_URL", "")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "")
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", "")

# Redirect URI for Discord OAuth
REDIRECT_URI = f"{DASHBOARD_URL.rstrip('/')}/callback" if DASHBOARD_URL else "http://localhost:5000/callback"

# MongoDB Database Setup
db = None
if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client.get_database("echo_dashboard")
    except Exception as e:
        print(f"MongoDB connection error: {e}")


def send_system_log(content_embed):
    """Sends log entry to Discord Webhook when a user accesses the site."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        return
    try:
        requests.post(SYSTEM_LOG_WEBHOOK_URL, json={"embeds": [content_embed]}, timeout=5)
    except Exception as e:
        print(f"Failed to post system log: {e}")


@app.route("/")
def index():
    user = session.get("user")
    
    # System log on entry
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    log_embed = {
        "title": "🌐 Website Access Log",
        "color": 0x8b5cf6,
        "fields": [
            {"name": "Authenticated User", "value": user["username"] if user else "Unauthenticated (Visitor)", "inline": True},
            {"name": "IP Address", "value": str(ip), "inline": True}
        ]
    }
    send_system_log(log_embed)

    discord_login_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={requests.utils.quote(REDIRECT_URI)}&response_type=code&scope=identify%20email"
    )

    return render_template("index.html", user=user, recaptcha_site_key=RECAPTCHA_SITE_KEY, discord_login_url=discord_login_url)


@app.route("/login")
def login():
    discord_login_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={requests.utils.quote(REDIRECT_URI)}&response_type=code&scope=identify%20email"
    )
    return redirect(discord_login_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))

    # Exchange code for token
    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post("https://discord.com/api/v10/oauth2/token", data=token_data, headers=headers)
    token_json = r.json()

    access_token = token_json.get("access_token")
    if not access_token:
        return redirect(url_for("index"))

    # Fetch User Details
    user_headers = {"Authorization": f"Bearer {access_token}"}
    user_r = requests.get("https://discord.com/api/v10/users/@me", headers=user_headers)
    user_data = user_r.json()

    session["user"] = {
        "id": user_data.get("id"),
        "username": user_data.get("username"),
        "avatar": f"https://cdn.discordapp.com/avatars/{user_data.get('id')}/{user_data.get('avatar')}.png" if user_data.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"
    }

    if db is not None:
        db.users.update_one({"id": user_data.get("id")}, {"$set": user_data}, upsert=True)

    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/verify-captcha", methods=["POST"])
def verify_captcha():
    data = request.json or {}
    token = data.get("token")
    
    if not RECAPTCHA_SECRET_KEY:
        return jsonify({"success": True, "message": "reCAPTCHA check bypassed (no secret key configured)"})

    res = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        data={"secret": RECAPTCHA_SECRET_KEY, "response": token},
        timeout=5
    )
    return jsonify(res.json())


@app.route("/api/generate", methods=["POST"])
def generate():
    if "user" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    payload = request.json or {}
    prompt = payload.get("prompt", "")
    
    # AI Brain layout generation
    generated_layout = ai_brain.generate_layout(prompt, GEMINI_API_KEY)
    
    return jsonify({"success": True, "data": generated_layout})


@app.route("/api/submit", methods=["POST"])
def submit_design():
    if "user" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    layout_data = request.json or {}
    user = session["user"]

    # Dispatch to DESIGN_WEBHOOK_URL
    if DESIGN_WEBHOOK_URL:
        embed = {
            "title": f"🏗️ New Server Design Submitted by {user['username']}",
            "color": 0x10b981,
            "fields": [
                {"name": "Server Name", "value": layout_data.get("server_name", "N/A"), "inline": True},
                {"name": "Categories Count", "value": str(len(layout_data.get("categories", []))), "inline": True},
                {"name": "Roles Count", "value": str(len(layout_data.get("roles", []))), "inline": True}
            ]
        }
        try:
            requests.post(DESIGN_WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
        except Exception as e:
            print(f"Error submitting to design webhook: {e}")

    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
