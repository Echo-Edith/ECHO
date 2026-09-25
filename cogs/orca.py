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
        builder_url = "https://echo-dashboard-qn39.onrender.com/"

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
        description="Automatically construct channels, categories, and roles from an uploaded JSON blueprint"
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
            description="Initializing roles, categories, and channels from design payload...",
            color=ORCA_CYAN
        )
        await interaction.followup.send(embed=progress_embed)

        created_roles = 0
        created_channels = 0
        separator = data.get("separator", "│")
        sep_prefix = f"{separator} " if separator else ""

        if "roles" in data and isinstance(data["roles"], list):
            for r in data["roles"]:
                try:
                    name = r.get("name", "New Role")
                    color_hex = r.get("color", "#06b6d4").lstrip("#")
                    color = discord.Color(int(color_hex, 16)) if color_hex else discord.Color.default()
                    await guild.create_role(name=name, color=color)
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

                        if ch_type == "voice":
                            await guild.create_voice_channel(name=formatted_ch_name, category=category)
                        else:
                            await guild.create_text_channel(name=formatted_ch_name, category=category)
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
        description="Toggle emergency deep-sea maintenance lockdown for the web server (Owner Only)"
    )
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
                f"- **Maintenance Screen:** `Deep-Sea Abyss Mode`\n"
                f"- **Executed By:** <@{interaction.user.id}>"
            ),
            color=color
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="status",
        description="Inspect ORCA bot operational metrics, WebSocket latency, and active clusters"
    )
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
        description="Display the master directory of available ORCA AI administrative and user commands"
    )
    async def help_command(self, interaction: discord.Interaction):
        embed = create_orca_embed(
            title="ORCA AI -- Command Reference Directory",
            description="Overview of available slash commands for building and managing server structures.",
            color=ORCA_PURPLE
        )
        embed.add_field(
            name="`/custom-server-builder`",
            value="Provides link to the web-based interactive Discord server layout tool.",
            inline=False
        )
        embed.add_field(
            name="`/build [file]`",
            value="Builds server categories, channels, and roles directly from an uploaded JSON blueprint.",
            inline=False
        )
        embed.add_field(
            name="`/lockdown [state]`",
            value="Toggles web portal maintenance screen with deep-sea anomalies *(Restricted to Owner)*.",
            inline=False
        )
        embed.add_field(
            name="`/status`",
            value="Displays real-time bot latency and operational statistics.",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))
