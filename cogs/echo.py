import os
import time
import asyncio
from datetime import datetime
from typing import Optional, List, Literal

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


def get_staff_role_title(user_id: int, guild) -> Optional[str]:
    if is_owner(user_id) or (guild and user_id == guild.owner_id):
        return "Super Admin"
    db = get_db()
    if db is None:
        return None
    doc = db["staff_members"].find_one({"user_id": str(user_id)})
    return doc.get("role_title") if doc else None


def is_staff_or_owner(user_id: int, guild) -> bool:
    return get_staff_role_title(user_id, guild) is not None


def is_lockdown_active(guild_id: int, user_id: int) -> bool:
    if is_owner(user_id):
        return False
    db = get_db()
    if db is None:
        return False
    config = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return config.get("lockdown", False)


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
    def __init__(self, title: str, questions_chunk: List[dict], all_questions: List[dict], current_index: int, previous_answers: List[dict]):
        modal_title = f"{title} (Part {current_index // 5 + 1})" if len(all_questions) > 5 else title
        super().__init__(title=modal_title[:45])
        
        self.main_title = title
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
                questions_chunk=next_chunk,
                all_questions=self.all_questions,
                current_index=next_index,
                previous_answers=current_answers
            )
            await interaction.response.send_modal(next_modal)
            return

        await interaction.response.defer(ephemeral=True)
        db = get_db()

        # Increment inquiry counter
        counter = 1
        config = db["guild_config"].find_one({"guild_id": interaction.guild.id}) if db is not None else {}
        if config:
            counter = config.get("submission_counter", 0) + 1
            if db is not None:
                db["guild_config"].update_one({"guild_id": interaction.guild.id}, {"$set": {"submission_counter": counter}})

        category = config.get("category", "Custom Bot Commission") if config else "Custom Bot Commission"

        # Save submission directly to MongoDB
        if db is not None:
            db["form_submissions"].insert_one({
                "number": counter,
                "category": category,
                "username": str(interaction.user),
                "user_id": str(interaction.user.id),
                "answers": current_answers,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            })

        # Post submission log to target log channel if configured
        log_channel_id = config.get("log_channel_id")
        if log_channel_id and interaction.guild:
            log_ch = interaction.guild.get_channel(int(log_channel_id))
            if log_ch:
                embed = discord.Embed(
                    title=f"📥 New Inquiry #{counter} [{category}]",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow()
                )
                embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)
                for ans in current_answers:
                    embed.add_field(name=ans["label"][:256], value=ans["value"][:1024], inline=False)

                ping_text = ""
                ping_toggle = config.get("ping_toggle", True)
                ping_role_id = config.get("ping_role_id")
                if ping_toggle and ping_role_id:
                    role = interaction.guild.get_role(int(ping_role_id))
                    if role:
                        ping_text = role.mention

                try:
                    await log_ch.send(content=ping_text if ping_text else None, embed=embed)
                except Exception:
                    pass

        await interaction.followup.send(
            embed=info_embed("✅ Submission Received!", "Thank you for filling out the form. Our team has received your submission and will review it shortly!"),
            ephemeral=True
        )


