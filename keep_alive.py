import logging
import os
from threading import Thread
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Suppress standard Flask / Werkzeug HTTP logging to keep cronjob output clean
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app)

# Rate Limiter setup to prevent spam
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["100 per day", "20 per hour"],
    storage_uri="memory://"
)

@app.route('/')
def home():
    """Renders the single-page application with Landing and Builder views."""
    return render_template('index.html')

@app.route('/health')
def health():
    """Lightweight endpoint for uptime pings and cronjobs."""
    return jsonify({"status": "ok"}), 200

@app.route('/api/generate-layout', methods=['POST'])
@limiter.limit("10 per minute")
def mock_generate_layout():
    """
    Mock endpoint for front-end testing.
    This will be bridged with ai_brain.py once configured.
    """
    data = request.get_json() or {}
    prompt = data.get("prompt", "Default Server")
    guild_id = data.get("guild_id", "000000000000000000")
    separator = data.get("separator", "|")

    # Sample mock layout matching index.html schema requirements
    mock_response = {
        "server_name": "AI Mock Generated Community",
        "target_guild_id": guild_id,
        "separator": separator,
        "roles": ["Owner", "Admin", "Moderator", "Member"],
        "categories": [
            {
                "name": "WELCOME",
                "channels": [
                    {
                        "emoji": "👋",
                        "name": "rules",
                        "type": "text",
                        "topic": "Server rules and guidelines",
                        "permissions": {}
                    },
                    {
                        "emoji": "📢",
                        "name": "announcements",
                        "type": "announcement",
                        "topic": "Official announcements",
                        "permissions": {}
                    }
                ]
            },
            {
                "name": "COMMUNITY CHATS",
                "channels": [
                    {
                        "emoji": "💬",
                        "name": "general-chat",
                        "type": "text",
                        "topic": "General chat room",
                        "permissions": {}
                    },
                    {
                        "emoji": "🎙️",
                        "name": "Lounge",
                        "type": "voice",
                        "topic": "General voice lounge",
                        "permissions": {}
                    }
                ]
            }
        ]
    }
    return jsonify(mock_response), 200

@app.route('/api/submit-design', methods=['POST'])
def mock_submit_design():
    """Mock endpoint for layout submission."""
    blueprint = request.get_json() or {}
    return jsonify({"status": "success", "message": "Blueprint submitted successfully"}), 200

def run():
    """Runs the web server."""
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    """Starts the web server in a non-blocking background thread."""
    t = Thread(target=run)
    t.daemon = True
    t.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
