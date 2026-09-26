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

# Initialize the Gemini API client
# Ensures environment variable GEMINI_API_KEY is used
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Enforced Response Schema for Structured Output
DISCORD_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "server_name": {"type": "STRING"},
        "target_guild_id": {"type": "STRING"},
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
                                "type": {"type": "STRING", "enum": ["text", "voice", "announcement"]},
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

def get_static_fallback(prompt: str, guild_id: str) -> dict:
    """Tier 3 Fallback: Returns an immediate blueprint if API endpoints fail."""
    logging.warning("Triggered Tier 3 Fallback layout generator.")
    return {
        "target_guild_id": guild_id,
        "server_name": "Community Hub",
        "roles": ["everyone", "Admin", "Moderator", "VIP", "Member"],
        "categories": [
            {
                "name": "📌 INFORMATION",
                "channels": [
                    {"emoji": "📜", "name": "rules", "type": "announcement", "topic": "Community Rules and Guidelines"},
                    {"emoji": "📢", "name": "announcements", "type": "announcement", "topic": "Server Updates"}
                ]
            },
            {
                "name": "💬 COMMUNITY CHATS",
                "channels": [
                    {"emoji": "💬", "name": "general", "type": "text", "topic": "General chat for everyone"},
                    {"emoji": "📷", "name": "media", "type": "text", "topic": "Share photos and videos"},
                    {"emoji": "🔊", "name": "Lounge", "type": "voice", "topic": "Voice chat"}
                ]
            }
        ]
    }

@app.route('/api/generate-layout', methods=['POST'])
def generate_layout():
    data = request.json or {}
    prompt = data.get('prompt', '').strip()
    guild_id = data.get('guild_id', '').strip()

    if not prompt or not guild_id:
        return jsonify({"error": "Both prompt and guild_id are required."}), 400

    system_instruction = (
        "You are an expert Discord infrastructure architect. "
        "Create a clean, well-organized Discord server layout matching the user's prompt. "
        "Include suitable channel emojis, channel types, categories, and custom roles."
    )

    user_content = f"Target Server ID: {guild_id}\nUser Description: {prompt}"

    # --- TIER 1: Gemini 2.5 Flash ---
    try:
        logging.info("Attempting Tier 1: Gemini 2.5 Flash...")
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=DISCORD_SCHEMA,
                temperature=0.7
            )
        )
        blueprint = json.loads(response.text)
        blueprint['target_guild_id'] = guild_id
        return jsonify(blueprint), 200

    except Exception as e1:
        logging.error(f"Tier 1 Generation Failed: {e1}")

        # --- TIER 2: Gemini 2.0 Flash ---
        try:
            logging.info("Attempting Tier 2: Gemini 2.0 Flash...")
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=DISCORD_SCHEMA,
                    temperature=0.7
                )
            )
            blueprint = json.loads(response.text)
            blueprint['target_guild_id'] = guild_id
            return jsonify(blueprint), 200

        except Exception as e2:
            logging.error(f"Tier 2 Generation Failed: {e2}")

            # --- TIER 3: Static Pre-built Blueprint ---
            fallback = get_static_fallback(prompt, guild_id)
            return jsonify(fallback), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
