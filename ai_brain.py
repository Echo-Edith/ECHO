import os
import re
import json
import asyncio
import urllib.request
import urllib.error

# Official Gemini API Endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"


def get_api_key():
    return os.getenv("GEMINI_API_KEY", "").strip()


async def call_gemini_api(user_prompt: str, system_prompt: str = "") -> str:
    """
    Tier 1 Network Call: Calls Gemini API with exponential backoff retries.
    """
    api_key = get_api_key()
    if not api_key:
        return ""

    url = f"{GEMINI_API_URL}?key={api_key}"
    payload = {
        "contents": [{
            "parts": [{"text": user_prompt}]
        }]
    }
    if system_prompt:
        payload["systemInstruction"] = {
            "parts": [{"text": system_prompt}]
        }

    data_bytes = json.dumps(payload).encode('utf-8')
    delays = [1, 2, 4, 8, 16]

    for attempt, delay in enumerate(delays):
        try:
            req = urllib.request.Request(
                url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=20)
            )

            res_body = response.read().decode('utf-8')
            res_json = json.loads(res_body)

            text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if text:
                return text.strip()

        except urllib.error.HTTPError as e:
            if e.code == 429:
                await asyncio.sleep(delay)
                continue
            else:
                break
        except Exception:
            await asyncio.sleep(delay)
            continue

    return ""


