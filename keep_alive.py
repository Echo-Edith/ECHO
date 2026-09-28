import os
import logging
import time
import requests
from collections import defaultdict
from threading import Thread
from flask import Flask, render_template, jsonify, session, make_response, redirect, request

# Import the PyMongo ban check directly from cogs/orca.py
try:
    from cogs.orca import is_user_banned, ban_user_db, get_ban_details
except ImportError:
    # Safe fallback handlers if imported standalone or during initial boot testing
    def is_user_banned(user_id: str) -> bool:
        return False

    def ban_user_db(user_id: str, reason: str = "Automated website rate-limit ban", dev_message: str = ""):
        pass

    def get_ban_details(user_id: str) -> dict:
        return {"is_banned": False, "reason": None, "dev_message": None}

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("keep_alive")

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-change-me")

# Configuration & Webhooks
PRIMARY_OWNER_ID = "1219266886143967245"
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", "")

# In-memory Rate Limiting Tracker (user_id/ip -> timestamp list)
design_attempts = defaultdict(list)


def is_maintenance_mode() -> bool:
    """Checks environment variable state for active lockdown mode."""
    return os.environ.get("IS_LOCKDOWN", "false").lower() in ("true", "1", "yes")


def send_system_webhook(title: str, description: str, fields: list = None, color: int = 0xFF0000):
    """Utility to post structured alert logs to Discord System Log Webhook."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        logger.warning("SYSTEM_LOG_WEBHOOK_URL not configured. Alert skipped.")
        return

    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields or [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }]
    }
    try:
        requests.post(SYSTEM_LOG_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as err:
        logger.error("Failed to send webhook log: %s", err)


# ---------------------------------------------------------------------------
# OVERRIDE ERROR HANDLERS TO PREVENT WHITE PLAIN-TEXT PAGES
# ---------------------------------------------------------------------------
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(500)
@app.errorhandler(502)
@app.errorhandler(503)
def force_html_error(e):
    """
    Intercepts HTTP errors and returns index.html for frontend SPA rendering,
    or a JSON error payload if requested by API clients.
    """
    if request.path.startswith('/api/'):
        return jsonify({
            "error": "An error occurred handling this API request.",
            "status_code": getattr(e, 'code', 500)
        }), getattr(e, 'code', 500)

    return make_response(render_template('index.html'), 200)


# ---------------------------------------------------------------------------
# ROUTES & GLASS UI BANNED PAGE
# ---------------------------------------------------------------------------

@app.route('/')
@app.route('/maintenance')
def index():
    """Serves the primary UI container for normal/lockdown states."""
    return render_template('index.html')


@app.route('/banned')
def banned_page():
    """
    Renders a dedicated, glassmorphism UI page for banned users
    with bold red header and developer ban message.
    """
    user_data = session.get('user', {})
    user_id = str(user_data.get('id', ''))
    
    ban_info = get_ban_details(user_id) if user_id else {}
    dev_msg = ban_info.get('dev_message') or "You have been restricted from accessing this website."

    # Direct Glassmorphism HTML string for clean rendering
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Access Denied - Banned</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }}
            body {{
                background: linear-gradient(135deg, #0f0c20 0%, #150a12 50%, #050508 100%);
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #ffffff;
                overflow: hidden;
            }}
            .glass-card {{
                background: rgba(255, 255, 255, 0.03);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.08);
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.6);
                border-radius: 20px;
                padding: 40px;
                max-width: 480px;
                width: 90%;
                text-align: center;
            }}
            h1 {{
                color: #ff3b30;
                font-size: 2.8rem;
                font-weight: 800;
                letter-spacing: 2px;
                margin-bottom: 12px;
                text-shadow: 0 0 20px rgba(255, 59, 48, 0.4);
            }}
            p.subtext {{
                color: #a0a0ab;
                font-size: 0.95rem;
                margin-bottom: 24px;
            }}
            .dev-box {{
                background: rgba(255, 59, 48, 0.08);
                border-left: 4px solid #ff3b30;
                border-radius: 8px;
                padding: 16px;
                margin-top: 15px;
                text-align: left;
            }}
            .dev-box label {{
                display: block;
                font-size: 0.75rem;
                color: #ff6b63;
                text-transform: uppercase;
                letter-spacing: 1px;
                font-weight: 700;
                margin-bottom: 6px;
            }}
            .dev-box p {{
                color: #e2e2e8;
                font-size: 0.9rem;
                line-height: 1.4;
            }}
        </style>
    </head>
    <body>
        <div class="glass-card">
            <h1>BANNED</h1>
            <p class="subtext">Your access to this application has been suspended.</p>
            <div class="dev-box">
                <label>Message from Developer</label>
                <p>{dev_msg}</p>
            </div>
        </div>
    </body>
    </html>
    """
    return make_response(html_content, 200)


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "online", "maintenance": is_maintenance_mode()}), 200


@app.route('/api/auth/logout')
def logout():
    """Clears user session."""
    session.pop('user', None)
    return redirect('/')


