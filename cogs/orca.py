import os
import time
import asyncio
import re
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


def sanitize_custom_id(category: str) -> str:
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', category.lower())
    return f"orca_form_{clean}"[:100]


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="❌ Error", description=message, color=ERROR_COLOR)


def info_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR)


def is_owner(user_id: int) -> bool:
    return user_id == HARDCODED_OWNER_ID


def is_blacklisted(user_id: int) -> bool:
    db = get_db()
    if db is None:
        return False
    return db["blacklist"].find_one({"user_id": str(user_id)}) is not None


def is_lockdown_active(guild_id: int, user_id: int) -> bool:
    if is_owner(user_id):
        return False
    db = get_db()
    if db is None:
        return False
    config = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return config.get("lockdown", False)


def is_maintenance(guild_id: int, user_id: int, guild: Optional[discord.Guild]) -> bool:
    if is_owner(user_id) or (guild and user_id == guild.owner_id):
        return False
    db = get_db()
    if db is None:
        return False
    config = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return config.get("maintenance", False)


# ----------------------------------------------------------------------
# Dynamic Multi-Part Chained Modals
# ----------------------------------------------------------------------
class ChainedCustomModal(discord.ui.Modal):
    def __init__(self, title: str, category: str, questions_chunk: List[dict], all_questions: List[dict], current_index: int, previous_answers: List[dict]):
        modal_title = f"{title} (Part {current_index // 5 + 1})" if len(all_questions) > 5 else title
        super().__init__(title=modal_title[:45])
        
        self.main_title = title
        self.category = category
        self.all_questions = all_questions
        self.current_index = current_index
        self.previous_answers = previous_answers
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
                category=self.category,
                questions_chunk=next_chunk,
                all_questions=self.all_questions,
                current_index=next_index,
                previous_answers=current_answers
            )
            await interaction.response.send_modal(next_modal)
            return

        await interaction.response.defer(ephemeral=True)
        db = get_db()

        counter = 1
        config = db["guild_config"].find_one({"guild_id": interaction.guild.id}) if db is not None else {}
        if config:
            counter = config.get("submission_counter", 0) + 1
            if db is not None:
                db["guild_config"].update_one({"guild_id": interaction.guild.id}, {"$set": {"submission_counter": counter}})

        cat_configs = config.get("category_configs", {}) if config else {}
        cat_data = cat_configs.get(self.category, {})

        if db is not None:
            db["form_submissions"].insert_one({
                "number": counter,
                "category": self.category,
                "username": str(interaction.user),
                "user_id": str(interaction.user.id),
                "answers": current_answers,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            })

        log_channel_id = cat_data.get("logChannel") or cat_data.get("log_channel_id") or config.get("log_channel_id")
        if log_channel_id and interaction.guild:
            log_ch = interaction.guild.get_channel(int(log_channel_id))
            if log_ch:
                embed = discord.Embed(
                    title=f"📥 New Inquiry #{counter} [{self.category}]",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow()
                )
                embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)
                for ans in current_answers:
                    embed.add_field(name=ans["label"][:256], value=ans["value"][:1024], inline=False)

                ping_text = ""
                ping_role_id = cat_data.get("staffRole") or cat_data.get("ping_role_id") or config.get("ping_role_id")
                if ping_role_id:
                    role = interaction.guild.get_role(int(ping_role_id))
                    if role:
                        ping_text = role.mention

                try:
                    await log_ch.send(content=ping_text if ping_text else None, embed=embed)
                except Exception:
                    pass

        await interaction.followup.send(
            embed=info_embed("✅ Submission Received!", f"Thank you for filling out the **{self.category}** form. Our team has received your submission and will review it shortly!"),
            ephemeral=True
        )


