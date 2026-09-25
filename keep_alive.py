import os
import json
import logging
import urllib.request
from threading import Thread
from flask import Flask, render_template, jsonify, request

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__, template_folder='templates', static_folder='static')

bot_ref = None
build_requests = []
total_layouts_created = 0
site_locked = False


def send_discord_webhook_notification(payload):
    """Sends submitted server design payload directly to a Discord Channel via Webhook."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("[INFO] DISCORD_WEBHOOK_URL not set. Skipping automated Discord message.")
        return

    order_id = payload.get("order_id", "#ORD-0000")
    blueprint = payload.get("blueprint", {})
    guild_name = blueprint.get("guild_name", "Custom Server")
    guild_id = blueprint.get("guild_id", "Unknown")
    categories = blueprint.get("categories", [])
    roles = blueprint.get("roles", [])

    total_channels = sum(len(c.get("channels", [])) for c in categories)

    embed = {
        "title": f"📥 New Server Layout Submitted — {order_id}",
        "description": f"A new buyer blueprint layout was generated on the web builder and is ready for staff deployment.",
        "color": 437012,  # Cyan
        "fields": [
            {"name": "Blueprint Name", "value": f"`{guild_name}`", "inline": True},
            {"name": "Target Guild ID", "value": f"`{guild_id}`", "inline": True},
            {"name": "Categories / Channels", "value": f"`{len(categories)} Categories` | `{total_channels} Channels`", "inline": False},
            {"name": "Configured Roles", "value": f"`{len(roles)} Roles`", "inline": True},
            {"name": "Deployment Command", "value": f"Download the JSON file or copy it from dashboard and run `/build` in Discord.", "inline": False}
        ],
        "footer": {"text": "ORCA AI Automated Layout Logger"}
    }

    # Prepare attached JSON blueprint snippet
    discord_payload = {
        "username": "ORCA AI Dispatcher",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/4712/4712109.png",
        "embeds": [embed]
    }

    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(discord_payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'ORCA-AI-Webhook'
            }
        )
        with urllib.request.urlopen(req) as resp:
            pass
        print(f"[SUCCESS] Sent layout {order_id} to Discord Webhook channel.")
    except Exception as e:
        print(f"[ERROR] Webhook dispatch failed: {e}")


@app.route('/')
@app.route('/staff')
def staff_index():
    """Renders staff control panel unless site lockdown is active."""
    if site_locked:
        return """
        <body style="background:#020617;color:#06b6d4;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
            <div style="text-align:center;border:1px solid #06b6d4;padding:40px;border-radius:24px;background:#0b1329;">
                <h1>ORCA AI -- ACCESS LOCKED</h1>
                <p style="color:#94a3b8;">This website portal is currently under administrator lockdown.</p>
            </div>
        </body>
        """, 535
    try:
        return render_template('index.html')
    except Exception as e:
        return f"Error loading staff template: {str(e)}", 500


@app.route('/buyer')
def buyer_index():
    """Renders buyer server builder."""
    if site_locked:
        return """
        <body style="background:#020617;color:#ef4444;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
            <div style="text-align:center;border:1px solid #ef4444;padding:40px;border-radius:24px;background:#0b1329;">
                <h1>BUILDER UNDER LOCKDOWN</h1>
                <p style="color:#94a3b8;">The interactive server builder has been temporarily locked by administrators.</p>
            </div>
        </body>
        """, 535
    try:
        return render_template('buyer/index.html')
    except Exception:
        return render_template('buyer.html')


@app.route('/healthz')
def health_check():
    """Dedicated endpoint for Render automated health checks."""
    return "OK", 200


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Fetches real-time bot statistics and system information."""
    total_guilds = len(bot_ref.guilds) if bot_ref and hasattr(bot_ref, 'guilds') else 0
    total_users = sum(g.member_count or 0 for g in bot_ref.guilds) if bot_ref and hasattr(bot_ref, 'guilds') else 0
    latency = round(bot_ref.latency * 1000) if bot_ref and hasattr(bot_ref, 'latency') else 0

    return jsonify({
        "success": True,
        "layouts_created": total_layouts_created,
        "total_guilds": total_guilds,
        "total_users": total_users,
        "bot_latency": latency
    }), 200


@app.route('/api/lockdown', methods=['POST', 'GET'])
def toggle_lockdown():
    """Endpoint for Discord Bot or Dashboard to toggle website access lockdown."""
    global site_locked
    state = request.args.get('state') or (request.json.get('state') if request.is_json else None)
    if state is not None:
        if isinstance(state, str):
            site_locked = state.lower() in ['true', '1', 'yes', 'lock']
        else:
            site_locked = bool(state)
    else:
        site_locked = not site_locked

    return jsonify({"success": True, "site_locked": site_locked}), 200


@app.route('/api/submit_build', methods=['POST'])
def submit_build():
    """Endpoint for buyer webpage to submit a server design payload."""
    global total_layouts_created
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No JSON payload provided"}), 400

        build_requests.append(data)
        total_layouts_created += 1

        # Dispatch notification to Discord Webhook channel
        send_discord_webhook_notification(data)

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