# ----------------------------------------------------------------------
# Persistent Button View
# ----------------------------------------------------------------------
class FormPanelView(discord.ui.View):
    def __init__(self, button_label: str = "📝 Fill Out Form"):
        super().__init__(timeout=None)
        self.open_form_button.label = button_label[:80]

    @discord.ui.button(label="📝 Fill Out Form", style=discord.ButtonStyle.blurple, custom_id="echo_open_custom_modal")
    async def open_form_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_lockdown_active(interaction.guild.id, interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("System is in Total Lockdown mode. Only owner access permitted."), ephemeral=True)

        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted from submitting forms."), ephemeral=True)

        if is_maintenance(interaction.guild.id) and not is_staff_or_owner(interaction.user.id, interaction.guild):
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

        first_chunk = questions[:5]
        modal = ChainedCustomModal(
            title=title,
            questions_chunk=first_chunk,
            all_questions=questions,
            current_index=0,
            previous_answers=[]
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
        if is_lockdown_active(ctx.guild.id if ctx.guild else 0, ctx.author.id):
            await ctx.send(embed=error_embed("System is in Total Lockdown mode."))
            return False

        if is_blacklisted(ctx.author.id):
            await ctx.send(embed=error_embed("You are blacklisted from using 𝐎𝐑𝐂𝐀 commands."))
            return False
        return True

    # ====================================================================
    # Commands
    # ====================================================================
    @app_commands.command(name="system-stats", description="Shows bot ping and uptime.")
    async def system_stats(self, interaction: discord.Interaction):
        if is_lockdown_active(interaction.guild.id if interaction.guild else 0, interaction.user.id):
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

    @app_commands.command(name="add-question", description="Add a question to a form panel category (Admins Only).")
    async def add_question_cmd(
        self,
        interaction: discord.Interaction,
        category: str,
        label: str,
        placeholder: Optional[str] = "",
        style: Literal["short", "paragraph"] = "paragraph",
        required: bool = True
    ):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin", "Admin"]:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        guild_id = interaction.guild.id
        doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
        cat_map = doc.get("category_questions", {})

        if category not in cat_map:
            cat_map[category] = []

        cat_map[category].append({
            "label": label.strip(),
            "placeholder": placeholder.strip(),
            "style": style,
            "required": required
        })

        db["guild_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {"category_questions": cat_map, "questions": cat_map.get(doc.get("category", "Custom Bot Commission"), [])}},
            upsert=True
        )

        await interaction.response.send_message(embed=info_embed("✅ Question Added!", f"Added question **\"{label}\"** to form panel category **\"{category}\"**."), ephemeral=True)

    @app_commands.command(name="clear-questions", description="Clear questions for a specific form panel category (Admins Only).")
    async def clear_questions_cmd(self, interaction: discord.Interaction, category: str):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin", "Admin"]:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        guild_id = interaction.guild.id
        doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
        cat_map = doc.get("category_questions", {})

        if category in cat_map:
            cat_map[category] = []
            db["guild_config"].update_one(
                {"guild_id": guild_id},
                {"$set": {"category_questions": cat_map}},
                upsert=True
            )
            await interaction.response.send_message(embed=info_embed("🗑️ Questions Cleared!", f"Cleared all questions for category **\"{category}\"**."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Category **\"{category}\"** not found in questions log."), ephemeral=True)

    @app_commands.command(name="delete-preset", description="Delete a saved form preset by name (Admins Only).")
    async def delete_preset_cmd(self, interaction: discord.Interaction, name: str):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin", "Admin"]:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        res = db["form_presets"].delete_one({"name": name.strip()})
        if res.deleted_count > 0:
            await interaction.response.send_message(embed=info_embed("🗑️ Preset Deleted!", f"Deleted form preset **\"{name}\"**."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Form preset **\"{name}\"** was not found."), ephemeral=True)

    @app_commands.command(name="add-staff", description="Add a staff member with role permissions (Admins Only).")
    async def add_staff_cmd(self, interaction: discord.Interaction, user: discord.User, role: Literal["Super Admin", "Admin"]):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin"]:
            return await interaction.response.send_message(embed=error_embed("Super Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        db["staff_members"].update_one(
            {"user_id": str(user.id)},
            {"$set": {"user_id": str(user.id), "username": str(user), "role_title": role}},
            upsert=True
        )
        await interaction.response.send_message(embed=info_embed(f"👑 Added {user.mention} as staff with role **{role}**!"), ephemeral=True)

    @app_commands.command(name="remove-staff", description="Remove a staff member's access (Admins Only).")
    async def remove_staff_cmd(self, interaction: discord.Interaction, user: discord.User):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin"]:
            return await interaction.response.send_message(embed=error_embed("Super Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        result = db["staff_members"].delete_one({"user_id": str(user.id)})
        if result.deleted_count > 0:
            await interaction.response.send_message(embed=info_embed(f"✅ Revoked staff access for {user.mention} (`{user.id}`)."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"User {user.mention} was not found in the staff database."), ephemeral=True)

    @app_commands.command(name="clear-submission", description="Delete a submission by Inquiry Number (Staff Only).")
    async def clear_submission_cmd(self, interaction: discord.Interaction, number: int):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin", "Admin"]:
            return await interaction.response.send_message(embed=error_embed("Staff permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        res = db["form_submissions"].delete_one({"number": number})
        if res.deleted_count > 0:
            await interaction.response.send_message(embed=info_embed(f"🗑️ Deleted Inquiry #{number} permanently."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Inquiry #{number} not found."), ephemeral=True)

    @app_commands.command(name="clear-all-submissions", description="Permanently delete all form submissions (Super Admin Only).")
    async def clear_all_submissions_cmd(self, interaction: discord.Interaction):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin"]:
            return await interaction.response.send_message(embed=error_embed("Super Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        res = db["form_submissions"].delete_many({})
        await interaction.response.send_message(embed=info_embed(f"🗑️ Purged {res.deleted_count} submissions from database."), ephemeral=True)

    @app_commands.command(name="blacklist", description="Add a user to the bot blacklist (Admins Only).")
    async def blacklist_cmd(self, interaction: discord.Interaction, user: discord.User, reason: Optional[str] = "No reason provided"):
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin", "Admin"]:
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
        role_title = get_staff_role_title(interaction.user.id, interaction.guild)
        is_admin = interaction.user.guild_permissions.administrator
        if not is_admin and role_title not in ["Super Admin", "Admin"]:
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
        if not is_staff_or_owner(interaction.user.id, interaction.guild) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Only staff members or server admins can access the dashboard link."), ephemeral=True)

        dashboard_url = os.getenv("DASHBOARD_URL", "https://echo-dashboard.duckdns.org").strip()
        if not dashboard_url.startswith(("http://", "https://")):
            dashboard_url = f"https://{dashboard_url}"

        embed = discord.Embed(title="🌐 𝐎𝐑𝐂𝐀 Web Dashboard", description="Build custom forms, manage questions, and review form submissions.", color=EMBED_COLOR)
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

