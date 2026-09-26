import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import time

WEB_BUILDER_URL = "https://echo-dashboard-qn39.onrender.com/"
is_lockdown = False
start_time = time.time()


class OrcaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- 1. /help COMMAND ---
    @app_commands.command(name="help", description="Displays the ORCA AI command reference directory.")
    async def help_command(self, interaction: discord.Interaction):
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
    @app_commands.command(name="custom-server-builder", description="Provides link to the web-based layout tool.")
    async def custom_server_builder(self, interaction: discord.Interaction):
        if is_lockdown:
            await interaction.response.send_message("⚠️ The web portal is currently under maintenance.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠️ Interactive Server Builder",
            description=f"Click below to access our AI-powered web builder:\n{WEB_BUILDER_URL}",
            color=0x5865F2
        )
        await interaction.response.send_message(embed=embed)

    # --- 3. /build COMMAND ---
    @app_commands.command(name="build", description="Builds server categories, channels, and roles from JSON blueprint.")
    @app_commands.checks.has_permissions(administrator=True)
    async def build(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.endswith('.json'):
            await interaction.response.send_message("❌ Error: Attached file must be a JSON blueprint.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            content = await file.read()
            blueprint = json.loads(content.decode('utf-8'))
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to parse JSON blueprint: {e}")
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
                title="Server Build Complete",
                description=f"Successfully deployed blueprint onto **{guild.name}**.",
                color=0x2ECC71
            )
            embed.add_field(name="• Roles Created", value=str(roles_created), inline=False)
            embed.add_field(name="• Channels Created", value=str(channels_created), inline=False)
            embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
            await target_channel.send(embed=embed)

    # --- 4. /lockdown COMMAND ---
    @app_commands.command(name="lockdown", description="Toggles web portal maintenance screen.")
    @app_commands.checks.has_permissions(administrator=True)
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
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 5. /status COMMAND ---
    @app_commands.command(name="status", description="Displays real-time bot latency and operational statistics.")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        uptime = round(time.time() - start_time)
        embed = discord.Embed(title="⚡ System Operational Status", color=0x5865F2)
        embed.add_field(name="Latency", value=f"{latency} ms", inline=True)
        embed.add_field(name="Uptime", value=f"{uptime} seconds", inline=True)
        embed.add_field(name="Maintenance Lock", value="ACTIVE" if is_lockdown else "INACTIVE", inline=True)
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 6. /nuke COMMAND ---
    @app_commands.command(name="nuke", description="Deletes all channels/categories except the command channel.")
    @app_commands.checks.has_permissions(administrator=True)
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
