import time
import json
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# Import blueprint retrieval helper and dynamic web URL from ai_brain.py
from ai_brain import get_blueprint_data, WEB_BUILDER_URL

AUTHORIZED_USER_ID = 1219266886143967245
is_lockdown = False
start_time = time.time()


def is_owner():
    """Custom check restricting administrative commands strictly to AUTHORIZED_USER_ID."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == AUTHORIZED_USER_ID:
            return True
        embed = discord.Embed(
            title="⛔ Access Denied",
            description="You do not have permission to execute this command.",
            color=0xE74C3C
        )
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return app_commands.check(predicate)


class OrcaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- 1. /help COMMAND (PUBLIC) ---
    @app_commands.command(name="help", description="Learn how to create and deploy a custom Discord server.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ How to Create & Deploy a Custom Discord Server",
            description=(
                "ORCA AI lets you automatically generate and deploy complete server layouts "
                "including categories, channels, and roles in seconds!\n\n"
                "### 📋 Step-by-Step Guide:\n\n"
                f"1️⃣ **Design on Web Builder**\n"
                f"Go to the [ORCA Web Builder]({WEB_BUILDER_URL}) and enter your prompt, Target Server ID, and Invite Link.\n\n"
                "2️⃣ **Generate Layout**\n"
                "Click **Generate Layout** to preview the AI-generated categories, channels, and roles.\n\n"
                "3️⃣ **Submit Design**\n"
                "Click **Submit Design**. A unique direct file link / code will be generated and logged.\n\n"
                "4️⃣ **Deploy via `/build`**\n"
                "Authorized staff can run `/build file:<link_or_code>` or attach the blueprint JSON file to deploy instantly."
            ),
            color=0x5865F2
        )
        embed.add_field(
            name="🌐 Web Builder Link",
            value=f"[Click Here to Launch Web Builder]({WEB_BUILDER_URL})",
            inline=False
        )
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 2. /custom-server-builder COMMAND (PUBLIC) ---
    @app_commands.command(name="custom-server-builder", description="Provides the link to the web-based layout builder.")
    async def custom_server_builder(self, interaction: discord.Interaction):
        if is_lockdown:
            embed = discord.Embed(
                title="⚠️ System Under Maintenance",
                description="The web portal is currently undergoing maintenance. Please try again later.",
                color=0xF1C40F
            )
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠️ Interactive Server Builder",
            description=f"Click below to launch the AI Web Builder:\n\n🔗 **[ORCA Web Builder Portal]({WEB_BUILDER_URL})**",
            color=0x5865F2
        )
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 3. /server-info COMMAND (PUBLIC/STAFF) ---
    @app_commands.command(name="server-info", description="Displays formatted Server ID and Invite Link in copyable code blocks.")
    @app_commands.describe(
        server_id="Target Discord Server ID (optional if executed inside a server)",
        invite_link="Invite URL to the target server (optional)"
    )
    async def server_info(
        self, 
        interaction: discord.Interaction, 
        server_id: str = None, 
        invite_link: str = None
    ):
        target_id = server_id.strip() if server_id else (str(interaction.guild.id) if interaction.guild else "N/A")
        
        # If no invite link provided, try to create or lookup one if in a guild
        formatted_invite = invite_link.strip() if invite_link else None
        if not formatted_invite and interaction.guild:
            try:
                # Find first channel where bot can create an invite
                for channel in interaction.guild.text_channels:
                    if channel.permissions_for(interaction.guild.me).create_instant_invite:
                        inv = await channel.create_invite(max_age=0, max_uses=0)
                        formatted_invite = inv.url
                        break
            except Exception:
                pass

        if not formatted_invite:
            formatted_invite = "N/A"

        guild_name = interaction.guild.name if interaction.guild else "Server Details"

        embed = discord.Embed(
            title=f"📌 {guild_name} — Information",
            description="Copy the Server ID or Invite Link below for use in the ORCA Web Builder:",
            color=0x5865F2
        )
        embed.add_field(
            name="🆔 Server ID",
            value=f"`{target_id}`",
            inline=False
        )
        embed.add_field(
            name="🔗 Invite Link",
            value=f"`{formatted_invite}`",
            inline=False
        )
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 4. /build COMMAND (OWNER ONLY) ---
    @app_commands.command(name="build", description="Builds server layout from blueprint URL, 5-digit code, or uploaded JSON file.")
    @app_commands.describe(
        file="Direct file link, HTTP URL, or 5-digit build code",
        attachment="Optional JSON blueprint file attachment"
    )
    @is_owner()
    async def build(
        self, 
        interaction: discord.Interaction, 
        file: str = None, 
        attachment: discord.Attachment = None
    ):
        await interaction.response.defer(thinking=True)

        blueprint = None
        source_identifier = "Unknown"

        # Case 1: Attachment provided
        if attachment:
            if not attachment.filename.endswith(".json"):
                embed = discord.Embed(
                    title="❌ Invalid File Format",
                    description="Please attach a valid `.json` blueprint file.",
                    color=0xE74C3C
                )
                embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
                await interaction.followup.send(embed=embed)
                return
            
            try:
                content = await attachment.read()
                blueprint = json.loads(content.decode("utf-8"))
                source_identifier = attachment.filename
            except Exception as e:
                embed = discord.Embed(
                    title="❌ File Parse Error",
                    description=f"Failed to read attached JSON file: `{e}`",
                    color=0xE74C3C
                )
                embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
                await interaction.followup.send(embed=embed)
                return

        # Case 2: URL or Code string provided
        elif file:
            clean_file = file.strip()
            source_identifier = clean_file

            if clean_file.startswith("http://") or clean_file.startswith("https://"):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(clean_file, timeout=10) as resp:
                            if resp.status == 200:
                                blueprint = await resp.json()
                            else:
                                embed = discord.Embed(
                                    title="❌ Download Error",
                                    description=f"Received status code `{resp.status}` when fetching URL.",
                                    color=0xE74C3C
                                )
                                embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
                                await interaction.followup.send(embed=embed)
                                return
                except Exception as e:
                    embed = discord.Embed(
                        title="❌ Network Request Failed",
                        description=f"Could not download blueprint from link: `{e}`",
                        color=0xE74C3C
                    )
                    embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
                    await interaction.followup.send(embed=embed)
                    return
            else:
                # Treat as 5-digit code lookup
                blueprint = get_blueprint_data(clean_file)

        else:
            embed = discord.Embed(
                title="❌ Missing Blueprint Input",
                description="Please provide a file URL/code in the `file:` parameter or attach a `.json` file.",
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        if not blueprint:
            embed = discord.Embed(
                title="❌ Blueprint Not Found",
                description=f"Could not locate or load blueprint data from `{source_identifier}`.",
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        # Verification Checks
        target_guild_id = str(blueprint.get("target_guild_id", "")).strip()
        current_guild_id = str(interaction.guild.id)

        # Check Guild ID Match
        if target_guild_id and target_guild_id != current_guild_id:
            embed = discord.Embed(
                title="⛔ Build Denied — Server ID Mismatch",
                description=(
                    f"Blueprint was generated for Server ID `{target_guild_id}`, "
                    f"but you are executing it in Server ID `{current_guild_id}`."
                ),
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        guild = interaction.guild
        current_channel = interaction.channel

        # 1. Purge Channels (except current channel executing command)
        for channel in guild.channels:
            if channel.id != current_channel.id:
                try:
                    await channel.delete()
                except Exception:
                    pass

        # 2. Purge Roles
        for role in guild.roles:
            if role.name != "@everyone" and not role.managed and role < guild.me.top_role:
                try:
                    await role.delete()
                except Exception:
                    pass

        roles_created = 0
        channels_created = 0

        # 3. Create Roles
        for role_name in blueprint.get("roles", []):
            if role_name.lower() in ["everyone", "@everyone"]:
                continue
            try:
                await guild.create_role(name=role_name, mentionable=True)
                roles_created += 1
            except Exception:
                pass

        # 4. Create Categories & Channels
        for cat_data in blueprint.get("categories", []):
            category = await guild.create_category(name=cat_data.get("name", "CATEGORY"))
            for ch_data in cat_data.get("channels", []):
                emoji = ch_data.get("emoji", "").strip()
                ch_name = ch_data.get("name", "channel").strip()
                full_name = f"{emoji} {ch_name}".strip() if emoji else ch_name
                
                ch_type = ch_data.get("type", "text")
                topic = ch_data.get("topic", "")

                if ch_type == "voice":
                    await guild.create_voice_channel(name=full_name, category=category)
                else:
                    await guild.create_text_channel(
                        name=full_name, 
                        category=category, 
                        topic=topic, 
                        news=(ch_type == "announcement")
                    )
                channels_created += 1

        # Delete command execution channel
        try:
            await current_channel.delete()
        except Exception:
            pass

        # Send completion embed into the first created text channel
        target_channel = guild.text_channels[0] if guild.text_channels else None
        if target_channel:
            embed = discord.Embed(
                title="🚀 Server Build Complete",
                description=f"Successfully deployed layout **`{blueprint.get('server_name', 'Discord Server')}`** onto **{guild.name}**.",
                color=0x2ECC71
            )
            embed.add_field(name="🔑 Source", value=f"`{source_identifier[:40]}`", inline=True)
            embed.add_field(name="• Roles Created", value=f"`{roles_created}`", inline=True)
            embed.add_field(name="• Channels Created", value=f"`{channels_created}`", inline=True)
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await target_channel.send(embed=embed)

    # --- 5. /lockdown COMMAND (OWNER ONLY) ---
    @app_commands.command(name="lockdown", description="Toggles web portal maintenance screen.")
    @is_owner()
    @app_commands.choices(state=[
        app_commands.Choice(name="ON (Enable Maintenance)", value="on"),
        app_commands.Choice(name="OFF (Disable Maintenance)", value="off")
    ])
    async def lockdown(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        global is_lockdown
        is_lockdown = (state.value == "on")
        status_str = "ENABLED" if is_lockdown else "DISABLED"
        embed = discord.Embed(
            title="🔒 Web Portal Maintenance Screen",
            description=f"Maintenance mode is now **{status_str}**.",
            color=0xE74C3C if is_lockdown else 0x2ECC71
        )
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 6. /status COMMAND (OWNER ONLY) ---
    @app_commands.command(name="status", description="Displays real-time bot latency and operational statistics.")
    @is_owner()
    async def status(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        uptime = round(time.time() - start_time)
        embed = discord.Embed(title="⚡ System Operational Status", color=0x5865F2)
        embed.add_field(name="Latency", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="Uptime", value=f"`{uptime} s`", inline=True)
        embed.add_field(name="Maintenance Lock", value="`ACTIVE`" if is_lockdown else "`INACTIVE`", inline=True)
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 7. /nuke COMMAND (OWNER ONLY) ---
    @app_commands.command(name="nuke", description="Deletes all channels/categories except the command channel.")
    @is_owner()
    async def nuke(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        guild = interaction.guild
        current_channel = interaction.channel

        deleted_count = 0
        for channel in guild.channels:
            if channel.id != current_channel.id:
                try:
                    await channel.delete()
                    deleted_count += 1
                except Exception:
                    pass

        embed = discord.Embed(
            title="💥 Server Nuked",
            description=f"Purged **{deleted_count}** channels and categories. Preserved this execution channel.",
            color=0xE74C3C
        )
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.followup.send(embed=embed)


# REQUIRED SETUP ENTRY POINT FOR DISCORD.PY EXTENSIONS
async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))