# ----------------------------------------------------------------------
# Dynamic Category Button View
# ----------------------------------------------------------------------
class FormPanelView(discord.ui.View):
    def __init__(self, category: str = "Custom Bot Commission", button_label: str = "📝 Fill Out Form"):
        super().__init__(timeout=None)
        self.category = category
        custom_id = sanitize_custom_id(category)

        btn = discord.ui.Button(
            label=button_label[:80],
            style=discord.ButtonStyle.blurple,
            custom_id=custom_id
        )
        btn.callback = self.open_form_callback
        self.add_item(btn)

    async def open_form_callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        if is_lockdown_active(guild_id, interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("System is in Total Lockdown mode. Only owner access permitted."), ephemeral=True)

        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted from submitting forms."), ephemeral=True)

        if is_maintenance(guild_id, interaction.user.id, interaction.guild):
            return await interaction.response.send_message(embed=error_embed("The system is currently undergoing maintenance. Please try again later!"), ephemeral=True)

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}
        
        cat_configs = config.get("category_configs", {}) if config else {}
        cat_data = cat_configs.get(self.category, {})

        questions = cat_data.get("questions", [])
        if not questions:
            questions = config.get("category_questions", {}).get(self.category, [])

        if not questions:
            questions = [
                {"label": "Please explain your request details", "placeholder": "Type your request here...", "style": "paragraph", "required": True}
            ]

        title = cat_data.get("panelTitle") or cat_data.get("title") or f"📋 {self.category.upper()}"

        first_chunk = questions[:5]
        modal = ChainedCustomModal(
            title=title,
            category=self.category,
            questions_chunk=first_chunk,
            all_questions=questions,
            current_index=0,
            previous_answers=[]
        )
        await interaction.response.send_modal(modal)


