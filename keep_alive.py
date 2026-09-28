from flask import Flask, render_template, request, jsonify, session
from threading import Thread
import os

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-change-me")

# ---------------------------------------------------------------------------
# GLOBAL STATE & ENVIRONMENT CHECKS
# Set IS_LOCKDOWN=true in your environment variables to trigger maintenance mode
# ---------------------------------------------------------------------------
def is_maintenance_mode():
    return os.environ.get("IS_LOCKDOWN", "false").lower() == "true"

# Example banned Discord User IDs list or database check
BANNED_DISCORD_IDS = set(os.environ.get("BANNED_USER_IDS", "").split(","))


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Serves the primary UI container for all states."""
    return render_template('index.html')


@app.route('/health')
def health():
    """Health check endpoint for keep-alive pingers (e.g., UptimeRobot)."""
    return jsonify({"status": "online"}), 200


@app.route('/api/auth/me')
def auth_me():
    """
    Client-side authentication & status verification endpoint.
    Returns JSON statuses instead of raw HTTP text errors.
    """
    # 1. Maintenance Mode Check
    if is_maintenance_mode():
        return jsonify({
            "authenticated": False,
            "is_lockdown": True,
            "is_banned": False
        }), 200

    # 2. Get current session user (if logged in via Discord OAuth)
    user_data = session.get('user')

    if user_data:
        discord_id = str(user_data.get('id', ''))

        # 3. Check Banned Status
        if discord_id in BANNED_DISCORD_IDS or user_data.get('is_banned', False):
            return jsonify({
                "authenticated": False,
                "is_lockdown": False,
                "is_banned": True
            }), 200

        # 4. Authenticated User Payload
        return jsonify({
            "authenticated": True,
            "is_lockdown": False,
            "is_banned": False,
            "user": user_data
        }), 200

    # Unauthenticated default response
    return jsonify({
        "authenticated": False,
        "is_lockdown": False,
        "is_banned": False
    }), 200


# Fallback status routes that still serve the dark-mode HTML wrapper
@app.route('/maintenance')
@app.route('/banned')
def status_pages():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# SERVER EXECUTION
# ---------------------------------------------------------------------------

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    server_thread = Thread(target=run)
    server_thread.daemon = True
    server_thread.start()
