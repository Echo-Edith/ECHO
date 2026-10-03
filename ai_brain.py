import os
import json
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


# --- Define Pydantic Schema for Discord Server Output ---

class RoleConfig(BaseModel):
    name: str = Field(description="Role name, e.g. Owner, Admin, VIP, or Member")
    color: str = Field(default="#5865f2", description="Hex color code for the role, e.g. #f1c40f")


class ChannelConfig(BaseModel):
    name: str = Field(description="Channel name formatted for Discord, e.g. general-chat or welcome")
    type: str = Field(description="Channel type: 'text' or 'voice'")
    topic: Optional[str] = Field(default="", description="Brief channel topic/purpose")
    emoji: Optional[str] = Field(default="💬", description="Single emoji representing the channel")
    description: Optional[str] = Field(default="", description="Detailed description for channel settings")
    read_only: bool = Field(default=False, description="True if only admins/bots should speak (e.g. announcements/rules)")


class CategoryConfig(BaseModel):
    name: str = Field(description="Category header name, e.g. INFORMATION or COMMUNITY")
    emoji: Optional[str] = Field(default="📁", description="Category header emoji")
    channels: List[ChannelConfig] = Field(description="List of channels inside this category")


class ServerLayoutSchema(BaseModel):
    server_name: str = Field(description="Suggested server name based on prompt")
    description: str = Field(description="A brief description of the server theme")
    roles: List[RoleConfig] = Field(description="List of roles configured for this server layout")
    categories: List[CategoryConfig] = Field(description="List of categories in structured order")


# --- Gemini Generation Handler ---

def generate_server_layout(
    prompt: str,
    channel_separator: str = "│",
    server_id: str = "",
    server_link: str = ""
) -> dict:
    """
    Uses Gemini AI to convert natural language prompt into a structured Discord Server Layout.
    Applies custom channel separators and packages metadata for rendering in the web builder.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set.")

    # Initialize official Google GenAI client
    client = genai.Client(api_key=api_key)

    system_instruction = (
        "You are an expert Discord Community Architect. Your goal is to design a clean, logical, "
        "and well-structured Discord server based on the user's requirements.\n"
        "Guidelines:\n"
        "1. Organize the server into clear, functional categories (e.g., WELCOME, GENERAL, GAMING, VOICE).\n"
        "2. Define default roles with realistic hex colors (e.g., Owner, Admin, Mod, VIP, Member).\n"
        "3. Include reasonable default channels for each category with appropriate text vs. voice types.\n"
        "4. Mark announcement, rules, or info channels as read_only=True.\n"
        "5. Keep channel names lower-case with hyphens or concise with emojis."
    )

    user_query = f"""
    Design a complete Discord server layout for the following theme/request:
    "{prompt}"

    Apply the separator character '{channel_separator}' appropriately where needed for visual layout formatting.
    """

    try:
        # Request strictly-typed JSON matching ServerLayoutSchema
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

        # Parse Gemini's JSON response
        layout_data = json.loads(response.text)

        # Inject custom user preferences & metadata into the root output
        layout_data["build_meta"] = {
            "channel_separator": channel_separator,
            "target_server_id": server_id.strip(),
            "target_server_link": server_link.strip(),
            "prompt_used": prompt
        }

        # Format channel names with custom separator if requested (e.g. "💬 │ general-chat")
        sep = channel_separator.strip() if channel_separator else ""
        for category in layout_data.get("categories", []):
            for channel in category.get("channels", []):
                emoji = channel.get("emoji", "")
                raw_name = channel.get("name", "")
                
                if sep and emoji and not raw_name.startswith(emoji):
                    channel["formatted_name"] = f"{emoji} {sep} {raw_name}"
                else:
                    channel["formatted_name"] = raw_name

        return {
            "success": True,
            "data": layout_data
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# Quick test execution
if __name__ == "__main__":
    test_result = generate_server_layout(
        prompt="A high-tech Cyberpunk Esports Gaming Community",
        channel_separator="│",
        server_id="123456789012345678",
        server_link="https://discord.gg/example"
    )
    print(json.dumps(test_result, indent=2))
