import os
import io
import json
import requests
from flask import Flask, render_template, request, redirect, session, url_for, jsonify, send_file
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback_secret_key")

# Environment Variables
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")
DESIGN_WEBHOOK_URL = os.getenv("DESIGN_WEBHOOK_URL", "")
SYSTEM_LOG_WEBHOOK_URL = os.getenv("SYSTEM_LOG_WEBHOOK_URL", "")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# In-memory store for generated blueprint JSON files (Key: server_id / blueprint_id)
blueprints_store = {}

# ----------------------------------------------------
# LOGGING SYSTEM (On Login Only - IP Removed)
# ----------------------------------------------------
def send_system_login_log(user_data):
    """Logs user login to SYSTEM_LOG_WEBHOOK_URL without IP logging."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        return

    username = user_data.get("username", "Unknown User")
    user_id = user_data.get("id", "N/A")
    avatar = user_data.get("avatar", "")
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else ""

    payload = {
        "embeds": [
            {
                "title": "🔐 User Authenticated / Logged In",
                "color": 0x3498db,
                "fields": [
                    {"name": "User", "value": f"@{username}", "inline": True},
                    {"name": "User ID", "value": f"`{user_id}`", "inline": True}
                ],
                "thumbnail": {"url": avatar_url} if avatar_url else {}
            }
        ]
    }
    try:
        requests.post(SYSTEM_LOG_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"Error sending login log: {e}")


# ----------------------------------------------------
# DESIGN LOGGING SYSTEM (Image 3 Matching Embed)
# ----------------------------------------------------
def send_design_submission_log(user_data, blueprint_data):
    """Sends submission log formatted matching Image 3 structure."""
    if not DESIGN_WEBHOOK_URL:
        return

    server_id = blueprint_data.get("target_server_id", "000000000000000000")
    server_name = blueprint_data.get("server_name", "Untitled Server")
    invite_link = blueprint_data.get("invite_link", "N/A")
    cat_count = blueprint_data.get("categories_count", 0)
    chan_count = blueprint_data.get("channels_count", 0)
    roles_count = blueprint_data.get("roles_count", 0)
    username = user_data.get("username", "Unknown User")

    file_download_url = f"{DASHBOARD_URL.rstrip('/')}/blueprint/{server_id}.json"

    # Match Image 3 structure exactly
    embed_payload = {
        "embeds": [
            {
                "title": f"📫 New Server Layout Submitted — #{server_id}",
                "description": "A new blueprint layout was generated and is ready for staff deployment.",
                "color": 0x2ecc71,  # Green left border accent
                "fields": [
                    {
                        "name": "🔑 Build Command:",
                        "value": f"`/build file: {file_download_url}`",
                        "inline": False
                    },
                    {
                        "name": "Submitted By",
                        "value": f"@{username}",
                        "inline": False
                    },
                    {
                        "name": "Target Server ID",
                        "value": f"`{server_id}`",
                        "inline": False
                    },
                    {
                        "name": "Server Name",
                        "value": f"`{server_name}`",
                        "inline": False
                    },
                    {
                        "name": "Server Invite Link",
                        "value": invite_link,
                        "inline": False
                    },
                    {
                        "name": "Categories & Channels",
                        "value": f"`{cat_count} Categories` | `{chan_count} Channels`",
                        "inline": False
                    },
                    {
                        "name": "Configured Roles",
                        "value": f"`{roles_count} Roles`",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "Echo Studio Automated Server Infrastructure"
                }
            }
        ]
    }

    try:
        requests.post(DESIGN_WEBHOOK_URL, json=embed_payload, timeout=5)
    except Exception as e:
        print(f"Error sending submission log: {e}")


# ----------------------------------------------------
# ROUTES & OAUTH
# ----------------------------------------------------
@app.route("/")
def index():
    user = session.get("user")
    return render_template("index.html", user=user)


@app.route("/login")
def login():
    # Example Discord OAuth2 Redirect
    redirect_uri = f"{DASHBOARD_URL.rstrip('/')}/callback"
    oauth_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}&response_type=code&scope=identify"
    )
    return redirect(oauth_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))

    # Exchange code for token
    token_url = "https://discord.com/api/oauth2/token"
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{DASHBOARD_URL.rstrip('/')}/callback",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    res = requests.post(token_url, data=data, headers=headers)
    token_json = res.json()

    access_token = token_json.get("access_token")
    if access_token:
        # Fetch user info
        user_res = requests.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_data = user_res.json()
        session["user"] = user_data

        # --- LOG ON LOGIN ONLY (IP Excluded) ---
        send_system_login_log(user_data)

    return redirect(url_for("index"))


@app.route("/api/submit-design", methods=["POST"])
def submit_design():
    user = session.get("user")
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json() or {}
    server_id = payload.get("target_server_id", "1554280544605438054")
    
    # Store JSON in memory for download endpoint
    blueprints_store[server_id] = payload

    # Send formatted embed log to DESIGN_WEBHOOK_URL
    send_design_submission_log(user, payload)

    return jsonify({"status": "success", "server_id": server_id})


@app.route("/blueprint/<server_id>.json")
def get_blueprint_json(server_id):
    data = blueprints_store.get(server_id, {"error": "Blueprint not found"})
    buffer = io.BytesIO(json.dumps(data, indent=2).encode('utf-8'))
    return send_file(
        buffer,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"blueprint_{server_id}.json"
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
