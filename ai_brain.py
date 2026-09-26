import os
import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("ai_brain")

def generate_discord_layout(prompt_theme: str) -> dict:
    """
    Generates a Discord server structure using ORCA AI (google-genai SDK).
    Autonomously creates custom roles matching the theme/prompt and assigns exact
    per-channel permissions based on role hierarchy and channel context.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("ORCA_AI_KEY")
    client = genai.Client(api_key=api_key) if api_key else genai.Client()

    system_instruction = (
        "You are ORCA AI, an expert Discord server architect. "
        "Analyze the user's prompt theme and autonomously design a complete Discord server layout. "
        "1. Create custom, highly relevant roles fitting the prompt's unique domain (e.g., specific staff, VIP, member, or specialized community tiers). "
        "2. Create structured categories (Information, General, Media, Voice, Staff-only, etc.) with relevant text/voice channels. "
        "3. Autonomously configure explicit channel permissions for every single role in each channel. "
        "   - Read-only channels (e.g., announcements/rules): Only higher staff/admin roles can send messages; members can only view. "
        "   - Public channels: Standard member roles can view, send messages, or connect to voice. "
        "   - Private/Staff channels: Standard member roles cannot view; only appropriate staff/management roles can view and participate."
    )

    prompt = (
        f"Design a complete Discord server tailored for: '{prompt_theme}'.\n\n"
        "Return strictly valid JSON using this format:\n"
        "{\n"
        "  \"guild_name\": \"Server Name\",\n"
        "  \"separator\": \"│\",\n"
        "  \"roles\": [\n"
        "    {\"name\": \"<Contextual Staff/Admin Role>\", \"color\": \"#hex\"},\n"
        "    {\"name\": \"<Contextual Special Role>\", \"color\": \"#hex\"},\n"
        "    {\"name\": \"<Contextual Member Role>\", \"color\": \"#hex\"}\n"
        "  ],\n"
        "  \"categories\": [\n"
        "    {\n"
        "      \"name\": \"CATEGORY NAME\",\n"
        "      \"channels\": [\n"
        "        {\n"
        "          \"name\": \"channel-name\",\n"
        "          \"type\": \"text\",\n"
        "          \"permissions\": {\n"
        "            \"<Role Name>\": {\"view\": true, \"send\": true, \"connect\": true}\n"
        "          }\n"
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
        # Dynamic local fallback structure
        return build_dynamic_fallback(prompt_theme)


def build_dynamic_fallback(prompt_theme: str) -> dict:
    """Fallback server generator if API is unavailable."""
    p = prompt_theme.lower()
    
    if "valorant" in p or "gaming" in p or "esports" in p:
        roles = [
            {"name": "⚡ Team Captain", "color": "#f59e0b"},
            {"name": "🛡️ Coach / Staff", "color": "#06b6d4"},
            {"name": "🎯 Radiant Player", "color": "#3b82f6"},
            {"name": "🎮 Gamer", "color": "#10b981"}
        ]
        categories = [
            {
                "name": "📌 INFORMATION",
                "channels": [
                    {
                        "name": "rules-and-info",
                        "type": "text",
                        "permissions": {
                            "⚡ Team Captain": {"view": True, "send": True, "connect": True},
                            "🛡️ Coach / Staff": {"view": True, "send": True, "connect": True},
                            "🎯 Radiant Player": {"view": True, "send": False, "connect": False},
                            "🎮 Gamer": {"view": True, "send": False, "connect": False}
                        }
                    }
                ]
            },
            {
                "name": "💬 COMMUNITY & LFG",
                "channels": [
                    {
                        "name": "lfg-squads",
                        "type": "text",
                        "permissions": {
                            "⚡ Team Captain": {"view": True, "send": True, "connect": True},
                            "🛡️ Coach / Staff": {"view": True, "send": True, "connect": True},
                            "🎯 Radiant Player": {"view": True, "send": True, "connect": True},
                            "🎮 Gamer": {"view": True, "send": True, "connect": True}
                        }
                    },
                    {
                        "name": "Squad Lounge",
                        "type": "voice",
                        "permissions": {
                            "⚡ Team Captain": {"view": True, "send": True, "connect": True},
                            "🛡️ Coach / Staff": {"view": True, "send": True, "connect": True},
                            "🎯 Radiant Player": {"view": True, "send": True, "connect": True},
                            "🎮 Gamer": {"view": True, "send": True, "connect": True}
                        }
                    }
                ]
            }
        ]
    else:
        roles = [
            {"name": "👑 Server Founder", "color": "#8b5cf6"},
            {"name": "🛡️ Moderation Team", "color": "#06b6d4"},
            {"name": "⭐ VIP Member", "color": "#f59e0b"},
            {"name": "💬 Community Member", "color": "#10b981"}
        ]
        categories = [
            {
                "name": "📌 WELCOME & RULES",
                "channels": [
                    {
                        "name": "welcome-and-rules",
                        "type": "text",
                        "permissions": {
                            "👑 Server Founder": {"view": True, "send": True, "connect": True},
                            "🛡️ Moderation Team": {"view": True, "send": True, "connect": True},
                            "⭐ VIP Member": {"view": True, "send": False, "connect": False},
                            "💬 Community Member": {"view": True, "send": False, "connect": False}
                        }
                    }
                ]
            },
            {
                "name": "💬 PUBLIC LOUNGE",
                "channels": [
                    {
                        "name": "general-chat",
                        "type": "text",
                        "permissions": {
                            "👑 Server Founder": {"view": True, "send": True, "connect": True},
                            "🛡️ Moderation Team": {"view": True, "send": True, "connect": True},
                            "⭐ VIP Member": {"view": True, "send": True, "connect": True},
                            "💬 Community Member": {"view": True, "send": True, "connect": True}
                        }
                    },
                    {
                        "name": "Lounge Voice",
                        "type": "voice",
                        "permissions": {
                            "👑 Server Founder": {"view": True, "send": True, "connect": True},
                            "🛡️ Moderation Team": {"view": True, "send": True, "connect": True},
                            "⭐ VIP Member": {"view": True, "send": True, "connect": True},
                            "💬 Community Member": {"view": True, "send": True, "connect": True}
                        }
                    }
                ]
            }
        ]

    return {
        "guild_name": prompt_theme if len(prompt_theme) <= 25 else "Custom AI Realm",
        "separator": "│",
        "roles": roles,
        "categories": categories
    }
