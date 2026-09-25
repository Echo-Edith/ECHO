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

# Deep Sea Anomaly Maintenance Screen HTML/CSS/JS
MAINTENANCE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ORCA AI — Abyss Maintenance</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body, html { width: 100%; height: 100%; overflow: hidden; background: #010409; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #06b6d4; }
        canvas { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
        .overlay {
            position: absolute;
            z-index: 10;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
            background: rgba(2, 6, 23, 0.85);
            border: 1px solid rgba(6, 182, 212, 0.4);
            box-shadow: 0 0 50px rgba(6, 182, 212, 0.2), inset 0 0 20px rgba(6, 182, 212, 0.1);
            padding: 40px 60px;
            border-radius: 24px;
            backdrop-filter: blur(12px);
            max-width: 90%;
            width: 550px;
        }
        h1 { font-size: 2rem; letter-spacing: 3px; color: #38bdf8; text-shadow: 0 0 15px rgba(56, 189, 248, 0.6); margin-bottom: 12px; font-weight: 800; }
        p { color: #94a3b8; font-size: 0.95rem; line-height: 1.6; margin-bottom: 24px; }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.4);
            color: #f87171;
            padding: 6px 16px;
            border-radius: 9999px;
            font-size: 0.8rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .pulse-dot { width: 8px; height: 8px; background: #f87171; border-radius: 50%; box-shadow: 0 0 8px #f87171; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
    </style>
</head>
<body>
    <canvas id="abyss"></canvas>
    <div class="overlay">
        <div class="status-badge"><div class="pulse-dot"></div> Deep Sea Lockdown</div>
        <h1 style="margin-top: 15px;">SYSTEM MAINTENANCE</h1>
        <p>ORCA AI infrastructure is undergoing deep-trench protocol updates. Access to the web portal is locked by system administrators.</p>
    </div>

    <script>
        const canvas = document.getElementById('abyss');
        const ctx = canvas.getContext('2d');

        function resize() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }
        resize();
        window.addEventListener('resize', resize);

        class Anglerfish {
            constructor() { this.reset(); }
            reset() {
                this.x = -150;
                this.y = Math.random() * (canvas.height - 200) + 100;
                this.speed = Math.random() * 1.2 + 0.6;
                this.size = Math.random() * 20 + 35;
                this.lightPulse = 0;
            }
            update() {
                this.x += this.speed;
                this.lightPulse += 0.05;
                if (this.x > canvas.width + 200) this.reset();
            }
            draw() {
                ctx.save();
                ctx.translate(this.x, this.y);

                // Body
                ctx.fillStyle = '#09131d';
                ctx.beginPath();
                ctx.ellipse(0, 0, this.size, this.size * 0.7, 0, 0, Math.PI * 2);
                ctx.fill();

                // Tail
                ctx.beginPath();
                ctx.moveTo(-this.size, 0);
                ctx.lineTo(-this.size - 25, -15);
                ctx.lineTo(-this.size - 25, 15);
                ctx.closePath();
                ctx.fillStyle = '#061a2c';
                ctx.fill();

                // Teeth & Mouth
                ctx.strokeStyle = '#e2e8f0';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(this.size * 0.2, 5);
                ctx.lineTo(this.size * 0.5, -5);
                ctx.lineTo(this.size * 0.4, 15);
                ctx.stroke();

                // Lure Stalk
                ctx.beginPath();
                ctx.moveTo(this.size * 0.3, -this.size * 0.5);
                ctx.quadraticCurveTo(this.size * 0.8, -this.size * 1.2, this.size * 1.2, -this.size * 0.2);
                ctx.strokeStyle = '#1e293b';
                ctx.lineWidth = 3;
                ctx.stroke();

                // Lure Glow Light
                const glow = Math.sin(this.lightPulse) * 5 + 12;
                const grad = ctx.createRadialGradient(this.size * 1.2, -this.size * 0.2, 2, this.size * 1.2, -this.size * 0.2, glow * 2);
                grad.addColorStop(0, '#38bdf8');
                grad.addColorStop(0.5, 'rgba(6, 182, 212, 0.4)');
                grad.addColorStop(1, 'transparent');

                ctx.fillStyle = grad;
                ctx.beginPath();
                ctx.arc(this.size * 1.2, -this.size * 0.2, glow * 2, 0, Math.PI * 2);
                ctx.fill();

                ctx.restore();
            }
        }

        class Jellyfish {
            constructor() { this.reset(); }
            reset() {
                this.x = Math.random() * canvas.width;
                this.y = canvas.height + 100;
                this.speed = Math.random() * 0.8 + 0.4;
                this.radius = Math.random() * 15 + 15;
                this.pulse = Math.random() * Math.PI;
            }
            update() {
                this.y -= this.speed;
                this.pulse += 0.03;
                this.x += Math.sin(this.pulse) * 0.5;
                if (this.y < -100) this.reset();
            }
            draw() {
                ctx.save();
                ctx.translate(this.x, this.y);
                const currentRadius = this.radius + Math.sin(this.pulse) * 3;

                // Umbrella cap
                const grad = ctx.createRadialGradient(0, 0, 2, 0, 0, currentRadius);
                grad.addColorStop(0, 'rgba(147, 51, 234, 0.8)');
                grad.addColorStop(1, 'rgba(6, 182, 212, 0.1)');
                ctx.fillStyle = grad;
                ctx.beginPath();
                ctx.arc(0, 0, currentRadius, Math.PI, 0);
                ctx.fill();

                // Tentacles
                ctx.strokeStyle = 'rgba(147, 51, 234, 0.4)';
                ctx.lineWidth = 1.5;
                for (let i = -currentRadius + 5; i <= currentRadius - 5; i += 6) {
                    ctx.beginPath();
                    ctx.moveTo(i, 0);
                    ctx.quadraticCurveTo(i + Math.sin(this.pulse * 2) * 8, 25, i, 45);
                    ctx.stroke();
                }

                ctx.restore();
            }
        }

        class Particle {
            constructor() {
                this.x = Math.random() * canvas.width;
                this.y = Math.random() * canvas.height;
                this.size = Math.random() * 2 + 0.5;
                this.speedY = -Math.random() * 0.3 - 0.1;
                this.opacity = Math.random() * 0.5 + 0.2;
            }
            update() {
                this.y += this.speedY;
                if (this.y < 0) {
                    this.y = canvas.height;
                    this.x = Math.random() * canvas.width;
                }
            }
            draw() {
                ctx.fillStyle = `rgba(56, 189, 248, ${this.opacity})`;
                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        const creatures = [
            new Anglerfish(),
            new Anglerfish(),
            ...Array.from({ length: 8 }, () => new Jellyfish()),
            ...Array.from({ length: 60 }, () => new Particle())
        ];

        function animate() {
            ctx.fillStyle = 'rgba(1, 4, 9, 0.25)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            creatures.forEach(c => {
                c.update();
                c.draw();
            });

            requestAnimationFrame(animate);
        }

        animate();
    </script>
</body>
</html>
"""

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


@app.before_request
def check_lockdown():
    """Global request handler: Blocks all traffic when site_locked is active, except the lockdown toggle API itself."""
    if site_locked and request.path != '/api/lockdown':
        if request.path.startswith('/api/'):
            return jsonify({"success": False, "error": "Website is under maintenance."}), 535
        return MAINTENANCE_HTML, 535


@app.route('/')
def buyer_index():
    return render_template('index.html')


@app.route('/api/generate_server', methods=['POST'])
def api_generate_server():
    data = request.json or {}
    prompt = data.get('prompt', '')
    guild_name = data.get('guild_name', 'My Custom Discord Server')
    
    try:
        blueprint = asyncio.run(AIBrain.generate_discord_blueprint(prompt, guild_name))
        return jsonify(blueprint)
    except Exception as e:
        print(f"[ERROR] AI Generation failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/lockdown', methods=['POST', 'GET'])
def toggle_lockdown():
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
