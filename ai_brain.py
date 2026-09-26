import os
import json
import logging
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)

# Initialize Gemini Client Safely
client = None
try:
    from google import genai
    from google.genai import types
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        client = genai.Client(api_key=api_key)
except Exception as e:
    logging.warning(f"Gemini client initialization skipped: {e}")

def get_static_fallback(prompt: str, guild_id: str) -> dict:
    """Fallback generator to ensure frontend always receives valid blueprint data."""
    return {
        "target_guild_id": str(guild_id),
        "server_name": "Roblox Community Server",
        "roles": ["everyone", "Owner", "Developer", "Admin", "Moderator", "VIP", "Member"],
        "categories": [
            {
                "name": "📌 INFORMATION",
                "channels": [
                    {"emoji": "📜", "name": "rules", "type": "announcement", "topic": "Community guidelines & rules"},
                    {"emoji": "📢", "name": "announcements", "type": "announcement", "topic": "Game announcements and patch notes"},
                    {"emoji": "🏀", "name": "updates", "type": "announcement", "topic": "Roblox basketball game updates"}
                ]
            },
            {
                "name": "💬 COMMUNITY",
                "channels": [
                    {"emoji": "💬", "name": "general-chat", "type": "text", "topic": "General chat and discussion"},
                    {"emoji": "🎬", "name": "highlights", "type": "text", "topic": "Share gameplay clips and screenshots"},
                    {"emoji": "💡", "name": "suggestions", "type": "text", "topic": "Feedback and suggestions"}
                ]
            },
            {
                "name": "🔊 VOICE LOUNGE",
                "channels": [
                    {"emoji": "🔊", "name": "General Voice", "type": "voice", "topic": "Voice chat lounge"},
                    {"emoji": "🎮", "name": "Squad Play", "type": "voice", "topic": "Team matchmaking voice"}
                ]
            }
        ]
    }

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    try:
        data = request.get_json(force=True, silent=True) or {}
        prompt = str(data.get('prompt', '')).strip()
        guild_id = str(data.get('guild_id', '')).strip()

        if not prompt or not guild_id:
            # Fall back cleanly instead of throwing HTTP 400
            return jsonify(get_static_fallback(prompt or "Roblox Game", guild_id or "0")), 200

        if not client:
            logging.warning("GEMINI_API_KEY is missing in environment. Using fallback layout.")
            return jsonify(get_static_fallback(prompt, guild_id)), 200

        system_instruction = (
            "You are a Discord server architect. Return ONLY valid JSON with this structure: "
            '{"server_name": string, "roles": [string], "categories": [{"name": string, "channels": [{"emoji": string, "name": string, "type": "text"|"voice"|"announcement", "topic": string}]}]}'
        )
        user_content = f"Target Server ID: {guild_id}\nUser Description: {prompt}"

        # Try Gemini 2.5 Flash
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
        except Exception as api_err:
            logging.error(f"Gemini API call failed: {api_err}")
            return jsonify(get_static_fallback(prompt, guild_id)), 200

    except Exception as general_err:
        logging.error(f"Unhandled endpoint exception: {general_err}")
        return jsonify(get_static_fallback("Roblox Server", "0")), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
