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
                "Click **Submit Design**. The layout blueprint `.json` file and a unique **5-digit verification code** will be logged for deployment.\n\n"
                "4️⃣ **Deploy via `/build`**\n"
                "Authorized staff will run `/build code: <code_here> file: <blueprint.json>` in the destination server to build everything automatically."
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

    # --- 3. /build COMMAND (OWNER ONLY) ---
    @app_commands.command(name="build", description="Builds server categories, channels, and roles from JSON blueprint.")
    @app_commands.describe(
        code="The 5-digit build verification code from the submission log",
        file="The attached blueprint .json file"
    )
    @is_owner()
    async def build(self, interaction: discord.Interaction, code: str, file: discord.Attachment):
        # File extension check
        if not file.filename.endswith('.json'):
            embed = discord.Embed(
                title="❌ Invalid File Format",
                description="The attached file must be a valid `.json` blueprint file.",
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # Parse JSON blueprint
        try:
            content = await file.read()
            blueprint = json.loads(content.decode('utf-8'))
        except Exception as e:
            embed = discord.Embed(
                title="❌ Blueprint Parsing Error",
                description=f"Failed to read JSON blueprint file:\n```{e}```",
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        # Verification Checks
        expected_code = str(blueprint.get("build_code", "")).strip()
        target_guild_id = str(blueprint.get("target_guild_id", "")).strip()
        current_guild_id = str(interaction.guild.id)
        input_code = str(code).strip()

        # Check 1: Match Code
        if input_code != expected_code:
            embed = discord.Embed(
                title="⛔ Build Denied — Code Mismatch",
                description=(
                    f"The provided build code (`{input_code}`) does not match "
                    f"the code recorded inside this blueprint (`{expected_code}`)."
                ),
                color=0xE74C3C
            )
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        # Check 2: Match Guild ID
        if target_guild_id and target_guild_id != current_guild_id:
            embed = discord.Embed(
                title="⛔ Build Denied — Server ID Mismatch",
                description=(
                    f"This blueprint was intended for Server ID `{target_guild_id}`, "
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

        # Delete command channel
        try:
            await current_channel.delete()
        except Exception:
            pass

        # Send completion embed into the first created text channel
        target_channel = guild.text_channels[0] if guild.text_channels else None
        if target_channel:
            embed = discord.Embed(
                title="🚀 Server Build Complete",
                description=f"Successfully deployed blueprint **`{blueprint.get('server_name', 'Discord Server')}`** onto **{guild.name}**.",
                color=0x2ECC71
            )
            embed.add_field(name="🔑 Verification Code", value=f"`{expected_code}`", inline=True)
            embed.add_field(name="• Roles Created", value=f"`{roles_created}`", inline=True)
            embed.add_field(name="• Channels Created", value=f"`{channels_created}`", inline=True)
            embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
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
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 5. /status COMMAND (OWNER ONLY) ---
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
            description=f"Purged **{deleted_count}** channels and categories. Preserved this execution channel.",
            color=0xE74C3C
        )
        embed.set_footer(text="ORCA AI — Automated Server Infrastructure")
        await interaction.followup.send(embed=embed)


# REQUIRED SETUP ENTRY POINT FOR DISCORD.PY EXTENSIONS
async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))
