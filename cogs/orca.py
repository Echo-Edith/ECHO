import os
import time
import asyncio
import re
import io
from datetime import datetime, timedelta
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


def generate_html_transcript(channel_name: str, messages: List[dict]) -> str:
    rows = []
    for msg in messages:
        author = msg.get("author", "User")
        content = msg.get("content", "")
        time_str = msg.get("timestamp", "")
        avatar = msg.get("avatar", "https://cdn.discordapp.com/embed/avatars/0.png")

        rows.append(f"""
        <div class="msg flex items-start gap-3 p-2 hover:bg-white/5 rounded-lg">
          <img src="{avatar}" class="w-10 h-10 rounded-full">
          <div>
            <div class="flex items-center gap-2">
              <span class="font-bold text-blue-400 text-sm">{author}</span>
              <span class="text-[10px] text-gray-400">{time_str}</span>
            </div>
            <p class="text-xs text-gray-200 mt-1">{content}</p>
          </div>
        </div>
        """)

    return f"""
    <!DOCTYPE html>
    <html class="dark">
    <head>
      <title>ORCA Studio Web Transcript - {channel_name}</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#090a0f] text-white p-6 font-sans">
      <div class="max-w-4xl mx-auto space-y-4">
        <header class="border-b border-white/10 pb-4">
          <h1 class="text-xl font-bold text-blue-400">🐬 ORCA Studio Transcript: #{channel_name}</h1>
          <p class="text-xs text-gray-400">Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
        </header>
        <div class="space-y-2">{"".join(rows)}</div>
      </div>
    </body>
    </html>
    """


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="❌ Error", description=message, color=ERROR_COLOR)


def info_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR)


def parse_hex_color(color_str: str) -> discord.Color:
    clean = color_str.strip().lstrip('#')
    try:
        return discord.Color(int(clean, 16))
    except Exception:
        return discord.Color.blurple()


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
    if is_owner(member.id) or member.id == guild.owner_id or member.guild_permissions.administrator:
        return True

    db = get_db()
    if db is None:
        return member.guild_permissions.manage_channels

    config = db["guild_config"].find_one({"guild_id": guild.id}) or {}
    cat_configs = config.get("category_configs", {})
    cat_data = cat_configs.get(category, {})

    staff_role_id = cat_data.get("staffRole") or config.get("ping_role_id")
    if staff_role_id and str(staff_role_id).isdigit():
        staff_role = guild.get_role(int(staff_role_id))
        if staff_role and staff_role in member.roles:
            return True

    return member.guild_permissions.manage_channels


# ----------------------------------------------------------------------
# 💳 Terms of Service Agreement Component
# ----------------------------------------------------------------------
class TOSAgreementView(discord.ui.View):
    def __init__(self, opener_id: int):
        super().__init__(timeout=None)
        self.opener_id = opener_id

    @discord.ui.button(label="📜 Accept Terms of Service", style=discord.ButtonStyle.success, custom_id="orca_tos_accept_btn")
    async def accept_tos(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opener_id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Only the ticket opener can accept the Terms of Service."), ephemeral=True)

        try:
            await interaction.channel.set_permissions(interaction.user, read_messages=True, send_messages=True, attach_files=True)
            button.disabled = True
            button.label = "✅ Terms Accepted"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ Terms of Service Accepted",
                    description=f"{interaction.user.mention} accepted the Terms of Service. Chat permissions granted!",
                    color=SUCCESS_COLOR
                )
            )
        except Exception as e:
            await interaction.response.send_message(embed=error_embed(f"Failed to update permissions: {e}"), ephemeral=True)


