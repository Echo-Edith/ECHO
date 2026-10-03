import os
import json
import requests
import asyncio
import discord
from discord.ext import commands
from flask import request, jsonify, session

# Import core modules
from keep_alive import app, keep_alive
from ai_brain import generate_server_layout

# Environment Variables
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DESIGN_WEBHOOK_URL = os.getenv("DESIGN_WEBHOOK_URL")

# Bot Setup with Command Prefix and Intents
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =============================================================================
# FLASK REST API ENDPOINTS
# =============================================================================

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """
    API route called by index.html JavaScript to trigger Gemini layout generation.
    """
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    separator = data.get("separator", "│")
    server_id = data.get("server_id", "")
    server_link = data.get("server_link", "")

    if not prompt or not server_id:
        return jsonify({"success": False, "error": "Prompt and Target Server ID are required."}), 400

    # Call Gemini generation engine in ai_brain.py
    result = generate_server_layout(
        prompt=prompt,
        channel_separator=separator,
        server_id=server_id,
        server_link=server_link
    )

    return jsonify(result)


@app.route("/api/submit", methods=["POST"])
def api_submit():
    """
    API route called when user submits design. Formats layout JSON, packages user/build info,
    and dispatches a webhook with the /build file: prompt.
    """
    layout_data = request.get_json() or {}
    user_info = session.get("user", {"id": "Unknown", "username": "Anonymous"})
    build_meta = layout_data.get("build_meta", {})

    target_server_id = build_meta.get("target_server_id", "N/A")
    target_server_link = build_meta.get("target_server_link", "N/A")

    # Construct Webhook Payload for Discord Channel
    webhook_payload = {
        "username": "ORCA Architect Dispatcher",
        "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
        "embeds": [
            {
                "title": "🏗️ New Server Design Submitted",
                "color": 47103,  # Cyan gradient matching Glassmorphism theme
                "fields": [
                    {
                        "name": "👤 Designer User",
                        "value": f"**{user_info.get('username')}** (`{user_info.get('id')}`)",
                        "inline": True
                    },
                    {
                        "name": "🎯 Target Guild ID",
                        "value": f"`{target_server_id}`",
                        "inline": True
                    },
                    {
                        "name": "🔗 Server Link",
                        "value": f"[Invite Link]({target_server_link})" if target_server_link.startswith("http") else target_server_link,
                        "inline": True
                    },
                    {
                        "name": "📊 Total Categories",
                        "value": str(len(layout_data.get("categories", []))),
                        "inline": True
                    }
                ],
                "description": (
                    "**To execute this build, save the layout JSON and run:**\n"
                    "```/build file: <ATTACH_DESIGN_JSON>```"
                ),
                "footer": {"text": "ORCA Architect System"}
            }
        ]
    }

    # Attach formatted raw layout JSON payload into webhook payload if needed
    if DESIGN_WEBHOOK_URL:
        try:
            # Send embed message first
            requests.post(DESIGN_WEBHOOK_URL, json=webhook_payload)

            # Send layout JSON file payload directly to webhook for easy downloading
            json_file_content = json.dumps(layout_data, indent=2)
            files = {
                "file": ("server_layout.json", json_file_content, "application/json")
            }
            requests.post(DESIGN_WEBHOOK_URL, files=files)

        except Exception as e:
            print(f"Webhook dispatch failed: {e}")

    return jsonify({"success": True, "message": "Design submitted successfully."}), 200


# =============================================================================
# DISCORD BOT EVENT HANDLERS & INITIALIZATION
# =============================================================================

@bot.event
async def on_ready():
    """Fired when bot connects to Discord gateway."""
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    
    # Load cogs extension
    try:
        await bot.load_extension("cogs.orca")
        print("Successfully loaded cogs/orca.py")
    except Exception as e:
        print(f"Failed to load cog: {e}")

    # Sync slash commands with Discord global registry
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands globally.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    if not DISCORD_BOT_TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN environment variable is missing!")

    # 1. Start Flask web server background thread
    keep_alive()

    # 2. Start Discord Bot on main thread
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
