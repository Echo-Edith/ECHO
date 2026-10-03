import os
import asyncio
import aiohttp
import discord
from discord.ext import commands
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from keep_alive import keep_alive
from ai_brain import generate_server_layout, send_webhook_log

# Initialize Discord Bot with full privileged intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Fetch Environment Variables
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY", "")
RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY", "")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")

# Specific Webhook Environment Variables
DESIGN_WEBHOOK_URL = os.getenv("DESIGN_WEBHOOK_URL", "").strip()
WEBSITE_WEBHOOK_URL = os.getenv("WEBSITE_WEBHOOK_URL", "").strip()


@bot.event
async def on_ready():
    print(f"✅ Bot is ONLINE and logged in as: {bot.user} (ID: {bot.user.id})")
    print("--- WEBHOOK CONFIGURATION CHECK ---")
    print(f"🔗 WEBSITE_WEBHOOK_URL: {'LOADED (' + WEBSITE_WEBHOOK_URL[:30] + '...)' if WEBSITE_WEBHOOK_URL else '❌ MISSING'}")
    print(f"🔗 DESIGN_WEBHOOK_URL:  {'LOADED (' + DESIGN_WEBHOOK_URL[:30] + '...)' if DESIGN_WEBHOOK_URL else '❌ MISSING'}")
    print("-----------------------------------")
    try:
        synced = await bot.tree.sync()
        print(f"⚡ Successfully synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")


async def dispatch_webhook_safely(url: str, title: str, user_info: dict, details: dict, color: int):
    """Wrapper function to guarantee errors are printed to console if Discord rejects the webhook."""
    if not url:
        print(f"⚠️ [WEBHOOK ERROR] Cannot send '{title}' — URL environment variable is empty!")
        return

    try:
        await send_webhook_log(
            webhook_url=url,
            title=title,
            user_info=user_info,
            action_details=details,
            color=color
        )
        print(f"🚀 [WEBHOOK SUCCESS] Dispatched log for: {title}")
    except Exception as e:
        print(f"❌ [WEBHOOK EXCEPTION] Failed to dispatch '{title}': {e}")


# --- API ROUTES ---

def configure_routes(app: Flask):

    @app.route("/")
    def index():
        user = session.get("user")
        return render_template(
            "index.html",
            user=user,
            recaptcha_site_key=RECAPTCHA_SITE_KEY,
            discord_client_id=DISCORD_CLIENT_ID
        )

    @app.route("/login")
    def login():
        discord_auth_url = (
            f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
            f"&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify"
        )
        return redirect(discord_auth_url)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/")

    @app.route("/api/verify-captcha", methods=["POST"])
    async def verify_captcha():
        data = request.get_json() or {}
        token = data.get("token")
        if not token:
            return jsonify({"success": False, "error": "Missing token"}), 400

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                "https://www.google.com/recaptcha/api/siteverify",
                data={"secret": RECAPTCHA_SECRET_KEY, "response": token}
            ) as resp:
                result = await resp.json()
                if result.get("success"):
                    session["captcha_verified"] = True
                    return jsonify({"success": True})
                return jsonify({"success": False, "error": "reCAPTCHA verification failed"}), 400

    @app.route("/api/generate", methods=["POST"])
    async def generate():
        data = request.get_json() or {}
        prompt = data.get("prompt", "").strip()
        server_id = data.get("server_id", "").strip()
        server_link = data.get("server_link", "").strip()

        if not prompt:
            return jsonify({"success": False, "error": "Prompt description is required."}), 400

        user_info = session.get("user", {"id": "0", "username": "Anonymous User"})

        try:
            result = await generate_server_layout(
                prompt=prompt,
                server_id=server_id,
                server_link=server_link,
                user_info=user_info,
                webhook_url=WEBSITE_WEBHOOK_URL
            )
            return jsonify(result)
        except Exception as e:
            print(f"❌ Generation Error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/submit-design", methods=["POST"])
    async def submit_design():
        data = request.get_json() or {}
        target_server_id = data.get("target_server_id", "Not Provided")
        server_name = data.get("server_name", "Custom Server")
        invite_link = data.get("invite_link", "Not Provided")
        prompt = data.get("prompt", "Not Provided")
        categories_count = data.get("categories_count", 0)
        channels_count = data.get("channels_count", 0)
        roles_count = data.get("roles_count", 0)

        user_info = session.get("user", {"id": "0", "username": "Anonymous User"})

        await dispatch_webhook_safely(
            url=DESIGN_WEBHOOK_URL,
            title="🚀 New Server Blueprint Submitted",
            user_info=user_info,
            details={
                "Server Name": server_name,
                "Target Server ID": target_server_id,
                "Invite Link": invite_link,
                "Prompt Description": prompt,
                "Architecture Overview": f"Categories: `{categories_count}` | Channels: `{channels_count}` | Roles: `{roles_count}`"
            },
            color=0x2ecc71
        )

        return jsonify({"success": True, "message": "Blueprint successfully dispatched!"})


async def load_cogs():
    try:
        await bot.load_extension("cogs.orca")
    except Exception as e:
        print(f"⚠️ Could not load cog 'cogs.orca': {e}")


async def main():
    # 1. Start background web server & register API endpoints
    flask_app = keep_alive()
    if flask_app:
        configure_routes(flask_app)
    
    # 2. Verify token and start Discord Bot
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("CRITICAL: DISCORD_BOT_TOKEN environment variable is missing!")

    async with bot:
        await load_cogs()
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
