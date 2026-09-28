from flask import Flask, render_template, request, jsonify, session, make_response
from threading import Thread
import os

# Import the PyMongo ban check directly from cogs/orca.py
from cogs.orca import is_user_banned

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-change-me")


def is_maintenance_mode():
    return os.environ.get("IS_LOCKDOWN", "false").lower() == "true"


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
    Intercepts HTTP errors and returns index.html so the frontend JavaScript 
    can render the custom styled UI instead of displaying a plain white error page.
    """
    return make_response(render_template('index.html'), 200)


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.route('/')
@app.route('/maintenance')
@app.route('/banned')
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
    Queries MongoDB for active ban status.
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

        # 3. Check PyMongo Ban Status via cogs/orca.py
        if is_user_banned(discord_id) or user_data.get('is_banned', False):
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
