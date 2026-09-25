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

def create_orca_embed(title: str, description: str, color=ORCA_CYAN) -> discord.Embed:
    """Helper function to build uniform, styled Discord embeds for all bot responses."""
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
    """Core Cog for ORCA AI handling server generation, custom-server linking, dashboard access, and moderation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.web_url = os.getenv("WEB_URL", "https://orca-seven-opal.vercel.app").rstrip("/")
        self.dashboard_url = os.getenv("DASHBOARD_URL", "https://echo-dashboard-qn39.onrender.com").rstrip("/")

    @commands.Cog.listener()
    async def on_ready(self):
        print("[INFO] ORCA Cog successfully initialized and active.")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Global error handler for commands in this cog to ensure embed response on permission failure."""
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
        name="custom-server",
        description="Get the interactive design link to build your custom Discord server layout"
    )
    async def custom_server_command(self, interaction: discord.Interaction):
        """Sends a rich embedded link to the custom server builder UI for all users."""
        buyer_url = f"{self.web_url}/"

        embed = create_orca_embed(
            title="ORCA AI -- Custom Server Builder",
            description=(
                "Design your ideal Discord server structure in real-time.\n\n"
                "Features:\n"
                "- Live visual channel and role builder\n"
                "- Custom categories and channels up to Discord limits\n"
                "- AI layout prompt regenerator with named channels\n"
                "- Direct payload submission to staff command & Webhook logger\n\n"
                "Click the 'Open Server Builder' button below to start building."
            ),
            color=ORCA_CYAN
        )
        embed.add_field(name="Portal Link", value=f"`{buyer_url}`", inline=False)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Open Server Builder",
            style=discord.ButtonStyle.link,
            url=buyer_url
        ))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="dashboard",
        description="Access the staff administration dashboard"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def dashboard_command(self, interaction: discord.Interaction):
        """Sends a rich embedded link to the staff control panel dashboard."""
        dashboard_url = f"{self.dashboard_url}/"

        embed = create_orca_embed(
            title="ORCA AI -- Staff Dashboard",
            description=(
                "Access the staff administration panel to view pending server build requests and retrieve buyer deployment payloads.\n\n"
                "Click the 'Open Staff Dashboard' button below to access the panel."
            ),
            color=ORCA_PURPLE
        )
        embed.add_field(name="Dashboard Link", value=f"`{dashboard_url}`", inline=False)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Open Staff Dashboard",
            style=discord.ButtonStyle.link,
            url=dashboard_url
        ))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="build",
        description="Deploy a server blueprint onto this guild from an uploaded JSON file"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def build_command(self, interaction: discord.Interaction, file: discord.Attachment = None):
        """Executes full server build from JSON structure."""
        await interaction.response.defer(ephemeral=True)

        if not file:
            error_embed = create_orca_embed(
                title="Missing Blueprint File",
                description="Please attach a valid `.json` blueprint file generated by the ORCA Web Builder or downloaded from the Webhook channel.",
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
        description="Toggle emergency website portal lockdown on Vercel"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown_command(self, interaction: discord.Interaction, state: bool):
        """Toggles the Vercel builder website lockdown status via API call."""
        await interaction.response.defer(ephemeral=True)

        status_str = "LOCKED" if state else "UNLOCKED"
        color = ORCA_RED if state else ORCA_EMERALD

        try:
            req_url = f"{self.web_url}/api/lockdown?state={'true' if state else 'false'}"
            req = urllib.request.Request(req_url, headers={'User-Agent': 'ORCA-Bot'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            logger.warning(f"Could not trigger remote web lockdown API: {e}")

        embed = create_orca_embed(
            title=f"Vercel Website Lockdown: {status_str}",
            description=(
                f"The Vercel builder website portal has been **{status_str.lower()}**.\n\n"
                f"- **Target URL:** `{self.web_url}`\n"
                f"- **Lockdown State:** `{status_str}`"
            ),
            color=color
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="status",
        description="View ORCA AI system health, latency, and operational parameters"
    )
    async def status_command(self, interaction: discord.Interaction):
        """Displays system status in an embed for all users."""
        latency = round(self.bot.latency * 1000)
        
        embed = create_orca_embed(
            title="ORCA AI -- System Status",
            description="Current system metrics and connection status.",
            color=ORCA_CYAN
        )
        embed.add_field(name="API Latency", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="Guild Count", value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.add_field(name="Status", value="`ONLINE & OPERATIONAL`", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="help",
        description="View all available ORCA AI slash commands"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def help_command(self, interaction: discord.Interaction):
        """Displays formatted help menu in a Discord Embed for administrators."""
        embed = create_orca_embed(
            title="ORCA AI -- Command Directory",
            description="Explore available slash commands for server creation and maintenance.",
            color=ORCA_PURPLE
        )
        embed.add_field(
            name="`/custom-server`",
            value="Generates link to the interactive web builder.",
            inline=False
        )
        embed.add_field(
            name="`/dashboard`",
            value="Generates link to the staff administration panel.",
            inline=False
        )
        embed.add_field(
            name="`/build [file]`",
            value="Deploys an uploaded JSON blueprint into channels and roles.",
            inline=False
        )
        embed.add_field(
            name="`/lockdown [state]`",
            value="Toggle emergency access lockdown for the Vercel website builder.",
            inline=False
        )
        embed.add_field(
            name="`/status`",
            value="Check bot latency and server metrics.",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))