# ----------------------------------------------------------------------
# Main Cog
# ----------------------------------------------------------------------
class Orca(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    async def cog_load(self):
        db = get_db()
        registered_cats = set(["Custom Bot Commission", "Partnership Application"])
        if db is not None:
            try:
                for doc in db["guild_config"].find():
                    cat_configs = doc.get("category_configs", {})
                    for cat in cat_configs.keys():
                        registered_cats.add(cat)
            except Exception:
                pass
        
        for cat in registered_cats:
            self.bot.add_view(FormPanelView(category=cat))

    async def cog_check(self, ctx: commands.Context) -> bool:
        guild_id = ctx.guild.id if ctx.guild else 0
        if is_lockdown_active(guild_id, ctx.author.id):
            await ctx.send(embed=error_embed("System is in Total Lockdown mode."))
            return False

        if is_blacklisted(ctx.author.id):
            await ctx.send(embed=error_embed("You are blacklisted from using 𝐎𝐑𝐂𝐀 commands."))
            return False
        return True

    # ====================================================================
    # Slash Commands
    # ====================================================================
    @app_commands.command(name="system-stats", description="Shows bot ping and uptime.")
    async def system_stats(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        if is_lockdown_active(guild_id, interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("System is in Total Lockdown."), ephemeral=True)

        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        latency_ms = round(self.bot.latency * 1000)
        uptime_seconds = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        embed = discord.Embed(title="⚙️ 𝐎𝐑𝐂𝐀 System Stats", color=EMBED_COLOR)
        embed.add_field(name="📶 Ping", value=f"`{latency_ms}ms`", inline=True)
        embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str}`", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="rename-category", description="Rename an existing form panel category (Admins Only).")
    async def rename_category_cmd(self, interaction: discord.Interaction, old_name: str, new_name: str):
        if not is_owner(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        guild_id = interaction.guild.id
        doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
        cat_configs = doc.get("category_configs", {})

        if old_name.strip() in cat_configs:
            cat_configs[new_name.strip()] = cat_configs.pop(old_name.strip())
            db["guild_config"].update_one(
                {"guild_id": guild_id},
                {"$set": {"category_configs": cat_configs}},
                upsert=True
            )
            await interaction.response.send_message(embed=info_embed("✏️ Category Renamed!", f"Renamed category **\"{old_name.strip()}\"** to **\"{new_name.strip()}\"**."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Category **\"{old_name.strip()}\"** not found."), ephemeral=True)

    @app_commands.command(name="delete-category", description="Delete an existing form panel category and its questions (Admins Only).")
    async def delete_category_cmd(self, interaction: discord.Interaction, name: str):
        if not is_owner(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        guild_id = interaction.guild.id
        doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
        cat_configs = doc.get("category_configs", {})

        if name.strip() in cat_configs:
            cat_configs.pop(name.strip())
            db["guild_config"].update_one(
                {"guild_id": guild_id},
                {"$set": {"category_configs": cat_configs}},
                upsert=True
            )
            await interaction.response.send_message(embed=info_embed("🗑️ Category Deleted!", f"Deleted category **\"{name.strip()}\"** and its configuration."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Category **\"{name.strip()}\"** not found."), ephemeral=True)

    @app_commands.command(name="blacklisted-list", description="Display all blacklisted users (Clean Embed).")
    async def blacklisted_list_cmd(self, interaction: discord.Interaction):
        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        cursor = db["blacklist"].find()
        bl_lines = []
        for doc in cursor:
            uid = doc.get("user_id")
            reason = doc.get("reason", "No reason provided")
            bl_lines.append(f"<@{uid}> (ID: `{uid}`) — *{reason}*")

        embed = discord.Embed(
            title="⛔ Blacklisted Users Log",
            description="\n".join(bl_lines) if bl_lines else "*(No blacklisted users)*",
            color=ERROR_COLOR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="blacklist", description="Add a user to the bot blacklist (Admins Only).")
    async def blacklist_cmd(self, interaction: discord.Interaction, user: discord.User, reason: Optional[str] = "No reason provided"):
        if not is_owner(interaction.user.id) and not interaction.user.guild_permissions.administrator:
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
        if not is_owner(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        result = db["blacklist"].delete_one({"user_id": str(user.id)})
        if result.deleted_count > 0:
            await interaction.response.send_message(embed=info_embed(f"✅ Successfully unblacklisted {user.mention} (`{user.id}`)."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"User {user.mention} was not found on the blacklist."), ephemeral=True)

    @app_commands.command(name="dashboard", description="Get the link to access the 𝐎𝐑𝐂𝐀 Web Control Dashboard.")
    async def dashboard(self, interaction: discord.Interaction):
        if not is_owner(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("Only the Bot Owner (1219266886143967245) can access the Web Control Dashboard."), ephemeral=True)

        dashboard_url = os.getenv("DASHBOARD_URL", "https://echo-dashboard.duckdns.org").strip()
        if not dashboard_url.startswith(("http://", "https://")):
            dashboard_url = f"https://{dashboard_url}"

        embed = discord.Embed(title="🌐 𝐎𝐑𝐂𝐀 Web Dashboard", description="Build custom forms, manage questions, and review system telemetry.", color=EMBED_COLOR)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Web Control Panel", url=dashboard_url, style=discord.ButtonStyle.link, emoji="🎛️"))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ====================================================================
    # Web Handlers: Category Deployment
    # ====================================================================
    async def deploy_form_panel_from_web(self, channel_id: int, category: str = "Custom Bot Commission"):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                raise Exception(f"Channel ID {channel_id} not found.")

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": channel.guild.id}) if db is not None else {}
        cat_configs = config.get("category_configs", {}) if config else {}
        cat_data = cat_configs.get(category, {})

        title = cat_data.get("panelTitle") or cat_data.get("title") or f"📋 {category.upper()}"
        desc = cat_data.get("panelDesc") or cat_data.get("description") or f"Click the button below to submit a {category} request."
        button_label = cat_data.get("buttonLabel") or cat_data.get("button_label") or "📝 Fill Out Form"

        embed = discord.Embed(title=title, description=desc, color=EMBED_COLOR)
        view = FormPanelView(category=category, button_label=button_label)
        msg = await channel.send(embed=embed, view=view)

        if db is not None:
            cat_data["last_form_msg_id"] = msg.id
            cat_data["channel_id"] = channel.id
            cat_configs[category] = cat_data

            db["guild_config"].update_one(
                {"guild_id": channel.guild.id},
                {"$set": {"category_configs": cat_configs}}
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Orca(bot))

