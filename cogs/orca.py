import os
import json
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# Import shared WEBSITE_BANS dictionary from keep_alive
from keep_alive import WEBSITE_BANS

# Hardcoded Bot Owner ID for /build file command
OWNER_ID = 1219266886143967245
WEBSITE_URL = "https://echo-dashboard-qn39.onrender.com/"

# In-memory storage for Discord bans
# Structure: { user_id_str: {"reason": str, "mention": str} }
DISCORD_BANS = {}


class OrcaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------------------------------------------------------------------------
    # 1. /ping
    # -------------------------------------------------------------------------
    @app_commands.command(name="ping", description="Check bot latency.")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! Latency: **{latency_ms}ms**", ephemeral=True)

    # -------------------------------------------------------------------------
    # 2. /website
    # -------------------------------------------------------------------------
    @app_commands.command(name="website", description="Get the link to the Orca server architect website.")
    async def website(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🌊 ORCA Dashboard",
            description=f"Build and customize your server using our website:\n[{WEBSITE_URL}]({WEBSITE_URL})",
            color=discord.Color.from_rgb(0, 242, 254)
        )
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------------------------------
    # 3. /build file: (Owner Only)
    # -------------------------------------------------------------------------
    @app_commands.command(name="build", description="Build server categories and channels from a design JSON file.")
    @app_commands.describe(file="Uploaded JSON design file or direct URL")
    async def build(self, interaction: discord.Interaction, file: discord.Attachment):
        # Strict Owner Check
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ This command is strictly reserved for the bot owner.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # Download attachment contents
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file.url) as resp:
                    if resp.status != 200:
                        await interaction.followup.send("❌ Failed to download design file.")
                        return
                    layout_data = await resp.json()
        except Exception as e:
            await interaction.followup.send(f"❌ Error parsing JSON file: `{str(e)}`")
            return

        # Target Server ID Verification
        build_meta = layout_data.get("build_meta", {})
        target_server_id = str(build_meta.get("target_server_id", "")).strip()
        current_server_id = str(interaction.guild_id)

        if target_server_id and target_server_id != current_server_id:
            await interaction.followup.send(
                f"⚠️ **Target Mismatch!** This layout file was generated for Server ID `{target_server_id}`, "
                f"but command was issued in `{current_server_id}`. Build aborted."
            )
            return

        # Construct Server Structure
        guild = interaction.guild
        categories = layout_data.get("categories", [])

        try:
            for cat_data in categories:
                # Create Category
                category = await guild.create_category(name=cat_data["name"])

                for chan_data in cat_data.get("channels", []):
                    chan_name = chan_data.get("formatted_name") or chan_data.get("name", "unnamed")
                    chan_type = chan_data.get("type", "text")
                    topic = chan_data.get("topic") or chan_data.get("description", "")
                    is_read_only = chan_data.get("read_only", False)

                    # Configure Read-Only Permissions (for announcements/info)
                    overwrites = None
                    if is_read_only:
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(send_messages=False),
                            guild.me: discord.PermissionOverwrite(send_messages=True)
                        }

                    if chan_type == "voice":
                        await guild.create_voice_channel(name=chan_name, category=category, overwrites=overwrites)
                    else:
                        await guild.create_text_channel(name=chan_name, category=category, topic=topic, overwrites=overwrites)

            await interaction.followup.send(f"✅ **Server built successfully!** Created {len(categories)} categories.")

        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred during construction: `{str(e)}`")

    # -------------------------------------------------------------------------
    # 4. /ban
    # -------------------------------------------------------------------------
    @app_commands.command(name="ban", description="Ban a user from the website or discord bot.")
    @app_commands.choices(location=[
        app_commands.Choice(name="Website", value="website"),
        app_commands.Choice(name="Discord", value="discord")
    ])
    async def ban(
        self,
        interaction: discord.Interaction,
        location: app_commands.Choice[str],
        user: discord.User,
        reason: str = "No reason provided.",
        message_from_dev: str = "You have been suspended by the developers."
    ):
        user_id_str = str(user.id)

        if location.value == "website":
            WEBSITE_BANS[user_id_str] = {
                "reason": reason,
                "message_from_dev": message_from_dev,
                "mention": user.mention
            }
            await interaction.response.send_message(f"🚫 **{user.name}** (`{user.id}`) has been banned from the website.")

        elif location.value == "discord":
            DISCORD_BANS[user_id_str] = {
                "reason": reason,
                "mention": user.mention
            }

            # DM User if location is discord
            try:
                dm_embed = discord.Embed(
                    title="⛔ You have been banned",
                    description=f"**Reason:** {reason}\n**Developer Message:** {message_from_dev}",
                    color=discord.Color.red()
                )
                await user.send(embed=dm_embed)
            except discord.HTTPException:
                pass  # Ignore if DMs are closed

            await interaction.response.send_message(f"🚫 **{user.name}** (`{user.id}`) has been recorded as Discord banned.")

    # -------------------------------------------------------------------------
    # 5. /unban
    # -------------------------------------------------------------------------
    @app_commands.command(name="unban", description="Unban a user from the website or discord bot.")
    @app_commands.choices(location=[
        app_commands.Choice(name="Website", value="website"),
        app_commands.Choice(name="Discord", value="discord")
    ])
    async def unban(
        self,
        interaction: discord.Interaction,
        location: app_commands.Choice[str],
        user: discord.User,
        reason: str = "Unbanned by admin."
    ):
        user_id_str = str(user.id)

        if location.value == "website":
            if user_id_str in WEBSITE_BANS:
                del WEBSITE_BANS[user_id_str]
                await interaction.response.send_message(f"✅ **{user.name}** (`{user.id}`) has been unbanned from the website.")
            else:
                await interaction.response.send_message("⚠️ User is not in the website ban list.", ephemeral=True)

        elif location.value == "discord":
            if user_id_str in DISCORD_BANS:
                del DISCORD_BANS[user_id_str]
                await interaction.response.send_message(f"✅ **{user.name}** (`{user.id}`) has been unbanned from Discord list.")
            else:
                await interaction.response.send_message("⚠️ User is not in the Discord ban list.", ephemeral=True)

    # -------------------------------------------------------------------------
    # 6. /ban-list
    # -------------------------------------------------------------------------
    @app_commands.command(name="ban-list", description="Display current bans by location.")
    @app_commands.choices(location=[
        app_commands.Choice(name="Website", value="website"),
        app_commands.Choice(name="Discord", value="discord")
    ])
    async def ban_list(self, interaction: discord.Interaction, location: app_commands.Choice[str]):
        bans_source = WEBSITE_BANS if location.value == "website" else DISCORD_BANS
        
        if not bans_source:
            await interaction.response.send_message(f"ℹ️ No active bans found for location: **{location.name}**.")
            return

        embed = discord.Embed(
            title=f"📋 Active Bans — {location.name}",
            color=discord.Color.red()
        )

        for uid, info in bans_source.items():
            mention = info.get("mention", f"<@{uid}>")
            reason = info.get("reason", "No reason provided")
            embed.add_field(
                name=f"User ID: {uid}",
                value=f"**User:** {mention}\n**Reason:** {reason}",
                inline=False
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))
