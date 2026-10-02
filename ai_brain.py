import os
import json
import io
import logging
from datetime import datetime, timezone
import requests
from flask import Flask, render_template, request, jsonify
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()

client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)

BLUEPRINT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "server_name": {"type": "STRING"},
        "roles": {
            "type": "ARRAY",
            "items": {"type": "STRING"}
        },
        "categories": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "channels": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "emoji": {"type": "STRING"},
                                "name": {"type": "STRING"},
                                "type": {
                                    "type": "STRING",
                                    "enum": ["text", "voice", "announcement"]
                                },
                                "topic": {"type": "STRING"}
                            },
                            "required": ["emoji", "name", "type"]
                        }
                    }
                },
                "required": ["name", "channels"]
            }
        }
    },
    "required": ["server_name", "roles", "categories"]
}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    data = request.get_json() or {}
    prompt = data.get('prompt', '')
    guild_id = data.get('guild_id', '')
    separator = data.get('separator', '-')

    if not prompt or not guild_id:
        return jsonify({"error": "Prompt and Guild ID are required."}), 400

    system_instruction = (
        "You are an expert Discord server architect. "
        "Generate a structured JSON layout for a Discord server tailored specifically to the user's prompt description.\n\n"
        "REQUIREMENTS:\n"
        "1. Tailor categories, channel names, topics, and roles specifically to the server theme.\n"
        "2. Emojis must fit the channel purpose.\n"
        "3. Channel names should be clean, lowercase, and concise.\n"
        "4. Include 4 to 8 custom roles and 3 to 6 categories with appropriate text and voice channels."
    )

    full_user_prompt = (
        f"Target Guild ID: {guild_id}\n"
        f"Channel Separator Character: {separator}\n"
        f"Server Purpose / Theme: {prompt}"
    )

    if client:
        try:
            logger.info("[ai_brain] Attempting Tier 1 execution (gemini-2.5-flash)...")
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=BLUEPRINT_SCHEMA,
                    temperature=0.3,
                )
            )
            layout_data = json.loads(response.text)
            return jsonify(_enrich_blueprint(layout_data, guild_id, separator))
        except Exception as e:
            logger.warning(f"[ai_brain] Tier 1 failed: {e}")

    if client:
        try:
            logger.info("[ai_brain] Attempting Tier 2 execution (gemini-2.5-pro)...")
            response = client.models.generate_content(
                model='gemini-2.5-pro',
                contents=f"{system_instruction}\n\n{full_user_prompt}\nReturn ONLY raw valid JSON.",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.4,
                )
            )
            layout_data = json.loads(response.text)
            return jsonify(_enrich_blueprint(layout_data, guild_id, separator))
        except Exception as e:
            logger.error(f"[ai_brain] Tier 2 failed: {e}")

    fallback = {
        "server_name": "Generated Community",
        "target_guild_id": guild_id,
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
    return jsonify(_enrich_blueprint(fallback, guild_id, separator))


@app.route('/api/submit-design', methods=['POST'])
def submit_design():
    blueprint = request.get_json()
    if not blueprint:
        return jsonify({"error": "No blueprint provided"}), 400

    target_guild = blueprint.get("target_guild_id", "Unknown")
    server_name = blueprint.get("server_name", "Discord Server")
    categories = blueprint.get("categories", [])
    roles = blueprint.get("roles", [])
    total_channels = sum(len(cat.get("channels", [])) for cat in categories)

    # --- EXTRACT USER & REQUEST METADATA (EXCLUDING IP ADDRESS) ---
    user_agent = request.headers.get('User-Agent', 'Unknown Client/Browser')
    accept_language = request.headers.get('Accept-Language', 'Not Specified')
    referrer = request.referrer or 'Direct Access'
    host_domain = request.host or 'Unknown Host'
    timestamp_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    # Truncate User-Agent if extremely long for clean embed formatting
    formatted_ua = user_agent[:120] + "..." if len(user_agent) > 120 else user_agent

    embed = {
        "title": f"📥 New Server Layout Submitted — Guild #{target_guild}",
        "description": (
            "A new user blueprint layout was submitted via the builder interface and is ready for deployment.\n\n"
            "📎 **Download the attached `.json` file below** and pass it into the deployment command."
        ),
        "color": 0x22C55E,  # Green accent
        "fields": [
            {"name": "🏗️ Server Name", "value": f"`{server_name}`", "inline": True},
            {"name": "📁 Target Guild ID", "value": f"`{target_guild}`", "inline": True},
            {"name": "📊 Infrastructure Summary", "value": f"`{len(categories)} Categories` | `{total_channels} Channels` | `{len(roles)} Roles`", "inline": False},
            {"name": "💻 Browser / Client Info", "value": f"`{formatted_ua}`", "inline": False},
            {"name": "🌐 Language & Locale", "value": f"`{accept_language}`", "inline": True},
            {"name": "🔗 Host Domain", "value": f"`{host_domain}`", "inline": True},
            {"name": "📍 Referrer Source", "value": f"`{referrer}`", "inline": False},
            {"name": "⏰ Submitted At", "value": f"`{timestamp_utc}`", "inline": False}
        ],
        "footer": {"text": "ORCA AI Automated Server Infrastructure"}
    }

    filename = f"blueprint_{target_guild}.json"
    json_bytes = json.dumps(blueprint, indent=2).encode('utf-8')
    file_object = io.BytesIO(json_bytes)

    if WEBHOOK_URL:
        try:
            payload_json = json.dumps({"embeds": [embed]})
            
            files = {
                "file": (filename, file_object, "application/json")
            }
            data = {
                "payload_json": payload_json
            }

            res = requests.post(
                WEBHOOK_URL,
                data=data,
                files=files,
                timeout=10
            )
            logger.info(f"Discord Webhook Status: {res.status_code}")

        except Exception as e:
            logger.error(f"Failed to post to webhook: {e}")
    else:
        logger.warning("WEBHOOK_URL is not set!")

    return jsonify({"status": "success", "message": "Blueprint submitted and logged successfully"}), 200


def _enrich_blueprint(data: dict, guild_id: str, separator: str) -> dict:
    data["target_guild_id"] = guild_id
    data["separator"] = separator

    roles = data.get("roles", ["Owner", "Admin", "Moderator", "Member"])
    
    for cat in data.get("categories", []):
        for ch in cat.get("channels", []):
            ch["name"] = ch.get("name", "channel").lower().replace(" ", separator)
            ch["permissions"] = {}
            for role in roles:
                ch["permissions"][role] = {
                    "view_channel": True,
                    "send_messages": True,
                    "embed_links": True,
                    "attach_files": True,
                    "read_message_history": True,
                    "connect": True,
                    "speak": True,
                    "manage_channels": False
                }
    return data


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
