import os
import json
import requests
from flask import Flask, render_template, request, jsonify, session, send_from_directory

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "echo-secret-key-1234")

BLUEPRINT_DIR = os.path.join(app.root_path, 'blueprints')
os.makedirs(BLUEPRINT_DIR, exist_ok=True)

DESIGN_WEBHOOK_URL = os.environ.get("DESIGN_WEBHOOK_URL", "")

@app.route('/')
def index():
    user = session.get('user', {'username': 'void071394', 'id': '1554280544605438054'})
    site_key = os.environ.get("RECAPTCHA_SITE_KEY", "your-recaptcha-site-key")
    return render_template('index.html', user=user, site_key=site_key)

@app.route('/blueprint/<filename>')
def serve_blueprint(filename):
    return send_from_directory(BLUEPRINT_DIR, filename)

@app.route('/api/submit', methods=['POST'])
def submit_layout():
    try:
        data = request.get_json() or {}
        
        server_id = data.get('server_id', '1554280544605438054')
        server_name = data.get('server_name', 'Echo Studio - Server')
        server_link = data.get('server_link', 'https://discord.gg/6mTr8sr7v')
        submitted_by = data.get('submitted_by', '@void071394')
        
        categories = data.get('categories', [])
        roles = data.get('roles', [])
        image_base64 = data.get('image_preview', None)

        cat_count = len(categories)
        chan_count = sum(len(c.get('channels', [])) for c in categories)
        role_count = len(roles)

        # 1. Save JSON Blueprint locally
        filename = f"blueprint_{server_id}.json"
        filepath = os.path.join(BLUEPRINT_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        host_url = request.host_url.rstrip('/')
        blueprint_file_url = f"{host_url}/blueprint/{filename}"

        # 2. Build Discord Webhook Payload matching exact log specification
        embed = {
            "title": f"📫 New Server Layout Submitted — #{server_id}",
            "description": "A new blueprint layout was generated and is ready for staff deployment.",
            "color": 3066993,  # Green accent line
            "fields": [
                {
                    "name": "🔑 Build Command",
                    "value": f"`/build file: {blueprint_file_url}`",
                    "inline": False
                },
                {
                    "name": "Submitted By",
                    "value": submitted_by if str(submitted_by).startswith('@') else f"@{submitted_by}",
                    "inline": False
                },
                {
                    "name": "Target Server ID",
                    "value": f"`{server_id}`",
                    "inline": False
                },
                {
                    "name": "Server Name",
                    "value": f"`{server_name}`",
                    "inline": False
                },
                {
                    "name": "Server Invite Link",
                    "value": server_link,
                    "inline": False
                },
                {
                    "name": "Categories & Channels",
                    "value": f"`{cat_count} Categories` | `{chan_count} Channels`",
                    "inline": False
                },
                {
                    "name": "Configured Roles",
                    "value": f"`{role_count} Roles`",
                    "inline": False
                }
            ],
            "footer": {
                "text": "Echo Studio Automated Server Infrastructure"
            }
        }

        # Handle file attachments to Webhook
        files = {}
        
        # Attach JSON Blueprint File
        files['file'] = (filename, json.dumps(data, indent=2), 'application/json')
        
        # Attach Rendered Discord Image Preview
        if image_base64 and ',' in image_base64:
            import base64
            img_data = base64.b64decode(image_base64.split(',')[1])
            files['file2'] = ('preview.png', img_data, 'image/png')
            embed['image'] = {'url': 'attachment://preview.png'}

        payload = {
            "payload_json": json.dumps({
                "username": "Custom Server Logger",
                "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
                "embeds": [embed]
            })
        }

        if DESIGN_WEBHOOK_URL:
            resp = requests.post(DESIGN_WEBHOOK_URL, data=payload, files=files)
            print("Webhook HTTP Response:", resp.status_code)

        return jsonify({"success": True, "blueprint_url": blueprint_file_url})

    except Exception as e:
        print("Submit Error:", e)
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
