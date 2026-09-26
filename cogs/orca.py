import discord
from discord import app_commands
from discord.ext import commands
import json
import keep_alive

class OrcaBuilder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==========================================
    # 1. HELP COMMAND
    # ==========================================
    @app_commands.command(name="help", description="Displays full ORCA AI command reference directory.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="ORCA AI -- Command Reference Directory",
            description="Overview of available slash commands for building and managing server structures.",
            color=0x8A2BE2  # Purple theme matching reference
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
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================
    # 2. PUBLIC /CUSTOM-SERVER-BUILDER COMMAND
    # ==========================================
    @app_commands.command(name="custom-server-builder", description="Provides link to interactive layout builder.")
    async def custom_builder(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ Interactive Server Layout Builder",
            description="Click below to open the builder web app. Design your categories, channels, and permissions interactively.",
            color=0x5865F2
        )
        embed.add_field(
            name="Web App Link", 
            value="[Open Builder Web Application](https://echo-dashboard-qn39.onrender.com/)", 
            inline=False
        )
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # ==========================================
    # 3. BUILD COMMAND
    # ==========================================
    @app_commands.command(name="build", description="Builds server layout from JSON blueprint.")
    @app_commands.describe(file="JSON blueprint content string")
    @app_commands.checks.has_permissions(administrator=True)
    async def build(self, interaction: discord.Interaction, file: str):
        await interaction.response.defer(ephemeral=True)

        try:
            data = json.loads(file)
        except json.JSONDecodeError:
            await interaction.followup.send("❌ Invalid JSON blueprint syntax.", ephemeral=True)
            return

        target_guild_id = str(data.get("target_guild_id", "")).strip()
        current_guild_id = str(interaction.guild_id)

        if target_guild_id != current_guild_id:
            await interaction.followup.send(
                f"⛔ **Guild Lockout Violation!** Blueprint target (`{target_guild_id}`) does not match current server ID (`{current_guild_id}`).",
                ephemeral=True
            )
            return

        guild = interaction.guild

        # Purge existing channels
        for channel in list(guild.channels):
            try:
                await channel.delete(reason="Wiping structure for ORCA AI rebuild.")
            except Exception:
                pass

        # Create roles
        roles_created = 0
        for role_name in data.get("roles", []):
            if not discord.utils.get(guild.roles, name=role_name):
                try:
                    await guild.create_role(name=role_name)
                    roles_created += 1
                except Exception:
                    pass

        # Create categories and channels
        channels_created = 0
        first_channel = None

        for cat in data.get("categories", []):
            category = await guild.create_category(cat.get("name", "CATEGORY"))
            for ch in cat.get("channels", []):
                ch_name = ch.get("name", "channel")
                ch_type = ch.get("type", "text")
                ch_topic = ch.get("topic", "")

                if ch_type == "voice":
                    await guild.create_voice_channel(name=ch_name, category=category)
                elif ch_type == "announcement":
                    try:
                        c = await guild.create_text_channel(name=ch_name, category=category, topic=ch_topic, news=True)
                    except Exception:
                        c = await guild.create_text_channel(name=ch_name, category=category, topic=ch_topic)
                    if not first_channel:
                        first_channel = c
                else:
                    c = await guild.create_text_channel(name=ch_name, category=category, topic=ch_topic)
                    if not first_channel:
                        first_channel = c

                channels_created += 1

        # Completion embed matching exact UI specifications
        embed = discord.Embed(
            title="Server Build Complete",
            description=f"Successfully deployed blueprint onto **{data.get('server_name', guild.name)}**.",
            color=0x2ECC71  # Emerald green accent
        )
        embed.add_field(
            name="", 
            value=f"• **Roles Created:** {roles_created}\n• **Channels Created:** {channels_created}", 
            inline=False
        )
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")

        if first_channel:
            await first_channel.send(embed=embed)

        await interaction.followup.send("✅ Server successfully built!", ephemeral=True)

    # ==========================================
    # 4. LOCKDOWN & STATUS COMMANDS
    # ==========================================
    @app_commands.command(name="lockdown", description="Toggles web portal maintenance screen.")
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown(self, interaction: discord.Interaction, state: bool):
        keep_alive.MAINTENANCE_MODE = state
        status = "ENABLED (Web App Locked)" if state else "DISABLED (Web App Active)"
        await interaction.response.send_message(f"🔒 Maintenance Mode is now **{status}**.", ephemeral=True)

    @app_commands.command(name="status", description="Displays real-time bot latency and stats.")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(title="ORCA AI -- Operational Status", color=0x5865F2)
        embed.add_field(name="WebSocket Latency", value=f"`{latency}ms`", inline=True)
        embed.add_field(name="Guilds Served", value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.set_footer(text="ORCA AI -- Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==========================================
    # 5. OWNER NUKE COMMAND
    # ==========================================
    @app_commands.command(name="nuke", description="[OWNER ONLY] Wipes all channels and leaves 1 control channel.")
    async def nuke(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ Only the server owner can execute this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        for c in list(interaction.guild.channels):
            try:
                await c.delete()
            except Exception:
                pass

        control = await interaction.guild.create_text_channel(name="bot-commands")
        await control.send(f"💥 Server wiped by {interaction.user.mention}. Ready for `/build`.")
        await interaction.followup.send("✅ Server wiped.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaBuilder(bot))