@app.route('/api/auth/me')
def auth_me():
    """Client-side authentication & status verification endpoint."""
    try:
        user_data = session.get('user')
        user_id = str(user_data.get('id', '')).strip() if user_data else None

        if is_maintenance_mode():
            if not user_id or user_id != PRIMARY_OWNER_ID:
                return jsonify({
                    "authenticated": False,
                    "is_lockdown": True,
                    "is_banned": False,
                    "user": None
                }), 200

        if user_data and user_id:
            ban_info = get_ban_details(user_id) if user_id else {}
            if ban_info.get("is_banned") or is_user_banned(user_id) or user_data.get('is_banned', False):
                session.pop('user', None)
                return jsonify({
                    "authenticated": False,
                    "is_lockdown": False,
                    "is_banned": True,
                    "dev_message": ban_info.get("dev_message", "No message provided."),
                    "user": None
                }), 200

            return jsonify({
                "authenticated": True,
                "is_lockdown": False,
                "is_banned": False,
                "user": user_data
            }), 200

        return jsonify({
            "authenticated": False,
            "is_lockdown": is_maintenance_mode(),
            "is_banned": False,
            "user": None
        }), 200

    except Exception as e:
        logger.error("Error encountered in auth_me route: %s", e)
        return jsonify({
            "authenticated": False,
            "is_lockdown": False,
            "is_banned": False,
            "error": "Internal state evaluation error"
        }), 500


# ---------------------------------------------------------------------------
# API: DETAILED LOGGING & SERVER LINK VERIFICATION
# ---------------------------------------------------------------------------

@app.route('/api/log_entry', methods=['POST'])
def log_entry():
    """Logs detailed user profile, account age, and connection metadata."""
    user = session.get('user', {})
    if not user:
        return jsonify({"status": "ignored"}), 200

    user_id = user.get('id', 'Unknown')
    username = f"{user.get('username')}#{user.get('discriminator', '0')}"
    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{user.get('avatar')}.png" if user.get('avatar') else ""
    
    # Calculate account creation timestamp from Snowflake ID
    created_at_timestamp = (int(user_id) >> 22) + 1420070400000 if user_id.isdigit() else 0
    account_created_str = f"<t:{int(created_at_timestamp / 1000)}:R>" if created_at_timestamp else "N/A"

    fields = [
        {"name": "User", "value": f"{username} (`{user_id}`)", "inline": True},
        {"name": "Account Age", "value": account_created_str, "inline": True},
        {"name": "IP Address", "value": f"`{request.remote_addr}`", "inline": True},
        {"name": "User-Agent", "value": f"`{request.headers.get('User-Agent', 'Unknown')}`", "inline": False}
    ]

    send_system_webhook(
        title="📥 Detailed Website Entry Log",
        description=f"User <@{user_id}> accessed the site.",
        fields=fields,
        color=0x3498DB
    )
    return jsonify({"status": "logged"}), 200


@app.route('/api/verify_and_generate', methods=['POST'])
def verify_and_generate():
    """
    Verifies guild matching via Discord REST API and enforces 3 designs / 10 min rate limit.
    Instantly bans user and notifies Webhook upon rate limit violation.
    """
    user_data = session.get('user')
    user_id = str(user_data.get('id', '')).strip() if user_data else request.remote_addr

    # 1. Rate Limiting Check (3 per 10 minutes)
    now = time.time()
    user_history = [t for t in design_attempts[user_id] if now - t < 600]
    user_history.append(now)
    design_attempts[user_id] = user_history

    if len(user_history) > 3:
        # Instant Ban Trigger
        ban_user_db(user_id, reason="Exceeded 3 layout generations per 10 minutes.", dev_message="Automated ban: Excessive design requests detected.")
        
        # Dispatch alert ping to System Webhook
        send_system_webhook(
            title="🚨 INSTANT BAN: Rate Limit Exceeded",
            description=f"User <@{user_id}> (`{user_id}`) exceeded the design limit (3 actions / 10 min) and was instantly banned.",
            fields=[{"name": "IP Address", "value": f"`{request.remote_addr}`", "inline": True}],
            color=0xFF0000
        )
        session.pop('user', None)
        return jsonify({"error": "Rate limit exceeded. You have been banned.", "is_banned": True}), 403

    # 2. Extract payload
    data = request.json or {}
    server_id = str(data.get('server_id', '')).strip()
    server_invite = data.get('server_link', '').strip()

    if not server_id or not server_invite:
        return jsonify({"success": False, "message": "Missing server ID or invite link."}), 400

    # Extract code from invite URL (e.g. discord.gg/abc -> abc)
    invite_code = server_invite.split('/')[-1]

    # 3. Verify server link and server ID against Discord API
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"} if DISCORD_BOT_TOKEN else {}
    try:
        res = requests.get(f"https://discord.com/api/v10/invites/{invite_code}", headers=headers, timeout=5)
        if res.status_code == 200:
            invite_data = res.json()
            resolved_guild_id = str(invite_data.get('guild', {}).get('id', ''))

            if resolved_guild_id != server_id:
                return jsonify({
                    "success": False,
                    "message": "Verification failed: Server link does not match the provided Server ID."
                }), 400
        else:
            return jsonify({
                "success": False,
                "message": "Verification failed: Unable to validate server invite link."
            }), 400
    except Exception as err:
        logger.error("Guild verification error: %s", err)
        return jsonify({"success": False, "message": "Verification failed due to internal error."}), 500

    return jsonify({"success": True, "message": "Verification successful. Generating layout..."}), 200


# ---------------------------------------------------------------------------
# SERVER EXECUTION
# ---------------------------------------------------------------------------

def run():
    """Runs the Flask web server."""
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)


def keep_alive():
    """Spawns the web server as a daemon thread."""
    server_thread = Thread(target=run, daemon=True)
    server_thread.start()
    logger.info("Keep-alive server thread initiated successfully.")


if __name__ == '__main__':
    run()
