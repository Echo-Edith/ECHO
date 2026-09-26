import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import time
import os

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Global Maintenance Lockdown State
is_lockdown = False
start_time = time.time()

# Configuration
WEB_BUILDER_URL = "https://customserver-shadowspire.vercel.app/"


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


# --- 1. /help COMMAND ---
@bot.tree.command(name="help", description="Displays the ORCA AI command reference directory.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="ORCA AI -- Command Reference Directory",
        description="Overview of available slash commands for building and managing server structures.",
        color=0x5865F2
    )
    
    embed.add_field(
        name="`/custom-server-builder`",
        value="Provides link to the web-based interactive Discord server layout tool. *(Public)*",
        inline=False
    )
    embed.add_field(
        name="`/build [file]`",
        value="Builds server categories, channels, and roles from JSON blueprint. *(Admin Only)*",
        inline=False
    )
    embed.add_field(
        name="`/lockdown [state]`",
        value="Toggles web portal maintenance screen. *(Admin Only)*",
        inline=False
    )
    embed.add_field(
        name="`/status`",
        value="Displays real-time bot latency and operational statistics. *(Admin Only)*",
        inline=False
    )
    embed.add_field(
        name="`/nuke`",
        value="Deletes all channels and categories, preserving only the current bot channel. *(Admin Only)*",
        inline=False
    )
    
    embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- 2. /custom-server-builder COMMAND ---
@bot.tree.command(name="custom-server-builder", description="Provides link to the web-based layout tool.")
async def custom_server_builder(interaction: discord.Interaction):
    if is_lockdown:
        await interaction.response.send_message("⚠️ The web portal is currently under maintenance. Please try again later.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🛠️ Interactive Server Builder",
        description=f"Click below to access our AI-powered web builder:\n{WEB_BUILDER_URL}",
        color=0x5865F2
    )
    await interaction.response.send_message(embed=embed)


# --- 3. /build COMMAND (Full Wipe & Construction) ---
@bot.tree.command(name="build", description="Builds server categories, channels, and roles from JSON blueprint.")
@app_commands.checks.has_permissions(administrator=True)
async def build(interaction: discord.Interaction, file: discord.Attachment):
    if not file.filename.endswith('.json'):
        await interaction.response.send_message("❌ Error: Attached file must be a JSON blueprint.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    
    # Read attached JSON blueprint
    try:
        content = await file.read()
        blueprint = json.loads(content.decode('utf-8'))
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to parse JSON blueprint: {e}")
        return

    guild = interaction.guild

    # 1. Purge Existing Channels (Keep current channel temporarily to log status)
    current_channel = interaction.channel
    for channel in guild.channels:
        if channel.id != current_channel.id:
            try:
                await channel.delete()
            except Exception:
                pass

    # 2. Purge Existing Roles (Excluding @everyone, managed bot roles, and top roles)
    for role in guild.roles:
        if role.name != "@everyone" and not role.managed and role < guild.me.top_role:
            try:
                await role.delete()
            except Exception:
                pass

    roles_created = 0
    channels_created = 0

    # 3. Create Roles
    role_map = {}
    for role_name in blueprint.get("roles", []):
        if role_name == "everyone":
            continue
        try:
            new_role = await guild.create_role(name=role_name, mentionable=True)
            role_map[role_name] = new_role
            roles_created += 1
        except Exception as e:
            print(f"Error creating role {role_name}: {e}")

    # 4. Create Categories & Channels
    for cat_data in blueprint.get("categories", []):
        category = await guild.create_category(name=cat_data.get("name", "Category"))
        
        for ch_data in cat_data.get("channels", []):
            ch_name = f"{ch_data.get('emoji', '')} {ch_data.get('name', 'channel')}".strip()
            ch_type = ch_data.get("type", "text")
            topic = ch_data.get("topic", "")

            if ch_type == "voice":
                await guild.create_voice_channel(name=ch_name, category=category)
            else:
                news_type = (ch_type == "announcement")
                await guild.create_text_channel(name=ch_name, category=category, topic=topic, news=news_type)
            
            channels_created += 1

    # Delete original temp execution channel
    try:
        await current_channel.delete()
    except Exception:
        pass

    # Find first available text channel to post completion status
    target_channel = guild.text_channels[0] if guild.text_channels else None
    
    if target_channel:
        embed = discord.Embed(
            title="Server Build Complete",
            description=f"Successfully deployed blueprint onto **{guild.name}**.",
            color=0x2ECC71
        )
        embed.add_field(name="• Roles Created", value=str(roles_created), inline=False)
        embed.add_field(name="• Channels Created", value=str(channels_created), inline=False)
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        
        await target_channel.send(embed=embed)


# --- 4. /lockdown COMMAND ---
@bot.tree.command(name="lockdown", description="Toggles web portal maintenance screen.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(state=[
    app_commands.Choice(name="ON (Enable Maintenance)", value="on"),
    app_commands.Choice(name="OFF (Disable Maintenance)", value="off")
])
async def lockdown(interaction: discord.Interaction, state: app_commands.Choice[str]):
    global is_lockdown
    is_lockdown = (state.value == "on")
    
    status_str = "ENABLED" if is_lockdown else "DISABLED"
    embed = discord.Embed(
        title="🔒 Web Portal Maintenance Screen",
        description=f"Maintenance mode is now **{status_str}**.",
        color=0xE74C3C if is_lockdown else 0x2ECC71
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- 5. /status COMMAND ---
@bot.tree.command(name="status", description="Displays real-time bot latency and operational statistics.")
@app_commands.checks.has_permissions(administrator=True)
async def status(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    uptime = round(time.time() - start_time)
    
    embed = discord.Embed(
        title="⚡ System Operational Status",
        color=0x5865F2
    )
    embed.add_field(name="Latency", value=f"{latency} ms", inline=True)
    embed.add_field(name="Uptime", value=f"{uptime} seconds", inline=True)
    embed.add_field(name="Maintenance Lock", value="ACTIVE" if is_lockdown else "INACTIVE", inline=True)
    embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- 6. /nuke COMMAND ---
@bot.tree.command(name="nuke", description="Deletes all channels/categories except the command channel.")
@app_commands.checks.has_permissions(administrator=True)
async def nuke(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    guild = interaction.guild
    current_channel = interaction.channel

    deleted_count = 0
    for channel in guild.channels:
        if channel.id != current_channel.id:
            try:
                await channel.delete()
                deleted_count += 1
            except Exception as e:
                print(f"Failed to delete channel {channel.name}: {e}")

    embed = discord.Embed(
        title="💥 Server Nuked",
        description=f"Purged **{deleted_count}** channels and categories. Only this channel was preserved.",
        color=0xE74C3C
    )
    embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
    await interaction.followup.send(embed=embed)


# --- ERROR HANDLERS ---
@build.error
@lockdown.error
@status.error
@nuke.error
async def admin_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You do not have Permission to run this command. (Admin Only)", ephemeral=True)

# Run the bot
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    bot.run(TOKEN)
