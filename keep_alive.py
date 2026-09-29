import os
import logging
import time
import requests
import re
from collections import defaultdict
from threading import Thread
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify, session, make_response, redirect, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

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

# Persistent Session Key and Lifespan (Prevents logging users out on redeploy/unban)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-echo-studio-persistent")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get("FLASK_ENV") == "production"

# Initialize IP Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Configuration, CAPTCHA, and Webhooks
PRIMARY_OWNER_ID = "1219266886143967245"
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", "").strip()
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA").strip()

# In-memory Trackers
design_attempts = defaultdict(list)  # { user_id/ip: [timestamp1, timestamp2] }
BANNED_IPS = {}                      # { ip: ban_expiration_timestamp }


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


def verify_turnstile_captcha(token: str, remote_ip: str) -> bool:
    """Verifies Cloudflare Turnstile token with Cloudflare API."""
    if not token:
        return False
    try:
        res = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": remote_ip
            },
            timeout=5
        )
        data = res.json()
        return data.get("success", False)
    except Exception as e:
        logger.error(f"Turnstile CAPTCHA verification error: {e}")
        return False


def extract_invite_code(url_or_code: str) -> str:
    """Extracts clean invite code from Discord URL."""
    match = re.search(r'(?:discord\.gg/|discord\.com/invite/)([a-zA-Z0-9-]+)', url_or_code)
    return match.group(1) if match else url_or_code.strip()


def check_user_guild_admin(user_id: str, guild_id: str) -> bool:
    """Verifies whether the user holds Administrator permissions in target guild via Bot API."""
    if not DISCORD_BOT_TOKEN:
        return True

    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        res = requests.get(f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}", headers=headers, timeout=5)
        if res.status_code != 200:
            return False

        member_data = res.json()
        roles_res = requests.get(f"https://discord.com/api/v10/guilds/{guild_id}/roles", headers=headers, timeout=5)
        if roles_res.status_code != 200:
            return False

        guild_roles = {r["id"]: int(r["permissions"]) for r in roles_res.json()}
        user_role_ids = member_data.get("roles", [])

        # Check for ADMINISTRATOR bitflag (0x8) or MANAGE_GUILD (0x20)
        for r_id in user_role_ids:
            perms = guild_roles.get(r_id, 0)
            if (perms & 0x8) == 0x8 or (perms & 0x20) == 0x20:
                return True
        return False
    except Exception as e:
        logger.error(f"Error checking admin permissions: {e}")
        return True


# ---------------------------------------------------------------------------
# MIDDLEWARE & ERROR HANDLERS
# ---------------------------------------------------------------------------

@app.before_request
def enforce_security_middleware():
    """Middleware enforcing site bans, IP bans, and maintenance restrictions."""
    session.permanent = True  # Keep Discord session alive across redeploys

    client_ip = get_remote_address()
    now = time.time()

    # IP Ban Handling with Expiration
    if client_ip in BANNED_IPS:
        ban_expiry = BANNED_IPS[client_ip]
        if now < ban_expiry:
            if request.path.startswith('/api/'):
                return jsonify({
                    "error": "IP Ban Active. Access restricted.",
                    "is_banned": True,
                    "ban_until": int(ban_expiry * 1000)
                }), 403
            return redirect('/banned')
        else:
            del BANNED_IPS[client_ip]

    user_data = session.get('user', {})
    user_id = str(user_data.get('id', '')).strip() if user_data else None

    # Alt Account Detection (0-30 days old)
    if user_id:
        created_at_ts = ((int(user_id) >> 22) + 1420070400000) / 1000.0 if user_id.isdigit() else time.time()
        account_age_days = (time.time() - created_at_ts) / 86400.0
        if 0 <= account_age_days <= 30:
            ban_reason = f"Alt Account Auto-Ban: Account is {int(account_age_days)} days old (Minimum limit 30 days)."
            ban_user_db(user_id, reason=ban_reason, dev_message=ban_reason)
            session.pop('user', None)
            if request.path.startswith('/api/'):
                return jsonify({"error": ban_reason, "is_banned": True}), 403
            return redirect('/banned')


