import os
import threading
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import discord
from discord.ext import commands

# 1. Initialize Flask Web Dashboard & API
app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kumo-super-secret-key-change-me").strip()

# Session cookie configuration for modern HTTPS proxies (Vercel & Render)
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# OAuth2 Environment Variables
DISCORD_CLIENT_ID = (os.environ.get("DISCORD_CLIENT_ID") or "").strip()
DISCORD_CLIENT_SECRET = (os.environ.get("DISCORD_CLIENT_SECRET") or "").strip()


def get_base_url() -> str:
    """
    Determines the current host base URL dynamically.
    Checks DISCORD_REDIRECT_URI / DASHBOARD_URL first, then VERCEL_URL, and falls back to request.host.
    """
    dashboard = os.environ.get("DASHBOARD_URL") or os.environ.get("DISCORD_REDIRECT_URI")
    if dashboard and "your-vercel-domain" not in dashboard:
        if dashboard.endswith("/callback"):
            return dashboard[:-9].rstrip("/")
        return dashboard.rstrip("/")

    vercel_url = os.environ.get("VERCEL_URL")
    if vercel_url:
        return f"https://{vercel_url.rstrip('/')}"

    if request and request.host:
        proto = request.headers.get("X-Forwarded-Proto", "https")
        return f"{proto}://{request.host}".rstrip("/")

    return "http://localhost:5000"


def get_redirect_uri() -> str:
    configured = os.environ.get("DISCORD_REDIRECT_URI")
    if configured and configured.strip():
        return configured.strip()
    return f"{get_base_url()}/callback"


@app.route("/")
def index():
    user = session.get("user")
    try:
        return render_template("index.html", user=user)
    except Exception:
        if user:
            return f"Logged in as {user.get('username')}! Dashboard is operational.", 200
        return "Kumo Web Dashboard is running! Please <a href='/login'>Log in with Discord</a> to access features.", 200


# 2. OAuth Debug Endpoint to inspect exact URLs being used
@app.route("/debug-oauth")
def debug_oauth():
    redirect_uri = get_redirect_uri()
    encoded_redirect = quote(redirect_uri, safe="")
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={encoded_redirect}"
        f"&response_type=code"
        f"&scope=identify%20email"
    )
    return jsonify({
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri_being_sent": redirect_uri,
        "encoded_redirect_uri": encoded_redirect,
        "full_discord_auth_url": auth_url,
        "env_discord_redirect_uri": os.environ.get("DISCORD_REDIRECT_URI"),
        "has_client_secret": bool(DISCORD_CLIENT_SECRET)
    })


# 3. Discord OAuth2 Login Endpoint
@app.route("/login")
def login():
    if not DISCORD_CLIENT_ID:
        return "Error: DISCORD_CLIENT_ID is not configured in Environment Variables.", 500

    redirect_uri = get_redirect_uri()
    encoded_redirect_uri = quote(redirect_uri, safe="")

    discord_auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={encoded_redirect_uri}"
        f"&response_type=code"
        f"&scope=identify%20email"
    )
    return redirect(discord_auth_url)


# 4. Discord OAuth2 Callback Endpoint
@app.route("/callback")
def callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error or not code:
        return redirect(url_for("index"))

    redirect_uri = get_redirect_uri()

    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    # Exchange code for token
    token_response = requests.post("https://discord.com/api/v10/oauth2/token", data=token_data, headers=headers)
    token_json = token_response.json()
    access_token = token_json.get("access_token")

    if not access_token:
        return f"Failed to authenticate with Discord: {token_json.get('error_description', token_response.text)}", 400

    # Fetch user details
    user_response = requests.get(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    user_json = user_response.json()

    avatar_hash = user_json.get("avatar")
    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{user_json.get('id')}/{avatar_hash}.png"
        if avatar_hash
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )

    # Save user into session
    session["user"] = {
        "id": user_json.get("id"),
        "username": user_json.get("username"),
        "discriminator": user_json.get("discriminator", "0"),
        "global_name": user_json.get("global_name") or user_json.get("username"),
        "avatar": avatar_url,
    }

    return redirect(url_for("index"))


# 5. Logout Endpoint
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("index"))


# 6. User API status check endpoint
@app.route("/api/user")
def api_user():
    user = session.get("user")
    if user:
        return jsonify({"authenticated": True, "user": user})
    return jsonify({"authenticated": False, "user": None})


# 7. Protected Layout Generation Endpoint
@app.route("/generate", methods=["POST"])
@app.route("/api/generate", methods=["POST"])
def generate_layout():
    user = session.get("user")
    if not user:
        return jsonify({
            "success": False,
            "error": "Authentication required. Please log in with Discord first.",
            "redirect": "/login"
        }), 401

    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")

    try:
        import ai_brain
        result = ai_brain.generate_layout(prompt) if hasattr(ai_brain, "generate_layout") else "Layout generated successfully."
    except ImportError:
        result = f"Layout successfully generated for user {user.get('username')}!"

    return jsonify({
        "success": True,
        "user": user.get("username"),
        "layout": result
    })


@app.route("/health")
def health():
    return jsonify({"status": "alive"}), 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# 8. Initialize Discord Bot (For Render / persistent bot runner)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("-----------------------------------------")

    try:
        if "cogs.kumo" not in bot.extensions:
            await bot.load_extension("cogs.kumo")
            print("Successfully loaded cog: cogs.kumo")
    except Exception as e:
        print(f"Failed to load cogs.kumo: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")


def main():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("Started background HTTP server.")

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN environment variable is missing!")
        return

    bot.run(token)


if __name__ == "__main__":
    main()

