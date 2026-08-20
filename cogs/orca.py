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


def can_user_close_ticket(member: discord.Member, category: str, guild: discord.Guild) -> bool:
    """Verify if user has permission to close: Must have Staff Ping Role or Admin/Owner permissions."""
    if is_owner(member.id) or member.id == guild.owner_id or member.guild_permissions.administrator:
        return True

    db = get_db()
    if db is None:
        return member.guild_permissions.manage_channels

    config = db["guild_config"].find_one({"guild_id": guild.id}) or {}
    cat_configs = config.get("category_configs", {})
    cat_data = cat_configs.get(category, {})

    staff_role_id = cat_data.get("staffRole") or config.get("ping_role_id")
    if staff_role_id:
        staff_role = guild.get_role(int(staff_role_id))
        if staff_role and staff_role in member.roles:
            return True

    return member.guild_permissions.manage_channels


# ----------------------------------------------------------------------
# Close Ticket Modal Interface
# ----------------------------------------------------------------------
class CloseTicketModal(discord.ui.Modal):
    def __init__(self, category: str, ticket_data: dict):
        super().__init__(title="Close Ticket & Finalize Log")
        self.category = category
        self.ticket_data = ticket_data

        self.reason_input = discord.ui.TextInput(
            label="Reason for Closing",
            style=discord.TextStyle.short,
            placeholder="e.g. Commission completed & delivered",
            required=False,
            max_length=200
        )
        self.add_item(self.reason_input)

        self.buyer_role_input = discord.ui.TextInput(
            label="Grant Buyer Role? (yes / no)",
            style=discord.TextStyle.short,
            placeholder="Type 'yes' or 'no'",
            required=True,
            default="yes",
            max_length=5
        )
        self.add_item(self.buyer_role_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not can_user_close_ticket(interaction.user, self.category, interaction.guild):
            return await interaction.response.send_message(
                embed=error_embed("Only staff members with the designated Staff Role or Administrators can close this ticket."),
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        reason = self.reason_input.value.strip() or "No reason provided"
        give_role = self.buyer_role_input.value.strip().lower() in ["yes", "y", "true", "1"]

        cog = interaction.client.get_cog("Orca")
        if cog:
            await cog.execute_ticket_close(
                interaction=interaction,
                channel=interaction.channel,
                category=self.category,
                ticket_data=self.ticket_data,
                reason=reason,
                give_role=give_role
            )


# ----------------------------------------------------------------------
# Ticket Control Panel Inside Active Channel
# ----------------------------------------------------------------------
class TicketChannelControlView(discord.ui.View):
    def __init__(self, category: str, ticket_data: dict):
        super().__init__(timeout=None)
        self.category = category
        self.ticket_data = ticket_data

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="orca_ticket_close_btn")
    async def close_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_user_close_ticket(interaction.user, self.category, interaction.guild):
            return await interaction.response.send_message(
                embed=error_embed("Only staff members with the designated Staff Role or Administrators can close this ticket."),
                ephemeral=True
            )

        await interaction.response.send_modal(CloseTicketModal(self.category, self.ticket_data))


# ----------------------------------------------------------------------
# Persistent Verification View
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
# Dynamic Multi-Part Chained Modals for Intake
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
                label=q.get("label", "Question Prompt")[:45],
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

        ticket_record = {
            "number": counter,
            "category": self.category,
            "username": str(interaction.user),
            "user_id": str(interaction.user.id),
            "answers": current_answers,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "closed": False
        }

        if db is not None:
            db["form_submissions"].insert_one(ticket_record)

        target_category_ref = cat_data.get("openedCategory")
        target_discord_category = None
        if target_category_ref and interaction.guild:
            if str(target_category_ref).isdigit():
                target_discord_category = interaction.guild.get_channel(int(target_category_ref))
            if not target_discord_category:
                target_discord_category = discord.utils.get(interaction.guild.categories, name=target_category_ref)

        ticket_channel = None
        if interaction.guild:
            clean_category = re.sub(r'[^a-zA-Z0-9]', '', self.category.lower()) or "support"
            clean_username = re.sub(r'[^a-zA-Z0-9]', '', interaction.user.name.lower()) or "user"
            channel_name = f"ticket-{clean_category}-{clean_username}"[:100]

            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
            }

            staff_role_id = cat_data.get("staffRole") or config.get("ping_role_id")
            if staff_role_id:
                staff_role = interaction.guild.get_role(int(staff_role_id))
                if staff_role:
                    overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            try:
                ticket_channel = await interaction.guild.create_text_channel(
                    name=channel_name,
                    category=target_discord_category if isinstance(target_discord_category, discord.CategoryChannel) else None,
                    overwrites=overwrites,
                    topic=f"ORCA Ticket #{counter} | Opener ID: {interaction.user.id} | Category: {self.category}",
                    reason=f"ORCA Ticket #{counter} opened by {interaction.user}"
                )
            except Exception:
                pass

        if ticket_channel:
            welcome_title = cat_data.get("welcomeTitle") or f"🐬 {self.category} Ticket Created"
            welcome_desc = cat_data.get("welcomeDesc") or "Welcome to your private ticket channel! A staff member will assist you shortly."
            
            w_embed = discord.Embed(title=welcome_title, description=welcome_desc, color=SUCCESS_COLOR)
            w_embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)
            
            for ans in current_answers:
                w_embed.add_field(name=ans["label"][:256], value=ans["value"][:1024], inline=False)

            ping_text = interaction.user.mention
            if staff_role_id:
                role = interaction.guild.get_role(int(staff_role_id))
                if role:
                    ping_text += f" {role.mention}"

            try:
                await ticket_channel.send(content=ping_text, embed=w_embed, view=TicketChannelControlView(self.category, ticket_record))
            except Exception:
                pass

        response_text = f"Thank you! Your ticket channel has been created: {ticket_channel.mention}" if ticket_channel else "Thank you for submitting your ticket details! Our team has received your submission."
        await interaction.followup.send(
            embed=info_embed("✅ Ticket Opened!", response_text),
            ephemeral=True
        )


