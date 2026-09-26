import os
import json
import asyncio
import logging
import urllib.request
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("orca_cog")

ORCA_CYAN = discord.Color.from_rgb(6, 182, 212)
ORCA_EMERALD = discord.Color.from_rgb(16, 185, 129)
ORCA_RED = discord.Color.from_rgb(239, 68, 68)
ORCA_PURPLE = discord.Color.from_rgb(147, 51, 234)

AUTHORIZED_OWNER_ID = 1219266886143967245


def create_orca_embed(title: str, description: str, color=ORCA_CYAN) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )
    embed.set_footer(
        text="ORCA AI -- Automated Server Infrastructure"
    )
    return embed


class OrcaCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.web_url = os.getenv("WEB_URL", "https://echo-dashboard-qn39.onrender.com").rstrip("/")

    @commands.Cog.listener()
    async def on_ready(self):
        print("[INFO] ORCA Cog successfully initialized and active.")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            embed = create_orca_embed(
                title="Access Denied",
                description="Administrator permissions are required to execute this command.",
                color=ORCA_RED
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="custom-server-builder",
        description="Launch the interactive website portal to visually design and build your server"
    )
    async def custom_server_builder_command(self, interaction: discord.Interaction):
        builder_url = f"{self.web_url}/"

        embed = create_orca_embed(
            title="ORCA AI -- Custom Server Builder Portal",
            description=(
                "Access our web-based studio to visually model your Discord server layout.\n\n"
                "**Builder Capabilities:**\n"
                "- Live preview of channels, voice lounges, and roles\n"
                "- Fine-tuned role permissions per channel\n"
                "- AI prompt generation engine for custom topic layouts\n"
                "- Instant JSON blueprint export for automated bot deployment\n\n"
                "Click the **Open Server Builder** button below to launch the portal."
            ),
            color=ORCA_CYAN
        )
        embed.add_field(name="Builder Web Portal", value=f"`{builder_url}`", inline=False)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Open Server Builder",
            style=discord.ButtonStyle.link,
            url=builder_url
        ))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="build",
        description="Construct channels, categories, and roles from blueprint (Admin Only)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def build_command(self, interaction: discord.Interaction, file: discord.Attachment = None):
        await interaction.response.defer(ephemeral=True)

        if not file:
            error_embed = create_orca_embed(
                title="Missing Blueprint File",
                description="Please attach a valid `.json` blueprint file exported from the ORCA Server Builder web app.",
                color=ORCA_RED
            )
            await interaction.followup.send(embed=error_embed)
            return

        try:
            content = await file.read()
            data = json.loads(content.decode("utf-8"))
        except Exception as e:
            error_embed = create_orca_embed(
                title="Invalid Blueprint File",
                description=f"Failed to parse JSON content: `{str(e)}`",
                color=ORCA_RED
            )
            await interaction.followup.send(embed=error_embed)
            return

        guild = interaction.guild

        progress_embed = create_orca_embed(
            title="Building Server Architecture...",
            description="Initializing roles, categories, channels, and permission overwrites from design payload...",
            color=ORCA_CYAN
        )
        await interaction.followup.send(embed=progress_embed)

        if "guild_name" in data and data["guild_name"]:
            try:
                await guild.edit(name=data["guild_name"])
            except Exception as e:
                logger.warning(f"Could not update server name: {e}")

        if "guild_icon" in data and data["guild_icon"]:
            try:
                req = urllib.request.Request(data["guild_icon"], headers={'User-Agent': 'ORCA-Bot'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    icon_bytes = resp.read()
                    await guild.edit(icon=icon_bytes)
            except Exception as e:
                logger.warning(f"Could not update server icon: {e}")

        created_roles = 0
        created_channels = 0
        role_map = {}
        separator = data.get("separator", "│")
        sep_prefix = f"{separator} " if separator else ""

        if "roles" in data and isinstance(data["roles"], list):
            for r in data["roles"]:
                try:
                    name = r.get("name", "New Role")
                    color_hex = r.get("color", "#06b6d4").lstrip("#")
                    color = discord.Color(int(color_hex, 16)) if color_hex else discord.Color.default()
                    
                    perms_data = r.get("permissions", {})
                    permissions = discord.Permissions.none()
                    if isinstance(perms_data, dict):
                        if perms_data.get("admin"):
                            permissions.administrator = True
                        if perms_data.get("manage"):
                            permissions.manage_messages = True
                            permissions.manage_channels = True
                        if perms_data.get("send", True):
                            permissions.send_messages = True
                        if perms_data.get("connect", True):
                            permissions.connect = True

                    created_role = await guild.create_role(name=name, color=color, permissions=permissions)
                    role_map[name] = created_role
                    created_roles += 1
                    await asyncio.sleep(0.4)
                except Exception as e:
                    logger.warning(f"Could not create role {r}: {e}")

        if "categories" in data and isinstance(data["categories"], list):
            for cat_data in data["categories"]:
                try:
                    cat_name = cat_data.get("name", "CATEGORY")
                    category = await guild.create_category(name=cat_name)
                    
                    channels = cat_data.get("channels", [])
                    for ch in channels:
                        raw_ch_name = ch.get("name", "channel")
                        formatted_ch_name = f"{sep_prefix}{raw_ch_name}" if ch.get("type") != "voice" else raw_ch_name
                        ch_type = ch.get("type", "text")
                        
                        overwrites = {}
                        ch_perms = ch.get("permissions", {})
                        if isinstance(ch_perms, dict):
                            for role_name, perm_opts in ch_perms.items():
                                target_role = role_map.get(role_name)
                                if not target_role:
                                    target_role = discord.utils.get(guild.roles, name=role_name)
                                
                                if target_role and isinstance(perm_opts, dict):
                                    overwrites[target_role] = discord.PermissionOverwrite(
                                        read_messages=perm_opts.get("view", True),
                                        send_messages=perm_opts.get("send", True),
                                        attach_files=perm_opts.get("attach", True),
                                        administrator=perm_opts.get("admin", False)
                                    )

                        if ch_type == "voice":
                            await guild.create_voice_channel(name=formatted_ch_name, category=category, overwrites=overwrites)
                        else:
                            await guild.create_text_channel(name=formatted_ch_name, category=category, overwrites=overwrites)
                        
                        created_channels += 1
                        await asyncio.sleep(0.4)
                except Exception as e:
                    logger.warning(f"Could not create category {cat_data}: {e}")

        complete_embed = create_orca_embed(
            title="Server Build Complete",
            description=(
                f"Successfully deployed blueprint onto **{guild.name}**.\n\n"
                f"- **Roles Created:** `{created_roles}`\n"
                f"- **Channels Created:** `{created_channels}`"
            ),
            color=ORCA_EMERALD
        )
        await interaction.followup.send(embed=complete_embed)

    @app_commands.command(
        name="lockdown",
        description="Toggle web maintenance lockdown mode (Admin Only)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown_command(self, interaction: discord.Interaction, state: bool):
        if interaction.user.id != AUTHORIZED_OWNER_ID:
            denied_embed = create_orca_embed(
                title="Access Denied",
                description="This emergency command is strictly restricted to the primary system administrator.",
                color=ORCA_RED
            )
            await interaction.response.send_message(embed=denied_embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        status_str = "ACTIVATED" if state else "DEACTIVATED"
        color = ORCA_RED if state else ORCA_EMERALD

        try:
            req_url = f"{self.web_url}/api/lockdown?state={'true' if state else 'false'}"
            req = urllib.request.Request(req_url, headers={'User-Agent': 'ORCA-Bot'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            logger.warning(f"Could not trigger remote web lockdown API: {e}")

        embed = create_orca_embed(
            title=f"Website Lockdown Protocol: {status_str}",
            description=(
                f"The web application status has been updated to **{status_str}**.\n\n"
                f"- **Target Domain:** `{self.web_url}`\n"
                f"- **Executed By:** <@{interaction.user.id}>"
            ),
            color=color
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="status",
        description="Inspect operational metrics and platform state (Admin Only)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def status_command(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        
        embed = create_orca_embed(
            title="ORCA AI -- Operational Status",
            description="Real-time bot performance and platform metrics.",
            color=ORCA_CYAN
        )
        embed.add_field(name="Bot Latency", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="Connected Guilds", value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.add_field(name="System Health", value="`ONLINE & OPERATIONAL`", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="help",
        description="Display command reference directory (Admin Only)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def help_command(self, interaction: discord.Interaction):
        embed = create_orca_embed(
            title="ORCA AI -- Command Reference Directory",
            description="Overview of available slash commands for building and managing server structures.",
            color=ORCA_PURPLE
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
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))
