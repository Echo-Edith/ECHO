import os
import time
import asyncio
import re
import io
from datetime import datetime
from typing import Optional, List, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands

try:
    import pymongo
except ImportError:
    pymongo = None

EMBED_COLOR = discord.Color.blurple()
ERROR_COLOR = discord.Color.red()
SUCCESS_COLOR = discord.Color.green()
HARDCODED_OWNER_ID = 1219266886143967245

_mongo_client = None


def get_db():
    global _mongo_client
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri or not pymongo:
        return None
    if _mongo_client is None:
        try:
            _mongo_client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        except Exception:
            return None
    try:
        return _mongo_client["echo_bot"]
    except Exception:
        return None


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="❌ Error", description=message, color=ERROR_COLOR)


def info_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR)


# ----------------------------------------------------------------------
# Persistent Verification View & Execution
# ----------------------------------------------------------------------
class VerificationView(discord.ui.View):
    def __init__(self, button_label: str = "✅ Verify Access"):
        super().__init__(timeout=None)
        btn = discord.ui.Button(
            label=button_label,
            style=discord.ButtonStyle.success,
            custom_id="orca_verification_btn"
        )
        btn.callback = self.verify_callback
        self.add_item(btn)

    async def verify_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        v_config = config.get("verification_config", {})

        role_ids = v_config.get("roles", [])
        if not role_ids:
            return await interaction.followup.send(embed=error_embed("No verification roles are currently configured."), ephemeral=True)

        added_roles = []
        failed_roles = []

        for rid in role_ids:
            if rid:
                role = guild.get_role(int(rid))
                if role:
                    try:
                        await interaction.user.add_roles(role, reason="ORCA Studio Member Verification")
                        added_roles.append(role.mention)
                    except Exception:
                        failed_roles.append(role.name)

        if added_roles:
            role_str = ", ".join(added_roles)
            embed = discord.Embed(
                title="✅ Member Verification Complete!",
                description=f"Welcome to **{guild.name}**! You have been verified and granted the following roles:\n\n{role_str}",
                color=SUCCESS_COLOR
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            log_ch_id = v_config.get("log_channel_id")
            if log_ch_id:
                log_ch = guild.get_channel(int(log_ch_id))
                if log_ch:
                    l_embed = discord.Embed(
                        title="🛡️ Member Verified",
                        description=f"{interaction.user.mention} (`{interaction.user.id}`) completed verification.",
                        color=SUCCESS_COLOR,
                        timestamp=discord.utils.utcnow()
                    )
                    l_embed.add_field(name="Granted Roles", value=role_str, inline=False)
                    try:
                        await log_ch.send(embed=l_embed)
                    except Exception:
                        pass
        else:
            await interaction.followup.send(embed=error_embed("Could not assign verification roles. Please inform an Administrator."), ephemeral=True)


# ----------------------------------------------------------------------
# Main Cog Engine with Verification Engine Support
# ----------------------------------------------------------------------
class Orca(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    async def cog_load(self):
        self.bot.add_view(VerificationView())

    @app_commands.command(name="verify", description="Bloxlink-style verification command to claim server access roles.")
    async def verify_slash_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send(embed=error_embed("Command must be run inside a server."), ephemeral=True)

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        v_config = config.get("verification_config", {})

        role_ids = v_config.get("roles", [])
        if not role_ids:
            return await interaction.followup.send(embed=error_embed("No verification roles are currently configured in dashboard."), ephemeral=True)

        added_roles = []
        for rid in role_ids:
            if rid:
                role = guild.get_role(int(rid))
                if role:
                    try:
                        await interaction.user.add_roles(role, reason="ORCA /verify command executed")
                        added_roles.append(role.mention)
                    except Exception:
                        pass

        if added_roles:
            role_str = ", ".join(added_roles)
            embed = discord.Embed(
                title="✅ Verification Success!",
                description=f"Your account has been verified in **{guild.name}**! Granted roles:\n\n{role_str}",
                color=SUCCESS_COLOR
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Failed to assign verification roles."), ephemeral=True)

    async def deploy_verification_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": channel.guild.id}) if db is not None else {}
        v_config = config.get("verification_config", {})

        title = v_config.get("title") or "🛡️ MEMBER VERIFICATION PORTAL"
        desc = v_config.get("description") or "Click the button below to complete verification!"
        button_label = v_config.get("button_label") or "✅ Verify Access"

        embed = discord.Embed(title=title, description=desc, color=EMBED_COLOR)
        view = VerificationView(button_label=button_label)
        await channel.send(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Orca(bot))