# ----------------------------------------------------------------------
# ⭐ Commission Review Modal & DM View
# ----------------------------------------------------------------------
class ReviewFeedbackModal(discord.ui.Modal):
    def __init__(self, guild_id: int, ticket_num: str, stars: int):
        super().__init__(title="⭐ Add Optional Feedback Review")
        self.guild_id = guild_id
        self.ticket_num = ticket_num
        self.stars = stars

        self.msg_input = discord.ui.TextInput(
            label="Feedback Comment (Server Rules Apply)",
            style=discord.TextStyle.paragraph,
            placeholder="Tell us what you liked about your commission experience...",
            required=False,
            max_length=500
        )
        self.add_item(self.msg_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        feedback_text = self.msg_input.value.strip() or "*(No written feedback provided)*"

        db = get_db()
        if db is not None:
            db["reviews"].insert_one({
                "guild_id": self.guild_id,
                "ticket_num": self.ticket_num,
                "user_id": str(interaction.user.id),
                "username": str(interaction.user),
                "stars": self.stars,
                "feedback": feedback_text,
                "timestamp": datetime.utcnow()
            })

            config = db["guild_config"].find_one({"guild_id": self.guild_id}) or {}
            rev_config = config.get("review_config", {})
            rev_ch_id = rev_config.get("channel_id")

            if rev_ch_id and str(rev_ch_id).isdigit():
                client_bot = interaction.client
                ch = client_bot.get_channel(int(rev_ch_id))
                if ch:
                    stars_str = "⭐" * self.stars
                    r_embed = discord.Embed(
                        title=f"🌟 New Client Review ({stars_str})",
                        description=f"**Client:** {interaction.user.mention}\n**Ticket Ref:** #{self.ticket_num}\n**Rating:** {self.stars}/5 Stars\n\n**Comment:**\n*{feedback_text}*",
                        color=SUCCESS_COLOR if self.stars >= 4 else EMBED_COLOR,
                        timestamp=discord.utils.utcnow()
                    )
                    r_embed.set_footer(text="Server Rules Apply • Official Studio Review")
                    try:
                        await ch.send(embed=r_embed)
                    except Exception:
                        pass

        await interaction.followup.send(embed=info_embed("Thank You!", "Your feedback has been submitted to the Studio Showcase! Server Rules Apply."), ephemeral=True)


class ReviewRatingView(discord.ui.View):
    def __init__(self, guild_id: int, ticket_num: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.ticket_num = ticket_num

    @discord.ui.button(label="⭐ 1 Star", style=discord.ButtonStyle.secondary, custom_id="orca_rate_1")
    async def rate_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReviewFeedbackModal(self.guild_id, self.ticket_num, 1))

    @discord.ui.button(label="⭐⭐ 2 Stars", style=discord.ButtonStyle.secondary, custom_id="orca_rate_2")
    async def rate_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReviewFeedbackModal(self.guild_id, self.ticket_num, 2))

    @discord.ui.button(label="⭐⭐⭐ 3 Stars", style=discord.ButtonStyle.primary, custom_id="orca_rate_3")
    async def rate_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReviewFeedbackModal(self.guild_id, self.ticket_num, 3))

    @discord.ui.button(label="⭐⭐⭐⭐ 4 Stars", style=discord.ButtonStyle.primary, custom_id="orca_rate_4")
    async def rate_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReviewFeedbackModal(self.guild_id, self.ticket_num, 4))

    @discord.ui.button(label="⭐⭐⭐⭐⭐ 5 Stars", style=discord.ButtonStyle.success, custom_id="orca_rate_5")
    async def rate_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReviewFeedbackModal(self.guild_id, self.ticket_num, 5))