@app.errorhandler(429)
def ratelimit_handler(e):
    """Handles Flask-Limiter IP rate limit triggers and applies 1-hour ban."""
    client_ip = get_remote_address()
    one_hour_later = time.time() + 3600
    BANNED_IPS[client_ip] = one_hour_later

    logger.warning(f"[SECURITY] IP {client_ip} exceeded rate limit. Applied 1-hour ban.")
    return jsonify({
        "error": "Rate limit exceeded. You have been placed on a 1-hour IP ban.",
        "is_banned": True,
        "ban_until": int(one_hour_later * 1000)
    }), 429


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
    """Renders glassmorphism UI page for banned users."""
    user_data = session.get('user', {})
    user_id = str(user_data.get('id', ''))
    
    ban_info = get_ban_details(user_id) if user_id else {}
    dev_msg = ban_info.get('dev_message') or "You have been restricted from accessing this website."

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
@limiter.limit("5 per minute")
def verify_and_generate():
    """
    Verifies CAPTCHA, 24-hour invite link, guild match, Admin perms,
    and enforces 3 designs / 10 min rate limit with 12-hour ban duration.
    """
    data = request.json or {}
    captcha_token = data.get('captcha_token', '').strip()
    client_ip = get_remote_address()

    # 1. Verify CAPTCHA
    if not captcha_token or not verify_turnstile_captcha(captcha_token, client_ip):
        return jsonify({"success": False, "message": "Security CAPTCHA verification failed."}), 400

    user_data = session.get('user')
    user_id = str(user_data.get('id', '')).strip() if user_data else request.remote_addr

    # 2. Rate Limiting Check (3 per 10 minutes -> 12-Hour Ban)
    now = time.time()
    user_history = [t for t in design_attempts[user_id] if now - t < 600]
    user_history.append(now)
    design_attempts[user_id] = user_history

    if len(user_history) > 3:
        twelve_hours_later = time.time() + (12 * 3600)
        BANNED_IPS[client_ip] = twelve_hours_later

        if user_data:
            ban_user_db(user_id, reason="Exceeded 3 layout generations per 10 minutes.", dev_message="Automated 12-hour ban: Excessive layout attempts.")

        send_system_webhook(
            title="🚨 INSTANT 12-HOUR BAN: Rate Limit Exceeded",
            description=f"User <@{user_id}> (`{user_id}`) exceeded limit (3 actions / 10 min) and was banned for 12 hours.",
            fields=[{"name": "IP Address", "value": f"`{client_ip}`", "inline": True}],
            color=0xFF0000
        )
        return jsonify({
            "error": "Rate limit exceeded (3 layouts in 10 mins). You have been banned for 12 hours.",
            "is_banned": True,
            "ban_until": int(twelve_hours_later * 1000)
        }), 403

    # 3. Extract payload
    server_id = str(data.get('server_id', '')).strip()
    server_invite = data.get('server_link', '').strip()

    if not server_id or not server_invite:
        return jsonify({"success": False, "message": "Missing server ID or invite link."}), 400

    invite_code = extract_invite_code(server_invite)

    # 4. Verify server link, server ID, 24h duration, & Admin permissions via Discord API
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"} if DISCORD_BOT_TOKEN else {}
    try:
        res = requests.get(f"https://discord.com/api/v10/invites/{invite_code}?with_counts=true", headers=headers, timeout=5)
        if res.status_code == 200:
            invite_data = res.json()
            resolved_guild_id = str(invite_data.get('guild', {}).get('id', '')).strip()

            if resolved_guild_id != server_id:
                return jsonify({
                    "success": False,
                    "message": f"Verification failed: Invite belongs to Guild ID `{resolved_guild_id}`, not target ID `{server_id}`."
                }), 400

            max_age = invite_data.get("max_age", 0)
            if max_age != 0 and max_age < 86400:
                return jsonify({
                    "success": False,
                    "message": "Verification failed: Server invite link must be active for at least 24 hours (or infinite)."
                }), 400

            if user_data and not check_user_guild_admin(user_id, server_id):
                return jsonify({
                    "success": False,
                    "message": "Verification failed: You do not hold Administrator permissions in this target server."
                }), 403
        else:
            return jsonify({
                "success": False,
                "message": "Verification failed: Provided invite link is invalid or expired."
            }), 400
    except Exception as err:
        logger.error("Guild verification error: %s", err)
        return jsonify({"success": False, "message": "Verification failed due to internal connection error."}), 500

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
