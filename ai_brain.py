import os
import json
import logging
import re
from google import genai
from google.genai import types

logger = logging.getLogger("ai_brain")

def generate_discord_layout(prompt_theme: str) -> dict:
    """
    Generates a unique, non-premade Discord server layout based on the exact 
    prompt / theme provided by the user using the Gemini API.
    """
    cleaned_prompt = prompt_theme.strip() if prompt_theme else "General Community Server"
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    system_instruction = (
        "You are an expert Discord community architect. Your job is to design a unique, "
        "custom Discord server blueprint tailored specifically to the user's request. "
        "DO NOT use generic pre-made templates unless specifically requested. "
        "You MUST return ONLY a valid JSON object with NO markdown formatting, NO backticks, and NO extra text.\n\n"
        "The JSON MUST follow this exact structure:\n"
        "{\n"
        '  "guild_name": "Unique Name Based on Prompt",\n'
        '  "separator": "│",\n'
        '  "roles": [\n'
        '    {"name": "👑 Role Name", "color": "#HEXCOLOR", "permissions": {"admin": true}},\n'
        '    {"name": "🛡️ Role Name", "color": "#HEXCOLOR", "permissions": {"manage": true}},\n'
        '    {"name": "⭐ Role Name", "color": "#HEXCOLOR", "permissions": {"send": true, "connect": true}}\n'
        '  ],\n'
        '  "categories": [\n'
        '    {\n'
        '      "name": "CATEGORY NAME",\n'
        '      "channels": [\n'
        '        {"name": "channel-name", "type": "text"},\n'
        '        {"name": "Voice Lounge", "type": "voice"}\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        "}"
    )

    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"Design a custom, highly specific Discord server layout for: {cleaned_prompt}",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.8,
                ),
            )
            
            raw_text = response.text.strip()
            # Clean potential code fence block tags if present
            cleaned_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
            cleaned_text = re.sub(r"\s*```$", "", cleaned_text, flags=re.MULTILINE)
            
            layout_json = json.loads(cleaned_text)
            return layout_json
        except Exception as e:
            logger.error(f"Error calling Gemini API: {e}. Falling back to generative builder.")

    # Dynamic fallback generator for fully customizable custom outputs
    clean_title = cleaned_prompt.title()
    clean_lower = cleaned_prompt.lower()
    
    return {
        "guild_name": f"{clean_title} Realm",
        "separator": "│",
        "roles": [
            {"name": f"👑 {clean_title} Founder", "color": "#8b5cf6", "permissions": {"admin": True}},
            {"name": f"🛡️ {clean_title} Overseer", "color": "#06b6d4", "permissions": {"manage": True}},
            {"name": f"⭐ {clean_title} Veteran", "color": "#3b82f6", "permissions": {"send": True, "connect": True}},
            {"name": f"🌿 {clean_title} Initiate", "color": "#10b981", "permissions": {"send": True, "connect": True}}
        ],
        "categories": [
            {
                "name": f"📌 {clean_title.upper()} PORTAL",
                "channels": [
                    {"name": "welcome-rules", "type": "text"},
                    {"name": "announcements", "type": "text"},
                    {"name": f"{clean_lower}-guidelines", "type": "text"}
                ]
            },
            {
                "name": f"💬 {clean_title.upper()} HUB",
                "channels": [
                    {"name": "main-chat", "type": "text"},
                    {"name": f"{clean_lower}-discussion", "type": "text"},
                    {"name": "creations-and-media", "type": "text"}
                ]
            },
            {
                "name": f"🔊 {clean_title.upper()} CHANNELS",
                "channels": [
                    {"name": f"{clean_title} Lounge 1", "type": "voice"},
                    {"name": f"{clean_title} Lounge 2", "type": "voice"},
                    {"name": "AFK Zone", "type": "voice"}
                ]
            }
        ]
    }
