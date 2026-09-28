import os
import json
import asyncio
import threading
import logging
from flask import request, jsonify, render_template, session
import discord
from discord.ext import commands

# Import Flask application from ai_brain.py
from ai_brain import app

logging.basicConfig(level=logging.INFO)

# -------------------------------------------------------------
# 0. WEBSITE BAN & LOCKDOWN BACKEND SYSTEM INTEGRATION
# -------------------------------------------------------------
BOT_API_KEY = os.environ.get("BOT_API_KEY", "").strip()
BANNED_USERS_FILE = "banned_users.json"
IS_LOCKDOWN = False

def load_banned_users():
    if not os.path.exists(BANNED_USERS_FILE):
        return []
    try:
        with open(BANNED_USERS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"❌ Failed to load ban file: {e}")
        return []

def save_banned_users(ban_list):
    try:
        with open(BANNED_USERS_FILE, "w") as f:
            json.dump(ban_list, f, indent=4)
    except Exception as e:
        logging.error(f"❌ Failed to save ban file: {e}")

banned_users = load_banned_users()


# --- Security API Routes for Bot Communication ---

@app.route('/api/security/ban', methods=['POST'])
def api_security_ban():
    """Endpoint called by /ban command in cogs/orca.py"""
    global banned_users
    auth_header = request.headers.get('X-Bot-Auth', '').strip()
    if BOT_API_KEY and auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    user_id = str(data.get('user_id', '')).strip()
    action = data.get('action', 'ban')
    reason = data.get('reason', 'No reason provided')

    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    if action == "ban":
        if not any(u.get('user_id') == user_id for u in banned_users):
            banned_users.append({'user_id': user_id, 'reason': reason})
            save_banned_users(banned_users)
            logging.info(f"🚫 Web Ban applied to user: {user_id}")
    elif action == "unban":
        banned_users = [u for u in banned_users if u.get('user_id') != user_id]
        save_banned_users(banned_users)
        logging.info(f"✅ Web Unban applied to user: {user_id}")

    return jsonify({"success": True, "banned_count": len(banned_users)}), 200


@app.route('/api/security/bans', methods=['GET'])
@app.route('/api/bans', methods=['GET'])
def api_security_bans():
    """Endpoint called by /ban-list command in cogs/orca.py"""
    auth_header = request.headers.get('X-Bot-Auth', '').strip()
    if BOT_API_KEY and auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    return jsonify({"banned_users": banned_users}), 200


@app.route('/api/security/lockdown', methods=['POST'])
def api_security_lockdown():
    """Endpoint called by /lockdown command in cogs/orca.py"""
    global IS_LOCKDOWN
    auth_header = request.headers.get('X-Bot-Auth', '').strip()
    if BOT_API_KEY and auth_header != BOT_API_KEY:
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json() or {}
    IS_LOCKDOWN = bool(data.get('enable', False))
    logging.info(f"🔒 Maintenance mode state updated to: {IS_LOCKDOWN}")
    return jsonify({"success": True, "is_lockdown": IS_LOCKDOWN}), 200


# --- Global Request Interceptor for Ban & Lockdown Enforcement ---

@app.before_request
def enforce_security_and_maintenance():
    # Exclude internal API routes and static assets from lockdown/ban checks
    if request.path.startswith('/api/security') or request.path.startswith('/static'):
        return None

    user = session.get('user', {})
    current_user_id = str(user.get('id', '')).strip()

    # 1. Check if the current user is Banned from website
    if current_user_id:
        ban_entry = next((u for u in banned_users if u.get('user_id') == current_user_id), None)
        if ban_entry:
            reason = ban_entry.get('reason', 'Violating platform rules')
            return render_template(
                'index.html',
                is_banned=True,
                ban_reason=reason,
                is_lockdown=False,
                user=user
            ), 403

    # 2. Check if Website Lockdown is Active
    if IS_LOCKDOWN:
        return render_template(
            'index.html',
            is_lockdown=True,
            is_banned=False,
            user=user
        ), 530


# -------------------------------------------------------------
# 1. DISCORD BOT INITIALIZATION
# -------------------------------------------------------------
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

# Enable required privileged intents for spam tracking & moderation
intents = discord.Intents.default()
intents.message_content = True  # Required for tracking message spam rate
intents.members = True          # Required for member timeouts and bans
intents.guilds = True


class OrcaBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Loads extension cogs and syncs application slash commands."""
        try:
            # Dynamically load cogs/orca.py
            await self.load_extension("cogs.orca")
            logging.info("✅ Cog 'cogs.orca' loaded successfully.")
        except Exception as e:
            logging.error(f"❌ Failed to load cog 'cogs.orca': {e}")

        # Sync slash commands with Discord API
        try:
            synced = await self.tree.sync()
            logging.info(f"🔄 Successfully synced {len(synced)} slash command(s).")
        except Exception as e:
            logging.error(f"❌ Failed to sync slash commands: {e}")

    async def on_ready(self):
        logging.info(f"🟢 Discord Bot logged in as: {self.user.name} (ID: {self.user.id})")


bot = OrcaBot()

# -------------------------------------------------------------
# 2. BOT RUNNER THREAD
# -------------------------------------------------------------
def start_discord_bot():
    if not BOT_TOKEN:
        logging.warning("⚠️ WARNING: 'DISCORD_BOT_TOKEN' environment variable is missing. Bot launch skipped.")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        logging.info("⚡ Starting Discord Bot connection...")
        bot.run(BOT_TOKEN)
    except Exception as e:
        logging.error(f"❌ Failed to run Discord Bot: {e}")


# -------------------------------------------------------------
# 3. APPLICATION ENTRY POINT
# -------------------------------------------------------------
if __name__ == '__main__':
    # Start Discord Bot in a background daemon thread
    bot_thread = threading.Thread(target=start_discord_bot, daemon=True)
    bot_thread.start()

    # Start Flask Web Server on the target port
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"🚀 Starting Web Server on port {port}...")
    app.run(host="0.0.0.0", port=port)