# ----------------------------------------------------------------------
# Dynamic Category Button View
# ----------------------------------------------------------------------
class FormPanelView(discord.ui.View):
    def __init__(self, category: str = "Custom Bot Commission", button_label: str = "📝 Open Ticket"):
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
            return await interaction.response.send_message(embed=error_embed("You are blacklisted from submitting tickets."), ephemeral=True)

        if is_maintenance(guild_id, interaction.user.id, interaction.guild):
            return await interaction.response.send_message(embed=error_embed("The ticket system is currently undergoing maintenance. Please try again later!"), ephemeral=True)

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}
        
        cat_configs = config.get("category_configs", {}) if config else {}
        cat_data = cat_configs.get(self.category, {})

        questions = cat_data.get("questions", [])
        if not questions:
            questions = config.get("category_questions", {}).get(self.category, [])

        if not questions:
            questions = [
                {"label": "Please explain your ticket details", "placeholder": "Type your details here...", "style": "paragraph", "required": True}
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
# Main Cog Engine
# ----------------------------------------------------------------------
class Orca(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    async def cog_load(self):
        self.bot.add_view(VerificationView())
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
    # Execute Ticket Close, Post-Close Buyer Role Grant, DM Receipt & Transcript Log
    # ====================================================================
    async def execute_ticket_close(self, interaction: discord.Interaction, channel: discord.TextChannel, category: str, ticket_data: dict, reason: str, give_role: bool):
        guild = interaction.guild
        if not guild or not isinstance(channel, discord.TextChannel):
            return

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        cat_configs = config.get("category_configs", {}) if config else {}
        cat_data = cat_configs.get(category, {})

        opener_id = ticket_data.get("user_id")
        if not opener_id and channel.topic:
            match = re.search(r"Opener ID:\s*(\d+)", channel.topic)
            if match:
                opener_id = match.group(1)

        opener_member = guild.get_member(int(opener_id)) if opener_id else None
        if not opener_member and opener_id:
            try:
                opener_member = await guild.fetch_member(int(opener_id))
            except Exception:
                pass

        role_granted_status = "No"
        if give_role and opener_member:
            buyer_role_id = cat_data.get("buyerRole")
            if buyer_role_id:
                buyer_role = guild.get_role(int(buyer_role_id))
                if buyer_role:
                    try:
                        await opener_member.add_roles(buyer_role, reason=f"ORCA Ticket #{ticket_data.get('number', '')} closed by {interaction.user}")
                        role_granted_status = f"Yes ({buyer_role.mention})"
                    except Exception as e:
                        role_granted_status = f"Failed to assign role: {e}"

        messages = []
        try:
            async for msg in channel.history(limit=500, oldest_first=True):
                time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                messages.append(f"[{time_str}] {msg.author}: {msg.clean_content}")
        except Exception:
            pass
        transcript_text = "\n".join(messages)

        log_channel_id = cat_data.get("logChannel") or cat_data.get("log_channel_id") or config.get("log_channel_id")
        if log_channel_id:
            log_ch = guild.get_channel(int(log_channel_id))
            if log_ch:
                l_embed = discord.Embed(
                    title=f"🔒 Ticket #{ticket_data.get('number', 'N/A')} Closed [{category}]",
                    color=ERROR_COLOR,
                    timestamp=discord.utils.utcnow()
                )
                l_embed.add_field(name="Ticket Opener", value=f"<@{opener_id}> (`{opener_id}`)" if opener_id else "Unknown", inline=True)
                l_embed.add_field(name="Closed By Staff", value=f"{interaction.user.mention}", inline=True)
                l_embed.add_field(name="Buyer Role Granted", value=role_granted_status, inline=True)
                l_embed.add_field(name="Close Reason", value=f"*{reason}*", inline=False)

                answers = ticket_data.get("answers", [])
                for ans in answers:
                    l_embed.add_field(name=ans["label"][:256], value=ans["value"][:1024], inline=False)

                file = discord.File(io.BytesIO(transcript_text.encode('utf-8')), filename=f"transcript-{channel.name}.txt") if transcript_text else None
                try:
                    await log_ch.send(embed=l_embed, file=file)
                except Exception:
                    pass

        if opener_member:
            dm_embed = discord.Embed(
                title=f"🧾 ORCA Studio Ticket Receipt #{ticket_data.get('number', '')}",
                description=f"Your ticket in **{guild.name}** under **{category}** has been finalized and closed.",
                color=EMBED_COLOR,
                timestamp=discord.utils.utcnow()
            )
            dm_embed.add_field(name="Status", value="Closed & Archived", inline=True)
            dm_embed.add_field(name="Reason", value=reason, inline=True)
            dm_embed.add_field(name="Buyer Role", value="Granted" if "Yes" in role_granted_status else "Not Applicable", inline=True)
            dm_embed.set_footer(text="Thank you for choosing ORCA Automation Studio!")

            try:
                await opener_member.send(embed=dm_embed)
            except Exception:
                pass

        if db is not None:
            db["form_submissions"].update_one(
                {"number": ticket_data.get("number")},
                {"$set": {"closed": True, "closed_by": str(interaction.user), "close_reason": reason}}
            )

        try:
            await interaction.followup.send(embed=info_embed("🔒 Closing Ticket", "This channel will be archived and deleted in 5 seconds..."), ephemeral=True)
            await asyncio.sleep(5)
            await channel.delete(reason=f"ORCA Ticket Closed by {interaction.user}")
        except Exception:
            pass

    # ====================================================================
    # Commands
    # ====================================================================
    @app_commands.command(name="verify", description="Verification command to claim configured server access roles.")
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

    @app_commands.command(name="close", description="Close current ticket channel (Staff only). Option to grant buyer role & supply reason.")
    @app_commands.describe(reason="Reason for closing this ticket", give_buyer_role="Grant configured Buyer Role to ticket opener?")
    @app_commands.choices(give_buyer_role=[
        app_commands.Choice(name="Yes (Grant Buyer Role)", value="yes"),
        app_commands.Choice(name="No (Do Not Grant Role)", value="no")
    ])
    async def close_slash_cmd(self, interaction: discord.Interaction, reason: Optional[str] = "No reason provided", give_buyer_role: app_commands.Choice[str] = None):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel) or not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message(embed=error_embed("This command can only be executed inside an active ticket channel."), ephemeral=True)

        category = "Custom Bot Commission"
        ticket_data = {"number": "N/A"}

        db = get_db()
        if db is not None and interaction.channel.topic:
            match = re.search(r"ORCA Ticket #(\d+)", interaction.channel.topic)
            if match:
                num = int(match.group(1))
                found = db["form_submissions"].find_one({"number": num})
                if found:
                    ticket_data = found
                    category = found.get("category", "Custom Bot Commission")

        if not can_user_close_ticket(interaction.user, category, interaction.guild):
            return await interaction.response.send_message(
                embed=error_embed("Only staff members with the designated Staff Role or Administrators can close this ticket."),
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        give_role = give_buyer_role.value == "yes" if give_buyer_role else False

        await self.execute_ticket_close(
            interaction=interaction,
            channel=interaction.channel,
            category=category,
            ticket_data=ticket_data,
            reason=reason,
            give_role=give_role
        )

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

    @app_commands.command(name="rename-category", description="Rename an existing ticket category (Admins Only).")
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
            await interaction.response.send_message(embed=info_embed("✏️ Ticket Category Renamed!", f"Renamed ticket category **\"{old_name.strip()}\"** to **\"{new_name.strip()}\"**."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Ticket Category **\"{old_name.strip()}\"** not found."), ephemeral=True)

    @app_commands.command(name="delete-category", description="Delete an existing ticket category and its questions (Admins Only).")
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
            await interaction.response.send_message(embed=info_embed("🗑️ Ticket Category Deleted!", f"Deleted ticket category **\"{name.strip()}\"** and its configuration."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Ticket Category **\"{name.strip()}\"** not found."), ephemeral=True)

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

        embed = discord.Embed(title="🌐 𝐎𝐑𝐂𝐀 Web Dashboard", description="Build custom ticket categories, manage questions, and review system telemetry.", color=EMBED_COLOR)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Web Control Panel", url=dashboard_url, style=discord.ButtonStyle.link, emoji="🎛️"))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ====================================================================
    # Web Handlers: Category & Verification Deployment
    # ====================================================================
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

        title = cat_data.get("panelTitle") or cat_data.get("title") or f"📋 {category.upper()} TICKET PANEL"
        desc = cat_data.get("panelDesc") or cat_data.get("description") or f"Click the button below to submit a {category} ticket request."
        button_label = cat_data.get("buttonLabel") or cat_data.get("button_label") or "📝 Open Ticket"

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

