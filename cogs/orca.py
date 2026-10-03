import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import json
import re

class OrcaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def slugify_channel_name(self, name: str, emoji: str = "") -> str:
        """Converts raw name input into a Discord-compliant slug (kebab-case)."""
        # Strip unicode emojis and non-alphanumeric chars except dashes/underscores
        clean_name = re.sub(r'[\u1f600-\u1f64f\u1f300-\u1f5ff\u1f680-\u1f6ff\u2600-\u26ff\u2700-\u27bf]', '', name or '')
        clean_name = re.sub(r'[^a-zA-Z0-9\s\-_]', '', clean_name).strip().lower()
        clean_name = re.sub(r'[\s_]+', '-', clean_name)
        
        prefix = f"{emoji}-" if emoji else ""
        formatted = f"{prefix}{clean_name}".strip("-")
        return formatted or "unnamed-channel"

    async def wipe_guild_infrastructure(self, guild: discord.Guild, leave_fallback_channel: bool = False):
        """Helper method to completely purge channels, categories, and roles from a server."""
        # 1. Delete all channels and categories
        for channel in guild.channels:
            try:
                await channel.delete(reason="Server Nuke/Rebuild Execution")
            except Exception as e:
                print(f"Failed to delete channel {channel.name}: {e}")

        # 2. Delete all customizable roles (skipping @everyone and managed bot roles)
        for role in guild.roles:
            if not role.is_default() and not role.is_bot_managed() and not role.is_premium_subscriber():
                try:
                    await role.delete(reason="Server Nuke/Rebuild Execution")
                except Exception as e:
                    print(f"Failed to delete role {role.name}: {e}")

        # 3. Create fallback channel if requested
        if leave_fallback_channel:
            try:
                await guild.create_text_channel(
                    name="nuked",
                    topic="Server wiped clean by Owner execution."
                )
            except Exception as e:
                print(f"Failed to create fallback channel: {e}")

    # --- BUILD COMMAND ---
    @app_commands.command(
        name="build",
        description="Nukes the server completely and rebuilds layout from a JSON blueprint file or URL."
    )
    @app_commands.describe(
        url="URL to blueprint JSON file",
        file="Uploaded JSON blueprint file",
        server_id="Target Server/Guild ID (Optional, defaults to blueprint metadata)"
    )
    async def build_server(
        self,
        interaction: discord.Interaction,
        url: str = None,
        file: discord.Attachment = None,
        server_id: str = None
    ):
        await interaction.response.defer(ephemeral=False)

        blueprint_data = None

        # Fetch JSON payload from file or URL
        try:
            async with aiohttp.ClientSession() as session:
                if file:
                    async with session.get(file.url) as resp:
                        if resp.status == 200:
                            blueprint_data = await resp.json()
                elif url:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            blueprint_data = await resp.json()
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to fetch blueprint data: `{e}`")
            return

        if not blueprint_data:
            await interaction.followup.send("❌ Please provide a valid `url:` link or upload a `file:` attachment.")
            return

        # Determine target Guild ID
        target_guild_id = None
        if server_id:
            target_guild_id = int(server_id.strip())
        elif "build_meta" in blueprint_data and blueprint_data["build_meta"].get("target_server_id"):
            target_guild_id = int(str(blueprint_data["build_meta"]["target_server_id"]).strip())
        else:
            target_guild_id = interaction.guild_id

        guild = self.bot.get_guild(target_guild_id)
        if not guild:
            try:
                guild = await self.bot.fetch_guild(target_guild_id)
            except Exception:
                guild = None

        if not guild:
            await interaction.followup.send(
                f"❌ Bot is not in target server (`{target_guild_id}`). "
                "Please invite the bot to the server first using the **Add Bot to Server** link!"
            )
            return

        # STEP 1: NUKE EXISTING INFRASTRUCTURE
        await interaction.followup.send(f"💥 Nuking **{guild.name}** before building standard infrastructure...")
        await self.wipe_guild_infrastructure(guild, leave_fallback_channel=False)

        # STEP 2: CREATE ROLES
        created_roles = {}
        for r_cfg in blueprint_data.get("roles", []):
            color_hex = str(r_cfg.get("color", "#8b5cf6")).replace("#", "")
            color_val = int(color_hex, 16) if color_hex else 0x8b5cf6
            try:
                role = await guild.create_role(
                    name=r_cfg["name"],
                    color=discord.Color(color_val),
                    reason="Echo Studio Infrastructure Automated Build"
                )
                created_roles[r_cfg["name"]] = role
            except Exception as e:
                print(f"Error creating role {r_cfg.get('name')}: {e}")

        # STEP 3: CREATE CATEGORIES AND CHANNELS
        for cat_cfg in blueprint_data.get("categories", []):
            try:
                cat_name = f"{cat_cfg.get('emoji', '')} {cat_cfg.get('name', 'CATEGORY')}".strip()
                category = await guild.create_category(name=cat_name)
                
                for chan_cfg in cat_cfg.get("channels", []):
                    c_raw_name = chan_cfg.get("name", "channel")
                    c_emoji = chan_cfg.get("emoji", "")
                    c_name = self.slugify_channel_name(c_raw_name, c_emoji)
                    c_type = chan_cfg.get("type", "text")
                    c_topic = chan_cfg.get("topic", "")
                    
                    # Construct Permission Overwrites
                    perms_dict = chan_cfg.get("permissions", {})
                    read_only = chan_cfg.get("read_only", False)

                    overwrites = {}
                    if read_only or c_type == "announcement":
                        overwrites[guild.default_role] = discord.PermissionOverwrite(
                            view_channel=True,
                            send_messages=False
                        )
                    elif perms_dict:
                        overwrites[guild.default_role] = discord.PermissionOverwrite(
                            view_channel=perms_dict.get("view", True),
                            send_messages=perms_dict.get("send", True),
                            embed_links=perms_dict.get("embed", True),
                            attach_files=perms_dict.get("attach", True),
                            read_message_history=perms_dict.get("history", True),
                            connect=perms_dict.get("connect", True),
                            speak=perms_dict.get("speak", True)
                        )

                    if c_type == "voice":
                        await guild.create_voice_channel(
                            name=c_name,
                            category=category,
                            overwrites=overwrites
                        )
                    else:
                        await guild.create_text_channel(
                            name=c_name,
                            category=category,
                            topic=c_topic,
                            overwrites=overwrites
                        )
            except Exception as e:
                print(f"Error creating category/channel: {e}")

        embed = discord.Embed(
            title="🏗 Server Rebuilt & Cleaned!",
            description=f"Successfully nuked previous layout and deployed new blueprint to **{guild.name}** (`{guild.id}`).",
            color=0x2ecc71
        )
        
        # Fallback to DM or status message if interaction channel was deleted during nuke
        try:
            await interaction.followup.send(embed=embed)
        except Exception:
            if interaction.user:
                await interaction.user.send(embed=embed)

    # --- OWNER-ONLY NUKE COMMAND ---
    @app_commands.command(
        name="nuke",
        description="[OWNER ONLY] Deletes channels and roles. Leaves 1 channel unless optional server_id is specified."
    )
    @app_commands.describe(
        server_id="Optional target Server/Guild ID. If provided, wipes EVERYTHING without leaving a fallback channel."
    )
    async def nuke_server(
        self,
        interaction: discord.Interaction,
        server_id: str = None
    ):
        # Verify Bot Owner
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("❌ Only the Bot Owner can execute `/nuke`.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)

        target_guild_id = int(server_id.strip()) if server_id else interaction.guild_id
        guild = self.bot.get_guild(target_guild_id)

        if not guild:
            try:
                guild = await self.bot.fetch_guild(target_guild_id)
            except Exception:
                guild = None

        if not guild:
            await interaction.followup.send(f"❌ Guild ID `{target_guild_id}` not found or bot is not present.")
            return

        leave_channel = False if server_id else True

        await interaction.followup.send(f"⚠️ Initiating complete server nuke on **{guild.name}** (`{guild.id}`)...")
        await self.wipe_guild_infrastructure(guild, leave_fallback_channel=leave_channel)

        if server_id:
            try:
                await interaction.user.send(f"💥 Complete server nuke executed on **{guild.name}** (`{guild.id}`). All channels & roles deleted.")
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(OrcaCog(bot))
