import os
import json
import re
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


class RoleConfig(BaseModel):
    name: str = Field(description="Role name, e.g. Owner, Admin, VIP, or Member")
    color: str = Field(default="#5865f2", description="Hex color code for the role, e.g. #f1c40f")


class ChannelConfig(BaseModel):
    name: str = Field(description="Clean channel name formatted for Discord without leading emojis, e.g. mod-chat or general")
    type: str = Field(description="Channel type: 'text' or 'voice'")
    topic: Optional[str] = Field(default="", description="Brief channel topic/purpose")
    emoji: Optional[str] = Field(default="💬", description="Single emoji representing the channel")
    read_only: bool = Field(default=False, description="True if only admins/bots should speak (e.g. announcements/rules)")


class CategoryConfig(BaseModel):
    name: str = Field(description="Category header name, e.g. INFORMATION or COMMUNITY")
    channels: List[ChannelConfig] = Field(description="List of channels inside this category")


class ServerLayoutSchema(BaseModel):
    server_name: str = Field(description="Suggested server name based on prompt")
    description: str = Field(description="A brief description of the server theme")
    roles: List[RoleConfig] = Field(description="List of roles configured for this server layout")
    categories: List[CategoryConfig] = Field(description="List of categories in structured order")


def strip_emojis(text: str) -> str:
    """Removes emojis from channel name string to prevent duplicate visual rendering."""
    return re.sub(r'[\u1F600-\u1F64F\u1F300-\u1F5FF\u1F680-\u1F6FF\u2600-\u26FF\u2700-\u27BF]', '', text).strip()


def generate_server_layout(
    prompt: str,
    channel_separator: str = "│",
    server_id: str = "",
    server_link: str = ""
) -> dict:
    """Uses Gemini AI to convert natural language prompt into a structured Discord Server Layout."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set.")

    client = genai.Client(api_key=api_key)

    system_instruction = (
        "You are an expert Discord Community Architect. Your goal is to design a clean, logical, "
        "and well-structured Discord server based on the user's requirements.\n"
        "Guidelines:\n"
        "1. Organize the server into clear, functional categories (e.g., WELCOME, GENERAL, GAMING, VOICE).\n"
        "2. Define default roles with realistic hex colors (e.g., Owner, Admin, Mod, VIP, Member).\n"
        "3. Keep channel names lower-case, concise, and WITHOUT emojis in the name field (e.g. 'general', 'rules').\n"
        "4. Provide a single representative emoji in the emoji field for each channel.\n"
        "5. Mark announcement, rules, or info channels as read_only=True."
    )

    user_query = f"""
    Design a complete Discord server layout for the following theme/request:
    "{prompt}"

    Apply the separator character '{channel_separator}' appropriately where needed for visual layout formatting.
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_query,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=ServerLayoutSchema,
                temperature=0.7,
            ),
        )

        layout_data = json.loads(response.text)

        sep = channel_separator.strip() if channel_separator else ""
        for category in layout_data.get("categories", []):
            for channel in category.get("channels", []):
                clean_name = strip_emojis(channel.get("name", ""))
                emoji = channel.get("emoji", "💬")
                channel["name"] = clean_name

                if sep and emoji:
                    channel["formatted_name"] = f"{emoji} {sep} {clean_name}"
                elif emoji:
                    channel["formatted_name"] = f"{emoji} {clean_name}"
                else:
                    channel["formatted_name"] = clean_name

        layout_data["build_meta"] = {
            "channel_separator": channel_separator,
            "target_server_id": server_id.strip(),
            "target_server_link": server_link.strip(),
            "prompt_used": prompt
        }

        return {
            "success": True,
            "data": layout_data
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
