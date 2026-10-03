import os
import json
import requests
import discord
from discord.ext import commands
from flask import request, jsonify, session

from keep_alive import app, keep_alive
from ai_brain import generate_server_layout

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DESIGN_WEBHOOK_URL = os.getenv("DESIGN_WEBHOOK_URL")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://echo-dashboard-qn39.onrender.com/")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    separator = data.get("separator", "│")
    server_id = data.get("server_id", "")
    server_link = data.get("server_link", "")

    result = generate_server_layout(
        prompt=prompt,
        channel_separator=separator,
        server_id=server_id,
        server_link=server_link
    )
    return jsonify(result)


@app.route("/api/submit", methods=["POST"])
def api_submit():
    layout_data = request.get_json() or {}
    user_info = session.get("user", {"id": "Unknown", "username": "Anonymous"})
    build_meta = layout_data.get("build_meta", {})

    target_server_id = build_meta.get("target_server_id", "N/A")
    target_server_link = build_meta.get("target_server_link", "N/A")

    if DESIGN_WEBHOOK_URL:
        webhook_payload = {
            "username": "ORCA Architect Dispatcher",
            "embeds": [{
                "title": "🏗️ New Server Design Submitted",
                "color": 0x00f2fe,
                "fields": [
                    {"name": "👤 Designer User", "value": f"**{user_info.get('username')}** (`{user_info.get('id')}`)", "inline": True},
                    {"name": "🎯 Target Guild ID", "value": f"`{target_server_id}`", "inline": True},
                    {"name": "🔗 Website Link", "value": f"[{DASHBOARD_URL}]({DASHBOARD_URL})", "inline": True}
                ],
                "description": "**Run command in Discord to build server:**\n`/build file: server_layout.json`"
            }]
        }
        try:
            requests.post(DESIGN_WEBHOOK_URL, json=webhook_payload)
            json_file_content = json.dumps(layout_data, indent=2)
            requests.post(DESIGN_WEBHOOK_URL, files={"file": ("server_layout.json", json_file_content, "application/json")})
        except Exception as e:
            print(f"Webhook dispatch failed: {e}")

    return jsonify({"success": True}), 200


@bot.event
async def on_ready():
    print(f"Bot active as {bot.user}")
    try:
        await bot.load_extension("cogs.orca")
        await bot.tree.sync()
    except Exception as e:
        print(f"Setup error: {e}")


def main():
    keep_alive()
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
