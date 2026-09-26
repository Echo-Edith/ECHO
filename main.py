import os
import threading
import asyncio
import discord
from discord.ext import commands
from discord import app_commands

from ai_brain import app

# -------------------------------------------------------------
# 1. DISCORD BOT SETUP
# -------------------------------------------------------------
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

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
        name="Commands",
        value=(
            "`/build` - Open the dashboard layout builder link\n"
            "`/nuke` - Wipe all existing channels and categories in this server\n"
            "`/status` - Check service status"
        ),
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="build", description="Get the direct link to build and generate layout blueprints.")
async def build_command(interaction: discord.Interaction):
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com")
    await interaction.response.send_message(
        f"🚀 **Server Blueprint Builder:** [Click Here to Build Your Layout]({url})",
        ephemeral=True
    )

@bot.tree.command(name="custom-server-builder", description="Get the direct link to open the AI Layout Builder.")
async def builder_alias_command(interaction: discord.Interaction):
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com")
    await interaction.response.send_message(
        f"🚀 **Launch Server Builder:** [Click Here to Build Your Server Layout]({url})",
        ephemeral=True
    )

@bot.tree.command(name="nuke", description="⚠️ Delete ALL channels and categories in this server.")
@app_commands.checks.has_permissions(administrator=True)
async def nuke_command(interaction: discord.Interaction):
    # Defer response immediately so Discord doesn't report "Application Didn't Respond"
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ This command can only be used inside a server.")
        return

    deleted_count = 0
    # Delete channels and categories
    for channel in list(guild.channels):
        try:
            await channel.delete(reason="Nuke command executed by admin.")
            deleted_count += 1
            await asyncio.sleep(0.2)  # Avoid hitting Discord rate limits
        except Exception as e:
            print(f"Failed to delete channel {channel.name}: {e}")

    # Create a fresh general channel so the server isn't empty
    try:
        new_ch = await guild.create_text_channel("general", topic="Server reset successfully.")
        await new_ch.send(f"💥 **Server Nuked!** Cleared `{deleted_count}` channel(s) by {interaction.user.mention}.")
    except Exception as e:
        print(f"Failed to create fresh text channel: {e}")

@nuke_command.error
async def nuke_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need **Administrator** permissions to use `/nuke`.", ephemeral=True)

# -------------------------------------------------------------
# 3. BOT EVENTS & SYNC LOGIC
# -------------------------------------------------------------

@bot.event
async def on_ready():
    print(f"✅ Discord Bot logged in as: {bot.user.name} (ID: {bot.user.id})")
    try:
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