# ----------------------------------------------------------------------
# Modal Ticket Close Field Component
# ----------------------------------------------------------------------
class ModalTicketClose(discord.ui.Modal):
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
            return await interaction.response.send_message(embed=error_embed("Staff permissions required to close this ticket."), ephemeral=True)

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
# 🏷️ Staff Ticket Claiming & Control View
# ----------------------------------------------------------------------
class TicketChannelControlView(discord.ui.View):
    def __init__(self, category: str, ticket_data: dict):
        super().__init__(timeout=None)
        self.category = category
        self.ticket_data = ticket_data

    @discord.ui.button(label="📌 Claim Ticket", style=discord.ButtonStyle.primary, custom_id="orca_ticket_claim_btn")
    async def claim_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_user_close_ticket(interaction.user, self.category, interaction.guild):
            return await interaction.response.send_message(embed=error_embed("Staff permissions required to claim this ticket."), ephemeral=True)

        channel = interaction.channel
        clean_user = re.sub(r'[^a-zA-Z0-9]', '', interaction.user.name.lower()) or "staff"
        new_name = f"{channel.name}-{clean_user}"[:100]

        try:
            await channel.edit(name=new_name, reason=f"Ticket claimed by {interaction.user}")
            button.disabled = True
            button.label = f"📌 Claimed by {interaction.user.display_name}"
            await interaction.response.edit_message(view=self)
            await channel.send(embed=info_embed("📌 Ticket Claimed", f"{interaction.user.mention} has claimed primary development responsibility for this ticket!"))
        except Exception as e:
            await interaction.response.send_message(embed=error_embed(f"Claim error: {e}"), ephemeral=True)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="orca_ticket_close_btn")
    async def close_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_user_close_ticket(interaction.user, self.category, interaction.guild):
            return await interaction.response.send_message(embed=error_embed("Staff permissions required to close this ticket."), ephemeral=True)

        await interaction.response.send_modal(ModalTicketClose(self.category, self.ticket_data))


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
        for rid in role_ids:
            if rid and str(rid).isdigit():
                role = guild.get_role(int(rid))
                if role:
                    try:
                        await interaction.user.add_roles(role, reason="ORCA Studio Member Verification")
                        added_roles.append(role.mention)
                    except Exception:
                        pass

        if added_roles:
            role_str = ", ".join(added_roles)
            embed = discord.Embed(
                title="✅ Member Verification Complete!",
                description=f"Welcome to **{guild.name}**! You have been verified and granted the following roles:\n\n{role_str}",
                color=SUCCESS_COLOR
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Could not assign verification roles."), ephemeral=True)


# ----------------------------------------------------------------------
# Custom Client Role Creation Modal & View
# ----------------------------------------------------------------------
class CustomRoleModal(discord.ui.Modal):
    def __init__(self, modal_title: str, label_name: str, label_color: str):
        super().__init__(title=modal_title[:45])

        self.name_input = discord.ui.TextInput(
            label=label_name[:45],
            style=discord.TextStyle.short,
            placeholder="e.g. VIP Client, Bot Architect",
            required=True,
            max_length=32
        )
        self.add_item(self.name_input)

        self.color_input = discord.ui.TextInput(
            label=label_color[:45],
            style=discord.TextStyle.short,
            placeholder="#3B82F6",
            required=True,
            default="#3B82F6",
            max_length=10
        )
        self.add_item(self.color_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        cr_config = config.get("custom_role_config", {})

        role_name = self.name_input.value.strip()
        role_color = parse_hex_color(self.color_input.value)

        header_role_id = cr_config.get("header_role_id")
        header_role = guild.get_role(int(header_role_id)) if header_role_id and str(header_role_id).isdigit() else None

        try:
            new_role = await guild.create_role(
                name=role_name,
                color=role_color,
                reason=f"ORCA Custom Buyer Role created by {interaction.user}"
            )

            roles_to_assign = [new_role]
            if header_role:
                roles_to_assign.append(header_role)

            await interaction.user.add_roles(*roles_to_assign, reason="Custom Role Studio Creation")

            if header_role and header_role.position > 1:
                try:
                    await new_role.edit(position=max(1, header_role.position - 1))
                except Exception:
                    pass

            log_ch_id = cr_config.get("log_channel_id")
            if log_ch_id and str(log_ch_id).isdigit():
                log_ch = guild.get_channel(int(log_ch_id))
                if log_ch:
                    l_embed = discord.Embed(
                        title="🎨 Custom Role Created Audit Log",
                        description=f"**User:** {interaction.user.mention} (`{interaction.user.id}`)\n**Role Name:** `{role_name}`\n**Role Mention:** {new_role.mention}\n**Hex Color:** `{self.color_input.value}`",
                        color=role_color,
                        timestamp=discord.utils.utcnow()
                    )
                    try:
                        await log_ch.send(embed=l_embed)
                    except Exception:
                        pass

            embed = discord.Embed(
                title="🎨 Custom Role Created & Assigned!",
                description=f"Your new custom role {new_role.mention} has been created and placed directly under your configured divider role!",
                color=role_color
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Failed to create custom role: {e}"), ephemeral=True)


class CustomRolePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎨 Create Custom Role", style=discord.ButtonStyle.blurple, custom_id="orca_custom_role_btn")
    async def create_role_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": interaction.guild.id}) if db is not None else {}
        cr_config = config.get("custom_role_config", {})

        m_title = cr_config.get("modal_title", "🎨 Create Custom Client Role")
        l_name = cr_config.get("modal_label_name", "Role Name")
        l_color = cr_config.get("modal_label_color", "Role Hex Color Code")

        await interaction.response.send_modal(CustomRoleModal(m_title, l_name, l_color))


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
            "closed": False,
            "order_status": "In Queue"
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
            clean_category = re.sub(r'[^a-zA-Z0-9]', '', self.category.lower()) or "ticket"
            channel_name = f"ticket-{clean_category}-{counter}"[:100]

            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=False, attach_files=True),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
            }

            staff_role_id = cat_data.get("staffRole") or config.get("ping_role_id")
            if staff_role_id and str(staff_role_id).isdigit():
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
            welcome_desc = cat_data.get("welcomeDesc") or "Welcome to your private ticket channel! Please accept the Terms of Service below to enable chat permissions."
            
            w_embed = discord.Embed(title=welcome_title, description=welcome_desc, color=SUCCESS_COLOR)
            w_embed.set_author(name=f"{interaction.user} ({interaction.user.id})", icon_url=interaction.user.display_avatar.url)
            
            for ans in current_answers:
                w_embed.add_field(name=ans["label"][:256], value=ans["value"][:1024], inline=False)

            ping_text = interaction.user.mention
            if staff_role_id and str(staff_role_id).isdigit():
                role = interaction.guild.get_role(int(staff_role_id))
                if role:
                    ping_text += f" {role.mention}"

            try:
                await ticket_channel.send(content=ping_text, embed=w_embed, view=TicketChannelControlView(self.category, ticket_record))
                tos_embed = discord.Embed(
                    title="📜 Studio Terms of Service & Revision Policy",
                    description="By commissioning ORCA Studio, you agree that revisions are limited after delivery and payments are non-refundable once development begins.",
                    color=EMBED_COLOR
                )
                await ticket_channel.send(embed=tos_embed, view=TOSAgreementView(interaction.user.id))
            except Exception:
                pass

        response_text = f"Thank you! Your ticket channel has been created: {ticket_channel.mention}" if ticket_channel else "Thank you for submitting your ticket details!"
        await interaction.followup.send(embed=info_embed("✅ Ticket Opened!", response_text), ephemeral=True)


class FormPanelView(discord.ui.View):
    def __init__(self, category: str = "Custom Bot Commission", button_label: str = "📝 Open Ticket"):
        super().__init__(timeout=None)
        self.category = category
        custom_id = re.sub(r'[^a-zA-Z0-9_]', '_', category.lower())[:100]

        btn = discord.ui.Button(
            label=button_label[:80],
            style=discord.ButtonStyle.blurple,
            custom_id=f"orca_form_{custom_id}"
        )
        btn.callback = self.open_form_callback
        self.add_item(btn)

    async def open_form_callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        if is_lockdown_active(guild_id, interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("System is in Total Lockdown mode."), ephemeral=True)

        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted from submitting tickets."), ephemeral=True)

        if is_maintenance(guild_id, interaction.user.id, interaction.guild):
            return await interaction.response.send_message(embed=error_embed("The ticket system is currently undergoing maintenance."), ephemeral=True)

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}
        
        cat_configs = config.get("category_configs", {}) if config else {}
        cat_data = cat_configs.get(self.category, {})

        questions = cat_data.get("questions", [])
        if not questions:
            questions = [
                {"label": "Please explain your ticket details", "placeholder": "Type your details here...", "style": "paragraph", "required": True}
            ]

        title = cat_data.get("panelTitle") or f"📋 {self.category.upper()}"
        modal = ChainedCustomModal(
            title=title,
            category=self.category,
            questions_chunk=questions[:5],
            all_questions=questions,
            current_index=0,
            previous_answers=[]
        )
        await interaction.response.send_modal(modal)


