import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from google import genai
from google.genai import types

app = Flask(__name__)
# Enable CORS for all routes so Vercel/Render frontend can call this backend
CORS(app)

logging.basicConfig(level=logging.INFO)

# Initialize Gemini API Client
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    logging.warning("GEMINI_API_KEY environment variable is not set!")

client = genai.Client(api_key=api_key)

# Response Schema Enforcement
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
    logging.warning("Triggered Tier 3 Static Fallback blueprint.")
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
    try:
        data = request.get_json(force=True) or {}
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

        # TIER 1: Gemini 2.5 Flash
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

            # TIER 2: Gemini 2.0 Flash
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

                # TIER 3: Pre-built Static Blueprint Fallback
                fallback = get_static_fallback(prompt, guild_id)
                return jsonify(fallback), 200

    except Exception as global_err:
        logging.error(f"Unhandled Error in /api/generate-layout: {global_err}")
        return jsonify({"error": str(global_err)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
