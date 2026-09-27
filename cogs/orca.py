import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import time

WEB_BUILDER_URL = "https://echo-dashboard-qn39.onrender.com/"
AUTHORIZED_USER_ID = 1219266886143967245
is_lockdown = False
start_time = time.time()


def is_owner():
    """Custom check restricting command execution strictly to AUTHORIZED_USER_ID."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == AUTHORIZED_USER_ID:
            return True
        embed = discord.Embed(
            title="⛔ Access Denied",
            description="You do not have permission to execute this command.",
            color=0xE74C3C
        )
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return app_commands.check(predicate)


class OrcaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- 1. /help COMMAND (PUBLIC) ---
    @app_commands.command(name="help", description="Learn how to generate and deploy custom Discord servers.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ How to Create a Custom Discord Server",
            description=(
                "Building a fully customized Discord server with channels, categories, "
                "and roles is fast and automated with ORCA AI.\n\n"
                "**Follow these steps to create your server:**\n"
                "1️⃣ **Design Your Layout**\n"
                f"Visit our web builder to generate a blueprint using AI:\n{WEB_BUILDER_URL}\n\n"
                "2️⃣ **Customize & Export**\n"
                "Provide your server prompt, target Guild ID, and server invite link, then generate your JSON layout.\n\n"
                "3️⃣ **Submit Blueprint**\n"
                "Submit your layout directly on the website to send the JSON blueprint to our staff deployment queue."
            ),
            color=0x5865F2
        )
        embed.add_field(
            name="🌐 Web Builder Link",
            value=f"[Click here to open ORCA Web Builder]({WEB_BUILDER_URL})",
            inline=False
        )
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 2. /custom-server-builder COMMAND (PUBLIC) ---
    @app_commands.command(name="custom-server-builder", description="Provides link to the web-based layout tool.")
    async def custom_server_builder(self, interaction: discord.Interaction):
        if is_lockdown:
            embed = discord.Embed(
                title="⚠️ System Under Maintenance",
                description="The web portal is currently under maintenance. Please try again later.",
                color=0xF1C40F
            )
            embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠️ Interactive Server Builder",
            description=f"Click below to access our AI-powered web builder:\n{WEB_BUILDER_URL}",
            color=0x5865F2
        )
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 3. /build COMMAND (OWNER ONLY) ---
    @app_commands.command(name="build", description="Builds server categories, channels, and roles from JSON blueprint.")
    @is_owner()
    async def build(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.endswith('.json'):
            embed = discord.Embed(
                title="❌ Invalid File Format",
                description="The attached file must be a valid `.json` blueprint file.",
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            content = await file.read()
            blueprint = json.loads(content.decode('utf-8'))
        except Exception as e:
            embed = discord.Embed(
                title="❌ Blueprint Parsing Error",
                description=f"Failed to parse JSON blueprint file:\n```{e}```",
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        guild = interaction.guild
        current_channel = interaction.channel

        # 1. Purge Channels except current
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
            if role_name == "everyone":
                continue
            try:
                await guild.create_role(name=role_name, mentionable=True)
                roles_created += 1
            except Exception:
                pass

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
                    await guild.create_text_channel(name=ch_name, category=category, topic=topic, news=(ch_type == "announcement"))
                channels_created += 1

        # Delete temp execution channel
        try:
            await current_channel.delete()
        except Exception:
            pass

        target_channel = guild.text_channels[0] if guild.text_channels else None
        if target_channel:
            embed = discord.Embed(
                title="🚀 Server Build Complete",
                description=f"Successfully deployed blueprint onto **{guild.name}**.",
                color=0x2ECC71
            )
            embed.add_field(name="• Roles Created", value=str(roles_created), inline=False)
            embed.add_field(name="• Channels Created", value=str(channels_created), inline=False)
            embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
            await target_channel.send(embed=embed)

    # --- 4. /lockdown COMMAND (OWNER ONLY) ---
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
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 5. /status COMMAND (OWNER ONLY) ---
    @app_commands.command(name="status", description="Displays real-time bot latency and operational statistics.")
    @is_owner()
    async def status(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        uptime = round(time.time() - start_time)
        embed = discord.Embed(title="⚡ System Operational Status", color=0x5865F2)
        embed.add_field(name="Latency", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="Uptime", value=f"`{uptime} seconds`", inline=True)
        embed.add_field(name="Maintenance Lock", value="`ACTIVE`" if is_lockdown else "`INACTIVE`", inline=True)
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 6. /nuke COMMAND (OWNER ONLY) ---
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
            description=f"Purged **{deleted_count}** channels and categories. Preserved this channel.",
            color=0xE74C3C
        )
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.followup.send(embed=embed)


# REQUIRED SETUP ENTRY POINT FOR DISCORD.PY EXTENSIONS
async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))
