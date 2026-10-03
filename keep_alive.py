import os
import requests
from threading import Thread
from flask import Flask, redirect, request, session, render_template, jsonify

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key-change-me")

# Environment Variables
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "https://echo-dashboard-qn39.onrender.com/callback")
DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# In-memory storage for banned users (synchronized with cogs/orca.py)
# Structure: { user_id_str: {"reason": str, "message_from_dev": str} }
WEBSITE_BANS = {}


@app.route("/")
def home():
    """Renders the main Glassmorphism frontend."""
    user = session.get("user")
    
    # Check if logged-in user is banned on the website
    if user and str(user.get("id")) in WEBSITE_BANS:
        ban_info = WEBSITE_BANS[str(user["id"])]
        return render_template(
            "index.html",
            is_banned=True,
            user=user,
            dev_message=ban_info.get("message_from_dev", "No message provided by developer.")
        )

    return render_template("index.html", is_banned=False, user=user)


@app.route("/login")
def login():
    """Redirects the user to Discord OAuth2 Authorization page."""
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
    """Handles the callback from Discord after authorization."""
    code = request.args.get("code")
    if not code:
        return redirect("/")

    # Exchange authorization code for an Access Token
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

    # Fetch User Info using the Access Token
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

    return redirect("/")


@app.route("/logout")
def logout():
    """Logs the user out by clearing session state."""
    session.clear()
    return redirect("/")


@app.route("/health")
def health():
    """Endpoint pinged by external cronjob to prevent Render spinning down."""
    return jsonify({"status": "alive"}), 200


def run():
    """Runs Flask app on port 8080 (or PORT env var)."""
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    """Spawns the web server on a background thread alongside the Discord Bot."""
    t = Thread(target=run)
    t.daemon = True
    t.start()


if __name__ == "__main__":
    run()