class AIBrain:
    """
    Autonomous AI Architect for ORCA Studio:
    Implements a 3-tier fallback pipeline for generating Discord blueprints.
    """

    @staticmethod
    def _clean_code_block(raw_code: str) -> str:
        """Strips markdown code fences (```json ... ```) from output."""
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", raw_code.strip())
        cleaned = re.sub(r"\n```$", "", cleaned.strip())
        return cleaned.strip()

    @classmethod
    async def generate_discord_blueprint(cls, prompt_details: str, guild_name: str = "My Custom Server") -> dict:
        """
        Executes the 3-Tier Fallback Generation Strategy.
        """
        # ==========================================
        # TIER 1: LIVE GEMINI API (PRIMARY)
        # ==========================================
        if get_api_key():
            system_prompt = (
                "You are the Lead Discord Architect for ORCA AI. Generate a customized Discord server blueprint based on the user's prompt.\n"
                "Output ONLY a valid raw JSON object matching this schema EXACTLY without markdown formatting:\n\n"
                "{\n"
                '  "blueprint": {\n'
                '    "guild_name": "String",\n'
                '    "roles": [\n'
                '      {"name": "String", "color": "#HEXCOLOR"}\n'
                '    ],\n'
                '    "categories": [\n'
                '      {\n'
                '        "name": "CATEGORY NAME IN ALL CAPS",\n'
                '        "channels": [\n'
                '          {\n'
                '            "name": "channel-name",\n'
                '            "type": "text" | "voice",\n'
                '            "permissions": {\n'
                '              "RoleName": {"view": true/false, "send": true/false, "connect": true/false}\n'
                '            }\n'
                '          }\n'
                '        ]\n'
                '      }\n'
                '    ]\n'
                '  }\n'
                "}\n\n"
                "Rules:\n"
                "1. Make channel names, categories, and roles EXTREMELY specific to the topic in the prompt.\n"
                "2. Lowercase text channels with dashes (e.g. 'scrim-schedules'). Capitalize voice channels (e.g. 'Lounge Voice 1').\n"
                "3. Provide realistic permissions per role for each channel."
            )

            user_prompt = f"Server Name: {guild_name}\nPrompt / Vision: {prompt_details}"

            raw_res = await call_gemini_api(user_prompt, system_prompt)
            if raw_res:
                try:
                    cleaned = cls._clean_code_block(raw_res)
                    parsed_data = json.loads(cleaned)
                    if "blueprint" in parsed_data:
                        parsed_data["tier_used"] = "Tier 1 (Gemini AI)"
                        return parsed_data
                except Exception:
                    pass  # Fall through to Tier 2 on JSON parse error

        # ==========================================
        # TIER 2: ALGORITHMIC PROMPT MATCHER (SECONDARY)
        # ==========================================
        tier_2_result = cls._tier2_keyword_matcher(prompt_details, guild_name)
        if tier_2_result:
            tier_2_result["tier_used"] = "Tier 2 (Keyword Intent Matcher)"
            return tier_2_result

        # ==========================================
        # TIER 3: MODULAR SKELETON SYNTHESIZER (TERTIARY)
        # ==========================================
        tier_3_result = cls._tier3_synthesizer(prompt_details, guild_name)
        tier_3_result["tier_used"] = "Tier 3 (Dynamic Structural Fallback)"
        return tier_3_result

    @classmethod
    def _tier2_keyword_matcher(cls, prompt: str, guild_name: str) -> dict:
        """Tier 2: Matches intent domains using comprehensive topic mapping."""
        p = prompt.lower()
        
        # Gaming / Esports Domain
        if any(k in p for k in ["valorant", "gaming", "esports", "scrim", "fps", "clan", "guild", "fortnite", "league"]):
            roles = [
                {"name": "Owner", "color": "#f59e0b"},
                {"name": "Admin", "color": "#06b6d4"},
                {"name": "Moderator", "color": "#3b82f6"},
                {"name": "VIP Player", "color": "#9333ea"},
                {"name": "Member", "color": "#10b981"}
            ]
            categories = [
                {
                    "name": "📌 INFORMATION HUB",
                    "channels": [
                        {"name": "rules-and-announcements", "type": "text"},
                        {"name": "role-assignment", "type": "text"}
                    ]
                },
                {
                    "name": "⚔️ COMPETITIVE & SCRIMS",
                    "channels": [
                        {"name": "lfg-find-team", "type": "text"},
                        {"name": "scrim-schedules", "type": "text"},
                        {"name": "vod-reviews", "type": "text"},
                        {"name": "Squad Voice Alpha", "type": "voice"},
                        {"name": "Squad Voice Bravo", "type": "voice"}
                    ]
                },
                {
                    "name": "💬 COMMUNITY LOUNGE",
                    "channels": [
                        {"name": "general-chat", "type": "text"},
                        {"name": "clips-and-highlights", "type": "text"},
                        {"name": "Casual Lounge Voice", "type": "voice"}
                    ]
                }
            ]
            return cls._wrap_blueprint(guild_name, roles, categories)

        # Store / E-commerce / Service Domain
        if any(k in p for k in ["store", "shop", "market", "vouch", "service", "sell", "crypto", "nft"]):
            roles = [
                {"name": "Founder", "color": "#e11d48"},
                {"name": "Staff", "color": "#2563eb"},
                {"name": "Customer", "color": "#10b981"},
                {"name": "Verified Buyer", "color": "#f59e0b"},
                {"name": "@everyone", "color": "#94a3b8"}
            ]
            categories = [
                {
                    "name": "🛒 STOREFRONT",
                    "channels": [
                        {"name": "terms-of-service", "type": "text"},
                        {"name": "products-and-pricing", "type": "text"},
                        {"name": "customer-vouches", "type": "text"}
                    ]
                },
                {
                    "name": "🎟️ SUPPORT TICKETS",
                    "channels": [
                        {"name": "open-ticket", "type": "text"},
                        {"name": "billing-support", "type": "text"}
                    ]
                },
                {
                    "name": "🔒 STAFF ONLY",
                    "channels": [
                        {"name": "order-logs", "type": "text"},
                        {"name": "Staff Meeting Voice", "type": "voice"}
                    ]
                }
            ]
            return cls._wrap_blueprint(guild_name, roles, categories)

        # Education / Study / Homework Domain
        if any(k in p for k in ["study", "school", "class", "course", "learn", "student", "code", "dev"]):
            roles = [
                {"name": "Instructor", "color": "#dc2626"},
                {"name": "TA / Helper", "color": "#ea580c"},
                {"name": "Student", "color": "#0284c7"},
                {"name": "@everyone", "color": "#94a3b8"}
            ]
            categories = [
                {
                    "name": "📚 COURSE RESOURCES",
                    "channels": [
                        {"name": "announcements", "type": "text"},
                        {"name": "syllabus-and-links", "type": "text"}
                    ]
                },
                {
                    "name": "💬 DISCUSSION & HELP",
                    "channels": [
                        {"name": "homework-help", "type": "text"},
                        {"name": "general-discussion", "type": "text"},
                        {"name": "Study Room 1", "type": "voice"},
                        {"name": "Study Room 2", "type": "voice"}
                    ]
                }
            ]
            return cls._wrap_blueprint(guild_name, roles, categories)

        return None  # Pass through to Tier 3 if no keyword matches

    @classmethod
    def _tier3_synthesizer(cls, prompt: str, guild_name: str) -> dict:
        """Tier 3: Extracts keywords dynamically from user input to assemble a custom server structure."""
        # Clean words from prompt
        words = re.findall(r'\b[a-zA-Z]{4,}\b', prompt.lower())
        stop_words = {"this", "that", "with", "from", "have", "make", "want", "server", "discord", "need", "like", "some", "your"}
        filtered_words = [w for w in words if w not in stop_words]

        # Dynamic topic derivation
        topic_1 = filtered_words[0] if len(filtered_words) > 0 else "general"
        topic_2 = filtered_words[1] if len(filtered_words) > 1 else "lounge"
        topic_3 = filtered_words[2] if len(filtered_words) > 2 else "topics"

        roles = [
            {"name": "Owner", "color": "#f59e0b"},
            {"name": "Admin", "color": "#06b6d4"},
            {"name": "Moderator", "color": "#3b82f6"},
            {"name": f"{topic_1.capitalize()} Specialist", "color": "#8b5cf6"},
            {"name": "Member", "color": "#10b981"}
        ]

        categories = [
            {
                "name": "📌 INFORMATION",
                "channels": [
                    {"name": "welcome-and-rules", "type": "text"},
                    {"name": "announcements", "type": "text"}
                ]
            },
            {
                "name": f"💬 {topic_1.upper()} & DISCUSSION",
                "channels": [
                    {"name": f"{topic_1}-chat", "type": "text"},
                    {"name": f"{topic_2}-discussion", "type": "text"},
                    {"name": f"{topic_3}-gallery", "type": "text"}
                ]
            },
            {
                "name": "🔊 VOICE CHANNELS",
                "channels": [
                    {"name": f"{topic_1.capitalize()} Voice Lounge", "type": "voice"},
                    {"name": "General Voice 1", "type": "voice"}
                ]
            },
            {
                "name": "🛡️ STAFF HQ",
                "channels": [
                    {"name": "mod-logs", "type": "text"},
                    {"name": "Staff Meeting", "type": "voice"}
                ]
            }
        ]

        return cls._wrap_blueprint(guild_name, roles, categories)

    @staticmethod
    def _wrap_blueprint(guild_name: str, roles: list, categories: list) -> dict:
        """Injects default role permissions across all channels and formats payload."""
        for cat in categories:
            for ch in cat["channels"]:
                ch["permissions"] = {
                    r["name"]: {"view": True, "send": True, "connect": True}
                    for r in roles
                }

        return {
            "blueprint": {
                "guild_name": guild_name,
                "roles": roles,
                "categories": categories
            }
        }
