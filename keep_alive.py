import os
import logging
from threading import Thread
from flask import Flask, render_template, jsonify, session, make_response, redirect, request

# Import the PyMongo ban check directly from cogs/orca.py
try:
    from cogs.orca import is_user_banned
except ImportError:
    # Safe fallback handler if imported standalone or during initial boot testing
    def is_user_banned(user_id: str) -> bool:
        return False

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("keep_alive")

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super-secret-key-change-me")

# Primary Owner ID exempted from website maintenance restrictions
PRIMARY_OWNER_ID = "1219266886143967245"


def is_maintenance_mode() -> bool:
    """Checks environment variable state for active lockdown mode."""
    return os.environ.get("IS_LOCKDOWN", "false").lower() in ("true", "1", "yes")


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
    """Health check endpoint for keep-alive pingers (e.g., UptimeRobot, Render)."""
    return jsonify({"status": "online", "maintenance": is_maintenance_mode()}), 200


@app.route('/api/auth/logout')
def logout():
    """Clears the user session and redirects home."""
    session.pop('user', None)
    return redirect('/')


@app.route('/api/auth/me')
def auth_me():
    """
    Client-side authentication & status verification endpoint.
    Queries MongoDB for active ban status.
    """
    try:
        user_data = session.get('user')
        user_id = str(user_data.get('id', '')).strip() if user_data else None

        # 1. Maintenance Mode Check (Exempt Primary Owner)
        if is_maintenance_mode():
            if not user_id or user_id != PRIMARY_OWNER_ID:
                return jsonify({
                    "authenticated": False,
                    "is_lockdown": True,
                    "is_banned": False,
                    "user": None
                }), 200

        # 2. Get current session user (if logged in via Discord OAuth)
        if user_data and user_id:
            # 3. Check PyMongo Ban Status via cogs/orca.py
            banned_status = False
            try:
                banned_status = is_user_banned(user_id)
            except Exception as ex:
                logger.error("Failed to query database ban status for ID %s: %s", user_id, ex)

            if banned_status or user_data.get('is_banned', False):
                session.pop('user', None)  # Wipe session immediately if banned
                return jsonify({
                    "authenticated": False,
                    "is_lockdown": False,
                    "is_banned": True,
                    "user": None
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
# SERVER EXECUTION
# ---------------------------------------------------------------------------

def run():
    """Runs the Flask web server."""
    port = int(os.environ.get("PORT", 8080))
    # use_reloader=False prevents double-execution in background threads
    app.run(host='0.0.0.0', port=port, use_reloader=False)


def keep_alive():
    """Spawns the web server as a daemon thread."""
    server_thread = Thread(target=run, daemon=True)
    server_thread.start()
    logger.info("Keep-alive server thread initiated successfully.")


if __name__ == '__main__':
    run()
