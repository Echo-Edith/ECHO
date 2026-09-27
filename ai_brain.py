import os
import json
import io
import logging
import requests
from flask import Flask, render_template, request, jsonify
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    guild_id = data.get('guild_id', '')
    server_link = data.get('server_link', '')
    separator = data.get('separator', '-')

    if not prompt or not guild_id:
        return jsonify({"error": "Prompt and Guild ID are required."}), 400

    system_instruction = (
        "You are an expert Discord server architect. "
        "Generate a structured JSON layout for a Discord server based on the user's prompt. "
        "Return strictly raw JSON conforming to this schema:\n"
        "{\n"
        '  "server_name": "String",\n'
        '  "target_guild_id": "String",\n'
        '  "server_link": "String",\n'
        '  "separator": "String",\n'
        '  "roles": ["Role 1", "Role 2"],\n'
        '  "categories": [\n'
        '    {\n'
        '      "name": "CATEGORY NAME",\n'
        '      "channels": [\n'
        '        {\n'
        '          "emoji": "💬",\n'
        '          "name": "channel-name",\n'
        '          "type": "text|voice|announcement",\n'
        '          "topic": "Description"\n'
        '        }\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        "}"
    )

    full_user_prompt = (
        f"Target Guild ID: {guild_id}\n"
        f"Server Invite Link: {server_link}\n"
        f"Channel Separator Character: {separator}\n"
        f"Server Purpose / Theme: {prompt}"
    )

    try:
        if not client:
            raise Exception("Gemini Client not initialized. Missing GEMINI_API_KEY.")

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=full_user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.3,
            )
        )
        
        layout_data = json.loads(response.text)
        layout_data["target_guild_id"] = guild_id
        layout_data["server_link"] = server_link
        layout_data["separator"] = separator
        return jsonify(layout_data)

    except Exception as e:
        logging.error(f"Error generating layout: {e}")
        fallback = {
            "server_name": "Generated Community",
            "target_guild_id": guild_id,
            "server_link": server_link,
            "separator": separator,
            "roles": ["Admin", "Moderator", "Member"],
            "categories": [
                {
                    "name": "WELCOME",
                    "channels": [
                        {"emoji": "👋", "name": f"rules{separator}info", "type": "text", "topic": "Server rules"},
                        {"emoji": "📢", "name": "announcements", "type": "announcement", "topic": "Updates"}
                    ]
                },
                {
                    "name": "COMMUNITY",
                    "channels": [
                        {"emoji": "💬", "name": f"general{separator}chat", "type": "text", "topic": "General lounge"},
                        {"emoji": "🔊", "name": "General Voice", "type": "voice", "topic": ""}
                    ]
                }
            ]
        }
        return jsonify(fallback)


@app.route('/api/submit-design', methods=['POST'])
def submit_design():
    blueprint = request.get_json()
    if not blueprint:
        return jsonify({"error": "No blueprint provided"}), 400

    target_guild = blueprint.get("target_guild_id", "Unknown")
    server_link = blueprint.get("server_link", "N/A")
    server_name = blueprint.get("server_name", "Discord Server")
    categories = blueprint.get("categories", [])
    roles = blueprint.get("roles", [])
    total_channels = sum(len(cat.get("channels", [])) for cat in categories)

    if WEBHOOK_URL:
        # STEP 1: Send the log message/embed first
        embed = {
            "title": f"📥 New Server Layout Submitted — #{target_guild}",
            "description": (
                "A new buyer blueprint layout was generated on the web builder and is ready for staff deployment.\n\n"
                "📎 **The `.json` blueprint file will follow in the next message.** Attach it to the `/build` command in Discord."
            ),
            "color": 0x22C55E,  # Green accent bar
            "fields": [
                {"name": "Server Name", "value": f"`{server_name}`", "inline": True},
                {"name": "Server Invite Link", "value": f"{server_link}", "inline": True},
                {"name": "Categories & Channels", "value": f"`{len(categories)} Categories` | `{total_channels} Channels`", "inline": False},
                {"name": "Configured Roles", "value": f"`{len(roles)} Roles`", "inline": False}
            ],
            "footer": {"text": "ORCA AI Automated Server Infrastructure"}
        }

        try:
            log_res = requests.post(
                WEBHOOK_URL,
                json={"embeds": [embed]},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            logging.info(f"Log Webhook Response Status: {log_res.status_code}")
        except Exception as e:
            logging.error(f"Failed to post log embed to webhook: {e}")

        # STEP 2: Send the file attachment second
        filename = f"blueprint_{target_guild}.json"
        json_bytes = json.dumps(blueprint, indent=2).encode('utf-8')
        file_object = io.BytesIO(json_bytes)

        try:
            files = {
                "file": (filename, file_object, "application/json")
            }
            payload_json = json.dumps({
                "content": f"📎 Blueprint file for Guild ID: `{target_guild}`"
            })
            
            file_res = requests.post(
                WEBHOOK_URL,
                data={"payload_json": payload_json},
                files=files,
                timeout=10
            )
            logging.info(f"File Webhook Response Status: {file_res.status_code}")
        except Exception as e:
            logging.error(f"Failed to post file to webhook: {e}")
    else:
        logging.warning("WEBHOOK_URL environment variable is not set!")

    return jsonify({"status": "success", "message": "Blueprint submitted and logged successfully"}), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
