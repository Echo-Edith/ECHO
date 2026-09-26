import os
import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("ai_brain")

def generate_discord_layout(prompt_theme: str) -> dict:
    """
    Generates a Discord server structure using ORCA AI (google-genai SDK).
    Intelligently assigns category and channel permissions based on role hierarchy.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("ORCA_AI_KEY")
    client = genai.Client(api_key=api_key) if api_key else genai.Client()

    system_instruction = (
        "You are ORCA AI, a premier Discord server architecture engine. "
        "Generate a complete Discord server layout JSON blueprint. "
        "Define roles logically (e.g. Founder/Admin, Manager/Staff, Chatter/Member). "
        "For each category and channel, assign realistic permissions map matching role hierarchy. "
        "For instance: Admin roles get administrator=true, Staff get manage_messages=true, "
        "and standard Chatter roles get view_channel=true, send_messages=true."
    )

    prompt = (
        f"Create a Discord layout for prompt: '{prompt_theme}'.\n"
        "Return JSON with format:\n"
        "{\n"
        "  \"guild_name\": \"Server Title\",\n"
        "  \"separator\": \"│\",\n"
        "  \"roles\": [{\"id\": \"r1\", \"name\": \"👑 Admin\", \"color\": \"#8b5cf6\"}, {\"id\": \"r2\", \"name\": \"💬 Chatter\", \"color\": \"#10b981\"}],\n"
        "  \"categories\": [\n"
        "    {\n"
        "      \"id\": \"cat_1\",\n"
        "      \"name\": \"📌 INFORMATION\",\n"
        "      \"permissions\": {\n"
        "        \"r1\": {\"administrator\": true},\n"
        "        \"r2\": {\"view_channel\": true, \"send_messages\": false}\n"
        "      },\n"
        "      \"channels\": [\n"
        "        {\n"
        "          \"id\": \"ch_1\",\n"
        "          \"name\": \"rules\",\n"
        "          \"type\": \"text\",\n"
        "          \"permissions\": {}\n"
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        logger.error(f"Error executing ORCA AI generation: {e}")
        # Fallback preset if API key is not present or error occurs
        return {
            "guild_name": "Community Realm",
            "separator": "│",
            "roles": [
                {"id": "r1", "name": "👑 Community Manager", "color": "#8b5cf6"},
                {"id": "r2", "name": "💬 Chatter", "color": "#10b981"}
            ],
            "categories": [
                {
                    "id": "cat_1",
                    "name": "📌 INFORMATION",
                    "permissions": {
                        "r1": {"administrator": True},
                        "r2": {"view_channel": True, "send_messages": False}
                    },
                    "channels": [
                        {"id": "ch_1", "name": "rules", "type": "text", "permissions": {}},
                        {"id": "ch_2", "name": "announcements", "type": "text", "permissions": {}}
                    ]
                },
                {
                    "id": "cat_2",
                    "name": "💬 PUBLIC LOUNGE",
                    "permissions": {
                        "r1": {"administrator": True},
                        "r2": {"view_channel": True, "send_messages": True}
                    },
                    "channels": [
                        {"id": "ch_3", "name": "chat", "type": "text", "permissions": {}},
                        {"id": "ch_4", "name": "Voice Lounge", "type": "voice", "permissions": {}}
                    ]
                }
            ]
        }
