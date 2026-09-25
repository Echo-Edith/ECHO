import os
import logging
from threading import Thread
from flask import Flask, render_template, jsonify, request

# Suppress Werkzeug standard logging for clean terminal output
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__, template_folder='templates', static_folder='static')

# Global bot reference and request store
bot_ref = None
build_requests = []

@app.route('/')
@app.route('/staff')
def staff_index():
    """Renders the templates/index.html file for the staff control panel."""
    try:
        return render_template('index.html')
    except Exception as e:
        return f"Error loading staff template: {str(e)}", 500

@app.route('/buyer')
def buyer_index():
    """Renders the buyer index template."""
    try:
        return render_template('buyer.html')
    except Exception:
        try:
            return render_template('buyer/index.html')
        except Exception:
            return render_template('index.html')

@app.route('/healthz')
def health_check():
    """Dedicated endpoint for Render automated health checks."""
    return "OK", 200

@app.route('/api/submit_build', methods=['POST'])
def submit_build():
    """Endpoint for buyer webpage to submit a server design payload."""
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No JSON payload provided"}), 400
        
        build_requests.append(data)
        return jsonify({"success": True, "message": "Design payload received successfully!"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/requests', methods=['GET'])
def get_requests():
    """Endpoint for staff control panel to fetch pending build requests."""
    return jsonify({"success": True, "requests": build_requests}), 200

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive(bot=None):
    global bot_ref
    bot_ref = bot
    t = Thread(target=run)
    t.daemon = True
    t.start()

