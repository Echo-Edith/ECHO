import os
import requests
from threading import Thread
from flask import Flask, redirect, request, session, render_template, jsonify
from pymongo import MongoClient

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

DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# MongoDB Ban Database Setup
db_bans = None
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI)
        db = mongo_client.get_default_database()
        db_bans = db["bans"]
    except Exception as e:
        print(f"[MongoDB Warning] Could not connect to Mongo: {e}")

# Memory Fallback
WEBSITE_BANS = {}


def is_user_banned(user_id):
    user_id_str = str(user_id)
    if db_bans is not None:
        return db_bans.find_one({"user_id": user_id_str, "location": "website"})
    return WEBSITE_BANS.get(user_id_str)


def log_system_event(title, description, color=0x00f2fe):
    """Sends entry log event to SYSTEM_LOG_WEBHOOK_URL."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        return
    
    payload = {
        "username": "ORCA System Logger",
        "embeds": [{
            "title": title,
            "description": description,
            "color": color
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
                site_key=RECAPTCHA_SITE_KEY
            )

    return render_template("index.html", is_banned=False, user=user, site_key=RECAPTCHA_SITE_KEY)


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
        session["user"] = {
            "id": user_data["id"],
            "username": user_data["username"],
            "discriminator": user_data.get("discriminator", "0"),
            "avatar": user_data.get("avatar")
        }

        # Log entry to SYSTEM_LOG_WEBHOOK_URL
        log_system_event(
            "🌐 Website Entry Logged",
            f"User **{user_data['username']}** (`{user_data['id']}`) authenticated and accessed the builder dashboard."
        )

    return redirect("/")


@app.route("/api/verify-recaptcha", methods=["POST"])
def verify_recaptcha():
    data = request.get_json() or {}
    token = data.get("token")
    
    if not token or not RECAPTCHA_SECRET_KEY:
        return jsonify({"success": True}), 200  # Fallback pass if key isn't provided in env

    verify_res = requests.post(
        "https://www.google.com/recaptcha/api/siteverify",
        data={"secret": RECAPTCHA_SECRET_KEY, "response": token}
    ).json()

    return jsonify({"success": verify_res.get("success", False)})


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
