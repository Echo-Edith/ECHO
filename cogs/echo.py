import os
import time
import asyncio
from datetime import datetime
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

try:
    import pymongo
except ImportError:
    pymongo = None

EMBED_COLOR = discord.Color.blurple()
ERROR_COLOR = discord.Color.red()

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


def is_blacklisted(user_id: int) -> bool:
    db = get_db()
    if db is None:
        return False
    return db["blacklist"].find_one({"user_id": str(user_id)}) is not None


def is_maintenance(guild_id: int) -> bool:
    db = get_db()
    if db is None:
        return False
    config = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return config.get("maintenance", False)


# ----------------------------------------------------------------------
# Dynamic Multi-Part Chained Modals
# ----------------------------------------------------------------------
class ChainedCustomModal(discord.ui.Modal):
    def __init__(self, title: str, questions_chunk: List[dict], all_questions: List[dict], current_index: int, previous_answers: List[dict], log_channel_id: Optional[int]):
        modal_title = f"{title} (Part {current_index // 5 + 1})" if len(all_questions) > 5 else title
        super().__init__(title=modal_title[:45])
        
        self.main_title = title
        self.all_questions = all_questions
        self.current_index = current_index
        self.previous_answers = previous_answers
        self.log_channel_id = log_channel_id
        self.inputs = []

        for q in questions_chunk:
            is_paragraph = q.get("style") == "paragraph"
            input_style = discord.TextStyle.paragraph if is_paragraph else discord.TextStyle.short
            field_input = discord.ui.TextInput(
                label=q.get("label", "Question")[:45],
                style=input_style,
                placeholder=q.get("placeholder", "")[:100],
                required=q.get("required", True),
                max_length=1000 if is_paragraph else 100
            )
            self.inputs.append((q.get("label"), field_input))
            self.add_item(field_input)

    async def on_submit(self, interaction: discord.Interaction):
        current_answers = list(self.previous_answers)
        for label, inp in self.inputs:
            val = inp.value.strip() or "*(No Answer)*"
            current_answers.append({"label": label, "value": val})

        next_index = self.current_index + len(self.inputs)

        if next_index < len(self.all_questions):
            next_chunk = self.all_questions[next_index:next_index + 5]
            next_modal = ChainedCustomModal(
                title=self.main_title,
                questions_chunk=next_chunk,
                all_questions=self.all_questions,
                current_index=next_index,
                previous_answers=current_answers,
                log_channel_id=self.log_channel_id
            )
            await interaction.response.send_modal(next_modal)
            return

        await interaction.response.defer(ephemeral=True)
        db = get_db()

        embed = discord.Embed(
            title=f"📥 New Form Submission: {self.main_title}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)

        for ans in current_answers:
            embed.add_field(name=ans["label"][:256], value=ans["value"][:1024], inline=False)

        if db is not None:
            db["form_submissions"].insert_one({
                "username": str(interaction.user),
                "user_id": str(interaction.user.id),
                "answers": current_answers,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            })

        target_channel = interaction.channel
        if self.log_channel_id and interaction.guild:
            ch = interaction.guild.get_channel(self.log_channel_id)
            if ch:
                target_channel = ch

        await target_channel.send(embed=embed)
        await interaction.followup.send(embed=info_embed("✅ Submission Received!", "Thank you for filling out the form. Our team has received your submission and will review it shortly!"), ephemeral=True)


# ----------------------------------------------------------------------
# Persistent Button View
# ----------------------------------------------------------------------
class FormPanelView(discord.ui.View):
    def __init__(self, button_label: str = "📝 Fill Out Form"):
        super().__init__(timeout=None)
        self.open_form_button.label = button_label[:80]

    @discord.ui.button(label="📝 Fill Out Form", style=discord.ButtonStyle.blurple, custom_id="echo_open_custom_modal")
    async def open_form_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted from submitting forms."), ephemeral=True)

        if is_maintenance(interaction.guild.id):
            return await interaction.response.send_message(embed=error_embed("The system is currently undergoing maintenance. Please try again later!"), ephemeral=True)

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": interaction.guild.id}) if db is not None else {}
        questions = config.get("questions", [])

        if not questions:
            questions = [
                {"label": "What type of bot do you need?", "placeholder": "e.g. Moderation, Music, Economy", "style": "short", "required": True},
                {"label": "List required features and details", "placeholder": "Describe what the bot should do...", "style": "paragraph", "required": True},
                {"label": "What is your budget?", "placeholder": "e.g. $20 / 1000 Robux", "style": "short", "required": False}
            ]

        title = config.get("title") or "Custom Form"
        log_channel_id = config.get("log_channel_id")

        first_chunk = questions[:5]
        modal = ChainedCustomModal(
            title=title,
            questions_chunk=first_chunk,
            all_questions=questions,
            current_index=0,
            previous_answers=[],
            log_channel_id=log_channel_id
        )
        await interaction.response.send_modal(modal)


# ----------------------------------------------------------------------
# Main cog
# ----------------------------------------------------------------------
class Echo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    async def cog_load(self):
        self.bot.add_view(FormPanelView())

    async def cog_check(self, ctx: commands.Context) -> bool:
        if is_blacklisted(ctx.author.id):
            await ctx.send(embed=error_embed("You are blacklisted from using ECHO commands."))
            return False
        return True

    # ====================================================================
    # Commands
    # ====================================================================
    @app_commands.command(name="system-stats", description="Shows bot ping and uptime.")
    async def system_stats(self, interaction: discord.Interaction):
        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        latency_ms = round(self.bot.latency * 1000)
        uptime_seconds = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        embed = discord.Embed(title="⚙️ ECHO System Stats", color=EMBED_COLOR)
        embed.add_field(name="📶 Ping", value=f"`{latency_ms}ms`", inline=True)
        embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str}`", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="blacklist", description="Add a user to the bot blacklist (Admins Only).")
    async def blacklist_cmd(self, interaction: discord.Interaction, user: discord.User, reason: Optional[str] = "No reason provided"):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        db["blacklist"].update_one(
            {"user_id": str(user.id)},
            {"$set": {"user_id": str(user.id), "username": str(user), "reason": reason}},
            upsert=True
        )
        await interaction.response.send_message(embed=info_embed(f"⛔ Blacklisted {user.mention}. Reason: {reason}"), ephemeral=True)

    @app_commands.command(name="unblacklist", description="Remove a user from the bot blacklist (Admins Only).")
    async def unblacklist_cmd(self, interaction: discord.Interaction, user: discord.User):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        result = db["blacklist"].delete_one({"user_id": str(user.id)})
        if result.deleted_count > 0:
            await interaction.response.send_message(embed=info_embed(f"✅ Successfully unblacklisted {user.mention} (`{user.id}`)."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"User {user.mention} was not found on the blacklist."), ephemeral=True)

    @app_commands.command(name="dashboard", description="Get the link to access the ECHO Web Control Dashboard.")
    async def dashboard(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Only server admins can access the dashboard link."), ephemeral=True)

        dashboard_url = os.getenv("DASHBOARD_URL", "https://echo-dashboard.duckdns.org").strip()
        if not dashboard_url.startswith(("http://", "https://")):
            dashboard_url = f"https://{dashboard_url}"

        embed = discord.Embed(title="🌐 ECHO Web Dashboard", description="Build custom forms, manage questions, and review form submissions.", color=EMBED_COLOR)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Web Control Panel", url=dashboard_url, style=discord.ButtonStyle.link, emoji="🎛️"))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ====================================================================
    # Web Handlers: Unified Embed + Button Deployment
    # ====================================================================
    async def deploy_form_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            raise Exception(f"Channel ID {channel_id} not found.")

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": channel.guild.id}) if db is not None else {}

        title = config.get("title") or "🤖 REQUEST CUSTOM BOT COMMISSION"
        desc = config.get("description") or "Want a custom bot built specifically for your Discord server? Click the button below to complete our quick commission form!"
        button_label = config.get("button_label") or "📝 Fill Out Form"

        embed = discord.Embed(title=title, description=desc, color=EMBED_COLOR)
        view = FormPanelView(button_label=button_label)
        msg = await channel.send(embed=embed, view=view)

        if db is not None:
            db["guild_config"].update_one(
                {"guild_id": channel.guild.id},
                {"$set": {"last_form_msg_id": msg.id, "channel_id": channel.id}}
            )

    async def update_form_panel_from_web(self):
        db = get_db()
        guild_id = self.bot.guilds[0].id if self.bot.guilds else 0
        config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}

        msg_id = config.get("last_form_msg_id")
        channel_id = config.get("channel_id")
        if not msg_id or not channel_id:
            raise Exception("No active form panel message stored.")

        channel = self.bot.get_channel(channel_id)
        if not channel:
            raise Exception("Form channel not found.")

        msg = await channel.fetch_message(msg_id)
        title = config.get("title") or "🤖 REQUEST CUSTOM BOT COMMISSION"
        desc = config.get("description") or "Want a custom bot built specifically for your Discord server? Click the button below to complete our quick commission form!"
        button_label = config.get("button_label") or "📝 Fill Out Form"

        embed = discord.Embed(title=title, description=desc, color=EMBED_COLOR)
        view = FormPanelView(button_label=button_label)
        await msg.edit(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Echo(bot))

