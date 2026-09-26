import os
import json
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
            "`/build [file]` - Build server layout from an attached .json file or get the dashboard link\n"
            "`/nuke` - Wipe all existing channels and categories in this server\n"
            "`/status` - Check service status"
        ),
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="build", description="Build server channels from a blueprint .json file or get the website link.")
@app_commands.describe(file="Optional: Upload your generated blueprint .json file here")
@app_commands.checks.has_permissions(administrator=True)
async def build_command(interaction: discord.Interaction, file: discord.Attachment = None):
    # If no file is attached, simply provide the URL
    if not file:
        url = os.environ.get("RENDER_EXTERNAL_URL", "https://echo-dashboard-qn39.onrender.com")
        await interaction.response.send_message(
            f"🚀 **Server Blueprint Builder:** [Click Here to Build Your Layout]({url})\n"
            f"*(Tip: Download your blueprint JSON from the website and use `/build file:<your_file.json>` to apply it automatically!)*",
            ephemeral=True
        )
        return

    # Check file extension
    if not file.filename.endswith(".json"):
        await interaction.response.send_message("❌ Please attach a valid `.json` blueprint file.", ephemeral=True)
        return

    # Defer response to avoid 3-second interaction timeout while building channels
    await interaction.response.defer(ephemeral=True)

    try:
        # Read and parse JSON from file attachment
        content = await file.read()
        blueprint = json.loads(content.decode('utf-8'))

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ This command must be used inside a server.")
            return

        # 1. Process Roles
        created_roles = 0
        roles_list = blueprint.get("roles", [])
        for role_name in roles_list:
            existing_role = discord.utils.get(guild.roles, name=role_name)
            if not existing_role:
                await guild.create_role(name=role_name, reason="Blueprint auto-build")
                created_roles += 1
                await asyncio.sleep(0.2)

        # 2. Process Categories & Channels
        created_cats = 0
        created_channels = 0
        categories = blueprint.get("categories", [])

        for cat_data in categories:
            cat_name = cat_data.get("name", "UNNAMED")
            category = await guild.create_category(name=cat_name)
            created_cats += 1
            await asyncio.sleep(0.2)

            for ch_data in cat_data.get("channels", []):
                emoji = ch_data.get("emoji", "")
                raw_name = ch_data.get("name", "channel")
                ch_type = ch_data.get("type", "text")
                topic = ch_data.get("topic", "")

                full_name = f"{emoji} {raw_name}".strip() if emoji else raw_name

                if ch_type == "voice":
                    await category.create_voice_channel(name=full_name)
                else:
                    await category.create_text_channel(name=full_name, topic=topic)

                created_channels += 1
                await asyncio.sleep(0.2)

        await interaction.followup.send(
            f"✅ **Build Complete!** Created `{created_cats}` categories, `{created_channels}` channels, and `{created_roles}` roles."
        )

    except json.JSONDecodeError:
        await interaction.followup.send("❌ Failed to parse JSON file. Ensure the file contains valid JSON code.")
    except Exception as e:
        print(f"Build error: {e}")
        await interaction.followup.send(f"❌ Error applying blueprint: `{e}`")

@build_command.error
async def build_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You need **Administrator** permissions to build server layouts.", ephemeral=True)

@bot.tree.command(name="nuke", description="⚠️ Delete ALL channels and categories in this server.")
@app_commands.checks.has_permissions(administrator=True)
async def nuke_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ This command can only be used inside a server.")
        return

    deleted_count = 0
    for channel in list(guild.channels):
        try:
            await channel.delete(reason="Nuke command executed by admin.")
            deleted_count += 1
            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Failed to delete channel {channel.name}: {e}")

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
