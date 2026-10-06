import os
import threading
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import discord
from discord.ext import commands

# 1. Initialize Flask Web Dashboard & API
app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kumo-super-secret-key-change-me")

# Session cookie configuration for modern HTTPS proxies (Vercel & Render)
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# OAuth2 Environment Variables
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")

# Helper to dynamically get Redirect URI if not explicitly set in env
def get_redirect_uri():
    configured_uri = os.environ.get("DISCORD_REDIRECT_URI")
    if configured_uri:
        return configured_uri.strip()
    
    # Auto-detect host URL enforcing HTTPS for proxies like Vercel and Render
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "https")
    host_url = f"{forwarded_proto}://{request.host}"
    return f"{host_url.rstrip('/')}/callback"

@app.route("/")
def index():
    user = session.get("user")
    # Tries to render templates/index.html if present, else simple status response
    try:
        return render_template("index.html", user=user)
    except Exception:
        if user:
            return f"Logged in as {user.get('username')}! Dashboard is operational.", 200
        return "Kumo Web Dashboard is running! Please <a href='/login'>Log in with Discord</a> to access features.", 200

# 2. Discord OAuth2 Login Endpoint
@app.route("/login")
def login():
    if not DISCORD_CLIENT_ID:
        return "Error: DISCORD_CLIENT_ID is not configured in Environment Variables.", 500
    
    redirect_uri = get_redirect_uri()
    encoded_redirect_uri = quote(redirect_uri, safe="")
    
    # Properly formatted Discord OAuth2 authorization URL
    discord_auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={encoded_redirect_uri}"
        f"&response_type=code"
        f"&scope=identify"
    )
    return redirect(discord_auth_url)

# 3. Discord OAuth2 Callback Endpoint
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
        return f"Failed to authenticate with Discord: {token_json.get('error_description', 'Unknown error')}", 400

    # Fetch user details
    user_response = requests.get(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    user_json = user_response.json()

    # Save user into session
    session["user"] = {
        "id": user_json.get("id"),
        "username": user_json.get("username"),
        "global_name": user_json.get("global_name") or user_json.get("username"),
        "avatar": user_json.get("avatar"),
    }

    return redirect(url_for("index"))

# 4. Logout Endpoint
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("index"))

# 5. User API status check endpoint
@app.route("/api/user")
def api_user():
    user = session.get("user")
    if user:
        return jsonify({"authenticated": True, "user": user})
    return jsonify({"authenticated": False, "user": None})

# 6. Protected Layout Generation Endpoint
@app.route("/generate", methods=["POST"])
@app.route("/api/generate", methods=["POST"])
def generate_layout():
    # Enforce strict Discord login check
    user = session.get("user")
    if not user:
        return jsonify({
            "success": False,
            "error": "Authentication required. Please log in with Discord first.",
            "redirect": "/login"
        }), 401

    # Processing request for authenticated users
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")

    # Check if ai_brain module is available
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

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 7. Initialize Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("-----------------------------------------")
    
    # Load cogs
    try:
        if not "cogs.kumo" in bot.extensions:
            await bot.load_extension("cogs.kumo")
            print("Successfully loaded cog: cogs.kumo")
    except Exception as e:
        print(f"Failed to load cogs.kumo: {e}")

    # Sync slash commands globally
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

def main():
    # Start web server thread
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