# ----------------------------------------------------------------------
# 💼 Commission Quote & Price Estimator Select Component
# ----------------------------------------------------------------------
class PriceEstimatorSelect(discord.ui.Select):
    def __init__(self, options_data: List[dict], currency: str):
        self.currency = currency
        self.type_map = {opt["name"]: opt["price"] for opt in options_data if opt.get("name") and opt.get("price")}

        select_options = [
            discord.SelectOption(label=name, description=f"Rate: {currency}{price}", value=name)
            for name, price in self.type_map.items()
        ]

        super().__init__(
            placeholder="Choose Bot Type & Feature Add-ons...",
            min_values=1,
            max_values=len(select_options) if select_options else 1,
            options=select_options if select_options else [discord.SelectOption(label="Standard Bot", value="Standard Bot")]
        )

    async def callback(self, interaction: discord.Interaction):
        selected_types = self.values
        total = sum([self.type_map.get(t, 0) for t in selected_types])

        details = "\n".join([f"• **{t}**: {self.currency}{self.type_map.get(t, 0)}" for t in selected_types])
        embed = discord.Embed(
            title="💼 Instant Commission Quote Estimate",
            description=f"Calculated estimate based on your selections:\n\n{details}\n\n**Total Estimated Price:** `{self.currency}{total}`",
            color=SUCCESS_COLOR
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PriceEstimatorPanelView(discord.ui.View):
    def __init__(self, types_data: List[dict], currency: str):
        super().__init__(timeout=None)
        if types_data:
            self.add_item(PriceEstimatorSelect(types_data, currency))


# ----------------------------------------------------------------------
# Main Cog Engine
# ----------------------------------------------------------------------
class Orca(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()
        self.user_ping_tracker = {}
        self.bot_health_monitor_loop.start()

    def cog_unload(self):
        self.bot_health_monitor_loop.cancel()

    async def cog_load(self):
        self.bot.add_view(VerificationView())
        self.bot.add_view(CustomRolePanelView())
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

    # ----------------------------------------------------------------------
    # 🛡️ Basic AutoMod Listener (Bypasses Admins & Owner)
    # ----------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if message.author.guild_permissions.administrator or is_owner(message.author.id):
            return

        db = get_db()
        if db is None:
            return

        config = db["guild_config"].find_one({"guild_id": message.guild.id}) or {}
        am = config.get("automod_config", {})

        # Anti-Link Filter (Permits Giphy/Tenor GIF links)
        if am.get("anti_link"):
            content_lower = message.content.lower()
            if ("discord.gg/" in content_lower or "discord.com/invite/" in content_lower or "http://" in content_lower or "https://" in content_lower):
                if not any(gif_domain in content_lower for gif_domain in ["tenor.com", "giphy.com", ".gif"]):
                    try:
                        await message.delete()
                        embed = discord.Embed(title="⚠️ AutoMod Security Action", description=f"{message.author.mention}, unauthorized links are not permitted here.", color=ERROR_COLOR)
                        return await message.channel.send(embed=embed, delete_after=5)
                    except Exception:
                        pass

        # Mass Ping Rate Limit Protection (2+ pings in under 10 seconds)
        if am.get("anti_ping"):
            total_pings = len(message.mentions) + len(message.role_mentions)
            if total_pings >= 2:
                now = time.time()
                user_id = message.author.id
                history = self.user_ping_tracker.get(user_id, [])
                history = [t for t in history if now - t < 10]
                history.append(now)
                self.user_ping_tracker[user_id] = history

                if len(history) >= 2:
                    try:
                        await message.delete()
                        embed = discord.Embed(title="⚠️ AutoMod Security Action", description=f"{message.author.mention}, mass or repeated pings are restricted.", color=ERROR_COLOR)
                        return await message.channel.send(embed=embed, delete_after=5)
                    except Exception:
                        pass

        # Bad Words Blacklist
        banned_str = am.get("banned_words", "")
        if banned_str:
            banned_words = [w.strip().lower() for w in banned_str.split(",") if w.strip()]
            for word in banned_words:
                if word in message.content.lower():
                    try:
                        await message.delete()
                        embed = discord.Embed(title="⚠️ AutoMod Security Action", description=f"{message.author.mention}, your message contained a blacklisted term.", color=ERROR_COLOR)
                        return await message.channel.send(embed=embed, delete_after=5)
                    except Exception:
                        pass

    # ----------------------------------------------------------------------
    # 👋 New Member Welcomer Listener
    # ----------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        db = get_db()
        if db is None:
            return

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
                    try:
                        await ch.send(embed=embed)
                    except Exception:
                        pass

        if wc.get("dm_enabled"):
            dm_text = wc.get("dm_message", "Welcome to {server}!").replace("{user}", member.name).replace("{server}", guild.name)
            dm_embed = discord.Embed(title=f"Welcome to {guild.name}!", description=dm_text, color=EMBED_COLOR)
            try:
                await member.send(embed=dm_embed)
            except Exception:
                pass

    # ----------------------------------------------------------------------
    # 🤖 Bot Hosting & Health Ping Monitor Loop
    # ----------------------------------------------------------------------
    @tasks.loop(minutes=10)
    async def bot_health_monitor_loop(self):
        for guild in self.bot.guilds:
            db = get_db()
            if db is None:
                continue

            config = db["guild_config"].find_one({"guild_id": guild.id}) or {}
            mc = config.get("monitor_config", {})
            log_ch_id = mc.get("log_channel_id")
            bot_ids = mc.get("bot_ids", [])

            if not log_ch_id or not bot_ids or not str(log_ch_id).isdigit():
                continue

            log_ch = guild.get_channel(int(log_ch_id))
            if not log_ch:
                continue

            for bid in bot_ids:
                if not bid or not str(bid).isdigit():
                    continue

                bot_member = guild.get_member(int(bid))
                if not bot_member:
                    try:
                        bot_member = await guild.fetch_member(int(bid))
                    except Exception:
                        bot_member = None

                if not bot_member or bot_member.status == discord.Status.offline:
                    embed = discord.Embed(
                        title="🚨 Bot Health Alert: Client Bot Offline",
                        description=f"Monitored Bot <@{bid}> 
                        appears to be **OFFLINE** or unreachable!",
                        color=ERROR_COLOR,
                        timestamp=discord.utils.utcnow()
                    )
                    try:
                        await log_ch.send(embed=embed)
                    except Exception:
                        pass

    @bot_health_monitor_loop.before_loop
    async def before_monitor_loop(self):
        await self.bot.wait_until_ready()

    async def execute_ticket_close(self, interaction: Optional[discord.Interaction], channel: discord.TextChannel, category: str, ticket_data: dict, reason: str, give_role: bool):
        guild = channel.guild
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
            if buyer_role_id and str(buyer_role_id).isdigit():
                buyer_role = guild.get_role(int(buyer_role_id))
                if buyer_role:
                    try:
                        await opener_member.add_roles(buyer_role, reason=f"ORCA Ticket closed")
                        role_granted_status = f"Yes ({buyer_role.mention})"

                        cr_config = config.get("custom_role_config", {})
                        cr_channel_id = cr_config.get("channel_id")
                        cr_channel = guild.get_channel(int(cr_channel_id)) if cr_channel_id and str(cr_channel_id).isdigit() else None

                        dm_embed = discord.Embed(
                            title="🎨 Your Custom Role Studio Link",
                            description=f"Your ticket in **{guild.name}** has been closed and your Buyer role has been granted!\n\n" +
                                        (f"You can now head over to {cr_channel.mention} to create your custom role!" if cr_channel else "Head over to the custom role channel in our server to create your custom role!"),
                            color=SUCCESS_COLOR
                        )
                        await opener_member.send(embed=dm_embed)
                    except Exception as e:
                        role_granted_status = f"Failed: {e}"

        messages_data = []
        try:
            async for msg in channel.history(limit=500, oldest_first=True):
                messages_data.append({
                    "author": str(msg.author),
                    "content": msg.clean_content,
                    "timestamp": msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "avatar": msg.author.display_avatar.url
                })
        except Exception:
            pass

        html_content = generate_html_transcript(channel.name, messages_data)
        file = discord.File(io.BytesIO(html_content.encode('utf-8')), filename=f"transcript-{channel.name}.html")

        log_channel_id = cat_data.get("logChannel") or config.get("log_channel_id")
        if log_channel_id and str(log_channel_id).isdigit():
            log_ch = guild.get_channel(int(log_channel_id))
            if log_ch:
                l_embed = discord.Embed(
                    title=f"📜 Ticket #{ticket_data.get('number', 'N/A')} HTML Transcript Log",
                    color=ERROR_COLOR,
                    timestamp=discord.utils.utcnow()
                )
                l_embed.add_field(name="Opener", value=f"<@{opener_id}>" if opener_id else "Unknown", inline=True)
                l_embed.add_field(name="Closed By", value=f"{interaction.user.mention}" if interaction else "Auto-Close System", inline=True)
                l_embed.add_field(name="Reason", value=f"*{reason}*", inline=False)
                try:
                    await log_ch.send(embed=l_embed, file=file)
                except Exception:
                    pass

        if opener_member:
            try:
                r_embed = discord.Embed(
                    title="⭐ Rate Your ORCA Studio Experience",
                    description=f"Your ticket #{ticket_data.get('number', '')} in **{guild.name}** is completed! Please rate your experience below 
                    (Server Rules Apply):",
                    color=EMBED_COLOR
                )
                r_embed.set_footer(text="Server Rules Apply • Studio Feedback")
                await opener_member.send(embed=r_embed, view=ReviewRatingView(guild.id, str(ticket_data.get('number', ''))))
            except Exception:
                pass

        if interaction:
            try:
                await interaction.followup.send(embed=info_embed("🔒 Closing Ticket", "Archiving channel in 5 seconds..."), ephemeral=True)
            except Exception:
                pass

        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"ORCA Ticket Closed")
        except Exception:
            pass

    # ====================================================================
    # Allowed Slash Commands
    # ====================================================================
    @app_commands.command(name="system-stats", description="Shows bot ping and uptime.")
    async def system_stats(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        uptime = int(time.time() - self.start_time)
        h, rem = divmod(uptime, 3600)
        m, s = divmod(rem, 60)
        await interaction.response.send_message(embed=discord.Embed(title="⚙️ ORCA System Stats", description=f"Ping: `{latency}ms`\nUptime: `{h}h {m}m {s}s`", color=EMBED_COLOR), ephemeral=True)

    @app_commands.command(name="dashboard", description="Get web control dashboard link.")
    async def dashboard(self, interaction: discord.Interaction):
        if not is_owner(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("Bot Owner only."), ephemeral=True)
        url = os.getenv("DASHBOARD_URL", "https://echo-dashboard.duckdns.org").strip()
        await interaction.response.send_message(embed=discord.Embed(title="🌐 ORCA Web Dashboard", description=f"[Open Control Panel]({url})", color=EMBED_COLOR), ephemeral=True)

    @app_commands.command(name="role-list", description="Display server roles in hierarchical order.")
    async def role_list_cmd(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(embed=error_embed("Server only."), ephemeral=True)

        await interaction.response.defer()
        guild = interaction.guild
        sorted_roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
        role_lines = [f"`{r.position}` — {r.mention}" for r in sorted_roles if r.name != "@everyone"]

        embed = discord.Embed(title="📜 Server Role Hierarchy Order", description="\n".join(role_lines[:40]) if role_lines else "*(No roles)*", color=EMBED_COLOR)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="order-status", description="Update or view commission order status.")
    @app_commands.describe(ticket_num="Ticket Number", status="Current status phase")
    @app_commands.choices(status=[
        app_commands.Choice(name="In Queue", value="In Queue"),
        app_commands.Choice(name="In Development", value="In Development"),
        app_commands.Choice(name="Testing Phase", value="Testing"),
        app_commands.Choice(name="Ready for Delivery", value="Ready for Delivery"),
        app_commands.Choice(name="Completed", value="Completed")
    ])
    async def order_status_cmd(self, interaction: discord.Interaction, ticket_num: int, status: Optional[app_commands.Choice[str]] = None):
        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        found = db["form_submissions"].find_one({"number": ticket_num})
        if not found:
            return await interaction.response.send_message(embed=error_embed(f"Order #{ticket_num} not found."), ephemeral=True)

        if status:
            if not interaction.user.guild_permissions.administrator and not is_owner(interaction.user.id):
                return await interaction.response.send_message(embed=error_embed("Staff permission required to update status."), ephemeral=True)

            db["form_submissions"].update_one({"number": ticket_num}, {"$set": {"order_status": status.value}})
            await interaction.response.send_message(embed=info_embed("💰 Order Status Updated", f"Order #{ticket_num} status updated to: **{status.value}**"), ephemeral=True)
        else:
            curr = found.get("order_status", "In Queue")
            embed = discord.Embed(title=f"📦 Order Status #{ticket_num}", color=EMBED_COLOR)
            embed.add_field(name="Category", value=found.get("category", "Custom Commission"), inline=True)
            embed.add_field(name="Current Progress", value=f"**{curr}**", inline=True)
            await interaction.response.send_message(embed=embed)

    # ====================================================================
    # Web Deploy Handlers
    # ====================================================================
    async def publish_announcement_from_web(self, data: dict):
        channel_id = data.get("channel_id")
        channel = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))

        ping_opt = data.get("ping")
        ping_content = ""
        if ping_opt == "everyone":
            ping_content = "@everyone"
        elif ping_opt == "here":
            ping_content = "@here"

        embed = discord.Embed(
            title=data.get("title", "📢 ANNOUNCEMENT"),
            description=data.get("description", ""),
            color=EMBED_COLOR,
            timestamp=discord.utils.utcnow()
        )
        await channel.send(content=ping_content if ping_content else None, embed=embed)

    async def deploy_verification_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        db = get_db()
        v_config = db["guild_config"].find_one({"guild_id": channel.guild.id}).get("verification_config", {}) if db else {}
        embed = discord.Embed(title=v_config.get("title", "🛡️ MEMBER VERIFICATION PORTAL"), description=v_config.get("description", "Click below to verify!"), color=EMBED_COLOR)
        await channel.send(embed=embed, view=VerificationView(v_config.get("button_label", "✅ Verify Access")))

    async def deploy_custom_role_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        embed = discord.Embed(title="🎨 CUSTOM ROLE CREATOR STUDIO", description="Click the button below to create your custom role!", color=EMBED_COLOR)
        await channel.send(embed=embed, view=CustomRolePanelView())

    async def deploy_estimator_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": channel.guild.id}) if db is not None else {}
        ec = config.get("estimator_config", {})

        types = ec.get("types", [])
        currency = ec.get("currency", "$")

        embed = discord.Embed(
            title="💼 COMMISSION QUOTE & PRICE ESTIMATOR",
            description="Select your required bot features and add-ons below to calculate an instant quote estimate!",
            color=EMBED_COLOR
        )
        view = PriceEstimatorPanelView(types, currency)
        await channel.send(embed=embed, view=view)

    async def deploy_form_panel_from_web(self, channel_id: int, category: str = "Custom Bot Commission"):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        embed = discord.Embed(title=f"📋 {category.upper()} TICKET PANEL", description="Click below to open a ticket!", color=EMBED_COLOR)
        await channel.send(embed=embed, view=FormPanelView(category=category))


async def setup(bot: commands.Bot):
    await bot.add_cog(Orca(bot))

