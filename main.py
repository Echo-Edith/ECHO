import os
import threading
import asyncio
import discord
from discord.ext import commands
from discord import app_commands

# Import Flask app from ai_brain.py
from ai_brain import app

# -------------------------------------------------------------
# 1. DISCORD BOT SETUP
# -------------------------------------------------------------
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------------------------------------------
# 2. DEFINE SLASH COMMANDS
# -------------------------------------------------------------

@bot.tree.command(name="status", description="Check if the bot and layout service are operational.")
async def status_command(interaction: discord.Interaction):
    await interaction.response.send_message("🟢 **Dashboard & Bot Status:** All systems operational!", ephemeral=True)

@bot.tree.command(name="help", description="Show information about the Discord Server Builder.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛠️ Discord Server Layout Builder",
        description="Generate and deploy custom Discord server structures seamlessly.",
        color=0x9333EA
    )
    embed.add_field(
        name="Dashboard Link",
        value=f"[Open Dashboard]({os.environ.get('RENDER_EXTERNAL_URL', 'https://echo-dashboard-qn39.onrender.com')})",
        inline=False
    )
    embed.add_field(
        name="How to use",
        value="1. Open the Web Dashboard.\n2. Enter your Target Guild ID and prompt.\n3. Customize categories & hit Submit!",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="custom-server-builder", description="Get the direct link to open the AI Layout Builder.")
async def builder_command(interaction: discord.Interaction):
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com")
    await interaction.response.send_message(
        f"🚀 **Launch Server Builder:** [Click Here to Build Your Server Layout]({url})",
        ephemeral=True
    )

# -------------------------------------------------------------
# 3. BOT EVENTS & SYNC LOGIC
# -------------------------------------------------------------

@bot.event
async def on_ready():
    print(f"✅ Discord Bot logged in as: {bot.user.name} (ID: {bot.user.id})")
    try:
        # Sync registered slash commands with Discord API
        synced = await bot.tree.sync()
        print(f"🔄 Successfully synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")

def start_discord_bot():
    if not BOT_TOKEN:
        print("⚠️ WARNING: 'DISCORD_BOT_TOKEN' environment variable is not set. Bot skipped.")
        return
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        print("⚡ Starting Discord Bot connection...")
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"❌ Failed to start Discord Bot: {e}")

# -------------------------------------------------------------
# 4. RUN THREADS
# -------------------------------------------------------------
if __name__ == '__main__':
    bot_thread = threading.Thread(target=start_discord_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 10000))
    print(f"🚀 Starting Web Server on port {port}...")
    app.run(host="0.0.0.0", port=port)
