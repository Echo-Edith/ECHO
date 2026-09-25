import os
import json
import logging
import asyncio
import urllib.request
from threading import Thread
from flask import Flask, render_template, jsonify, request
from ai_brain import AIBrain

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__, template_folder='templates', static_folder='static')

bot_ref = None
build_requests = []
total_layouts_created = 0
site_locked = False


def send_discord_webhook_notification(payload):
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("[INFO] DISCORD_WEBHOOK_URL not set. Skipping webhook dispatch.")
        return

    order_id = payload.get("order_id", "#ORD-0000")
    blueprint = payload.get("blueprint", {})
    guild_name = blueprint.get("guild_name", "Custom Server")
    guild_id = blueprint.get("guild_id", "") or "Not Provided"
    categories = blueprint.get("categories", [])
    roles = blueprint.get("roles", [])

    total_channels = sum(len(c.get("channels", [])) for c in categories)

    embed = {
        "title": f"📥 New Server Layout Submitted — {order_id}",
        "description": "A new buyer blueprint layout was generated on the web builder and is ready for staff deployment.\n\n📎 **Download the attached `.json` blueprint file below** and attach it to the `/build` command in Discord.",
        "color": 437012,
        "fields": [
            {"name": "Server Name", "value": f"`{guild_name}`", "inline": True},
            {"name": "Target Guild ID", "value": f"`{guild_id}`", "inline": True},
            {"name": "Categories & Channels", "value": f"`{len(categories)} Categories` | `{total_channels} Channels`", "inline": False},
            {"name": "Configured Roles", "value": f"`{len(roles)} Roles`", "inline": True}
        ],
        "footer": {"text": "ORCA AI Automated Server Infrastructure"}
    }

    payload_json = {
        "username": "ORCA AI Dispatcher",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/4712/4712109.png",
        "embeds": [embed]
    }

    file_bytes = json.dumps(blueprint, indent=2).encode('utf-8')
    filename = f"blueprint_{order_id.replace('#', '')}.json"
    boundary = f"----ORCAFormBoundary{os.urandom(12).hex()}"
    body = bytearray()

    body.extend(f"--{boundary}\r\n".encode('utf-8'))
    body.extend(b'Content-Disposition: form-data; name="payload_json"\r\n')
    body.extend(b'Content-Type: application/json\r\n\r\n')
    body.extend(json.dumps(payload_json).encode('utf-8'))
    body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode('utf-8'))
    body.extend(f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'.encode('utf-8'))
    body.extend(b'Content-Type: application/json\r\n\r\n')
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode('utf-8'))

    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'User-Agent': 'ORCA-AI-Webhook-Client/1.0'
    }

    try:
        req = urllib.request.Request(webhook_url, data=bytes(body), headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[SUCCESS] Sent layout {order_id} to Discord Webhook channel.")
    except Exception as e:
        print(f"[ERROR] Webhook dispatch failed: {e}")


@app.route('/')
def buyer_index():
    if site_locked:
        return "<body style='background:#020617;color:#ef4444;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;'><div style='text-align:center;border:1px solid #ef4444;padding:40px;border-radius:24px;background:#0b1329;'><h1>BUILDER LOCKED</h1></div></body>", 535
    
    # As per image_2.png, index.html is located in the templates folder
    return render_template('index.html')


@app.route('/api/generate_server', methods=['POST'])
def api_generate_server():
    """Hooks the frontend builder to the AI Python logic."""
    data = request.json or {}
    prompt = data.get('prompt', '')
    guild_name = data.get('guild_name', 'My Custom Discord Server')
    
    try:
        # Run the async AI generation within the sync Flask route
        blueprint = asyncio.run(AIBrain.generate_discord_blueprint(prompt, guild_name))
        return jsonify(blueprint)
    except Exception as e:
        print(f"[ERROR] AI Generation failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/submit_build', methods=['POST'])
def submit_build():
    global total_layouts_created
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No JSON payload provided"}), 400

        build_requests.append(data)
        total_layouts_created += 1
        send_discord_webhook_notification(data)
        return jsonify({"success": True, "message": "Design payload received successfully!"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/healthz')
def health_check():
    return "OK", 200


def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)


def keep_alive(bot=None):
    global bot_ref
    bot_ref = bot
    t = Thread(target=run)
    t.daemon = True
    t.start()
