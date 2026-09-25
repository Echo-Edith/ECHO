import os
import time
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

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
    emb = discord.Embed(title="❌ Error", description=message, color=ERROR_COLOR)
    emb.set_footer(text="(Server rules apply)")
    return emb


def info_embed(title: str, description: str = None) -> discord.Embed:
    emb = discord.Embed(title=title, description=description, color=EMBED_COLOR)
    emb.set_footer(text="(Server rules apply)")
    return emb


def is_owner(user_id: int) -> bool:
    return user_id == HARDCODED_OWNER_ID


def is_lockdown_active(guild_id: int, user_id: int) -> bool:
    if is_owner(user_id):
        return False
    db = get_db()
    if db is None:
        return False
    try:
        config = db["guild_config"].find_one({"guild_id": guild_id}) or {}
        return config.get("lockdown", False)
    except Exception:
        return False


class Orca(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()
        self.status_index = 0
        self.status_rotator_loop.start()

    def cog_unload(self):
        self.status_rotator_loop.cancel()

    # ----------------------------------------------------------------------
    # 🌊 1. Dynamic Presence Rotator
    # ----------------------------------------------------------------------
    @tasks.loop(minutes=2)
    async def status_rotator_loop(self):
        if not self.bot.guilds:
            return

        db = get_db()
        if db is None:
            return

        try:
            guild_id = self.bot.guilds[0].id
            config = db["guild_config"].find_one({"guild_id": guild_id}) or {}
            rc = config.get("rotator_config", {})

            if not rc.get("enabled"):
                return

            statuses = rc.get("statuses", [])
            if not statuses:
                return

            self.status_index = (self.status_index + 1) % len(statuses)
            item = statuses[self.status_index]

            st_type = item.get("type", "playing").lower()
            st_text = item.get("text", "ORCA Studio")

            act_type = discord.ActivityType.playing
            if st_type == "watching":
                act_type = discord.ActivityType.watching
            elif st_type == "listening":
                act_type = discord.ActivityType.listening
            elif st_type == "competing":
                act_type = discord.ActivityType.competing

            activity = discord.Activity(type=act_type, name=st_text)
            await self.bot.change_presence(activity=activity)
        except Exception:
            pass

    @status_rotator_loop.before_loop
    async def before_rotator_loop(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------------------
    # 👋 2. Welcomer System
    # ----------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        db = get_db()
        if db is None:
            return

        try:
            config = db["guild_config"].find_one({"guild_id": guild.id}) or {}
            wc = config.get("welcomer_config", {})

            if wc.get("enabled"):
                ch_id = wc.get("channel_id")
                if ch_id and str(ch_id).isdigit():
                    ch = guild.get_channel(int(ch_id))
                    if ch:
                        text = wc.get("message", "Welcome {user} to {server}!").replace("{user}", member.mention).replace("{server}", guild.name)
                        embed = discord.Embed(title="👋 Welcome to ORCA Studio!", description=text, color=SUCCESS_COLOR)
                        embed.set_thumbnail(url=member.display_avatar.url)
                        embed.set_footer(text="(Server rules apply)")
                        await ch.send(embed=embed)

            if wc.get("dm_enabled"):
                dm_text = wc.get("dm_message", "Welcome to {server}!").replace("{user}", member.name).replace("{server}", guild.name)
                dm_embed = discord.Embed(title=f"Welcome to {guild.name}!", description=dm_text, color=EMBED_COLOR)
                dm_embed.set_footer(text="(Server rules apply)")
                await member.send(embed=dm_embed)
        except Exception:
            pass

    # ----------------------------------------------------------------------
    # 🔒 3. System Lockdown Control Commands
    # ----------------------------------------------------------------------
    @app_commands.command(name="lockdown", description="Toggle Total Lockdown mode across Bot & Vercel Site.")
    @app_commands.describe(enabled="True to lock down systems, False to unlock")
    async def lockdown_cmd(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        if not is_owner(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            return await interaction.followup.send(embed=error_embed("Administrator privileges required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.followup.send(embed=error_embed("Database connection error."), ephemeral=True)

        try:
            db["guild_config"].update_one(
                {"guild_id": interaction.guild.id},
                {"$set": {"lockdown": enabled}},
                upsert=True
            )
            
            status_str = "ENABLED (Vercel & Bot Locked)" if enabled else "DISABLED (Systems Normal)"
            embed = info_embed("🔒 Lockdown Status Updated", f"Total System Lockdown is now **{status_str}**.")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Failed to update lockdown: {e}"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Orca(bot))
