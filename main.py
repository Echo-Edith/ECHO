import os
import requests
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "default-fallback-secret-key")

# Environment Variables
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

def log_system_entry(user_info=None):
    if not WEBSITE_WEBHOOK_URL:
        return

    # Ignore internal Render health checks coming from localhost
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if request.remote_addr == "127.0.0.1" and not request.headers.get('X-Forwarded-For'):
        return

    user_str = f"User: {user_info.get('username')} (ID: {user_info.get('id')})" if user_info else "Guest / Unauthenticated User"
    payload = {
        "embeds": [{
            "title": "🌐 Dashboard Visit Logged",
            "description": f"A user loaded the landing page.\n**Status:** {user_str}\n**IP:** `{ip}`",
            "color": 0x8b5cf6
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
    return render_template("index.html", user=user, recaptcha_site_key=RECAPTCHA_SITE_KEY)

@app.route("/login")
def login():
    # Explicit route triggered only when user clicks the "Log in with Discord" button
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
            "username": f"{u_data['username']}",
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

@app.route("/api/submit-design", methods=["POST"])
def submit_design():
    payload = request.json or {}
    if DESIGN_WEBHOOK_URL:
        webhook_data = {
            "embeds": [{
                "title": f"🚀 New Server Design: {payload.get('server_name', 'Untitled')}",
                "fields": [
                    {"name": "Target Server ID", "value": str(payload.get("target_server_id")), "inline": True},
                    {"name": "Categories", "value": str(payload.get("categories_count")), "inline": True},
                    {"name": "Channels", "value": str(payload.get("channels_count")), "inline": True},
                    {"name": "Roles", "value": str(payload.get("roles_count")), "inline": True},
                    {"name": "Invite Link", "value": str(payload.get("invite_link")), "inline": False}
                ],
                "color": 0x10b981
            }]
        }
        try:
            requests.post(DESIGN_WEBHOOK_URL, json=webhook_data, timeout=5)
        except Exception as e:
            print(f"Webhook dispatch error: {e}")

    return jsonify({"success": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
