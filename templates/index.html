import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from google import genai
from google.genai import types

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)

# Initialize Gemini client
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

def get_static_fallback(prompt: str, guild_id: str) -> dict:
    """Always returns a valid fallback blueprint if external calls fail."""
    logging.warning("Serving static fallback layout.")
    return {
        "target_guild_id": str(guild_id),
        "server_name": "Roblox Game Community",
        "roles": ["everyone", "Owner", "Developer", "Admin", "Moderator", "VIP", "Member"],
        "categories": [
            {
                "name": "📌 INFORMATION",
                "channels": [
                    {"emoji": "📜", "name": "rules", "type": "announcement", "topic": "Community rules & guidelines"},
                    {"emoji": "📢", "name": "game-updates", "type": "announcement", "topic": "Roblox updates and patch notes"},
                    {"emoji": "🎁", "name": "announcements", "type": "announcement", "topic": "General announcements"}
                ]
            },
            {
                "name": "💬 COMMUNITY",
                "channels": [
                    {"emoji": "💬", "name": "general-chat", "type": "text", "topic": "General community chat"},
                    {"emoji": "📷", "name": "media-and-clips", "type": "text", "topic": "Share gameplay clips and photos"},
                    {"emoji": "💡", "name": "suggestions", "type": "text", "topic": "Game feedback & suggestions"}
                ]
            },
            {
                "name": "🔊 VOICE LOUNGE",
                "channels": [
                    {"emoji": "🔊", "name": "Public Lounge", "type": "voice", "topic": "Voice channel"},
                    {"emoji": "🎮", "name": "Gaming Duo", "type": "voice", "topic": "Co-op squad voice"}
                ]
            }
        ]
    }

@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    data = request.get_json(force=True, silent=True) or {}
    prompt = str(data.get('prompt', '')).strip()
    guild_id = str(data.get('guild_id', '')).strip()

    if not prompt or not guild_id:
        return jsonify({"error": "Both prompt and guild_id are required."}), 400

    if not client:
        logging.warning("No GEMINI_API_KEY set. Returning fallback.")
        return jsonify(get_static_fallback(prompt, guild_id)), 200

    system_instruction = (
        "You are a Discord server architect. Return ONLY valid JSON with this structure: "
        '{"server_name": string, "roles": [string], "categories": [{"name": string, "channels": [{"emoji": string, "name": string, "type": "text"|"voice"|"announcement", "topic": string}]}]}'
    )

    user_content = f"Target Server ID: {guild_id}\nUser Description: {prompt}"

    # Tier 1: Gemini 2.5 Flash
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.7
            )
        )
        blueprint = json.loads(response.text)
        blueprint['target_guild_id'] = guild_id
        return jsonify(blueprint), 200
    except Exception as e1:
        logging.error(f"Tier 1 failed: {e1}")

    # Tier 2: Gemini 2.0 Flash
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.7
            )
        )
        blueprint = json.loads(response.text)
        blueprint['target_guild_id'] = guild_id
        return jsonify(blueprint), 200
    except Exception as e2:
        logging.error(f"Tier 2 failed: {e2}")

    # Tier 3: Static Fallback
    return jsonify(get_static_fallback(prompt, guild_id)), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
