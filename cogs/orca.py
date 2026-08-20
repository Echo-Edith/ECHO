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


def parse_color(color_str: str) -> discord.Color:
    clean = color_str.strip().lstrip('#')
    try:
        return discord.Color(int(clean, 16))
    except Exception:
        named_colors = {
            "blue": discord.Color.blue(),
            "red": discord.Color.red(),
            "green": discord.Color.green(),
            "purple": discord.Color.purple(),
            "gold": discord.Color.gold(),
            "orange": discord.Color.orange(),
            "cyan": discord.Color.dark_teal()
        }
        return named_colors.get(color_str.lower(), discord.Color.blurple())


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
            if rid:
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


class CustomRoleModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="🎨 Create Custom Client Role")

        self.name_input = discord.ui.TextInput(
            label="Role Name",
            style=discord.TextStyle.short,
            placeholder="e.g. VIP Client, Bot Architect",
            required=True,
            max_length=32
        )
        self.add_item(self.name_input)

        self.color_input = discord.ui.TextInput(
            label="Role Color (Hex Code or Name)",
            style=discord.TextStyle.short,
            placeholder="e.g. #FF5733, #3498DB, or Blue",
            required=True,
            default="#3B82F6",
            max_length=20
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
        role_color = parse_color(self.color_input.value)

        header_role_id = cr_config.get("header_role_id")
        header_role = guild.get_role(int(header_role_id)) if header_role_id else None

        try:
            new_role = await guild.create_role(
                name=role_name,
                color=role_color,
                reason=f"ORCA Custom Buyer Role created by {interaction.user}"
            )

            await interaction.user.add_roles(new_role, reason="Custom Role Studio Creation")

            if header_role and header_role.position > 1:
                try:
                    await new_role.edit(position=max(1, header_role.position - 1))
                except Exception:
                    pass

            embed = discord.Embed(
                title="🎨 Custom Role Created & Assigned!",
                description=f"Your new role {new_role.mention} has been created and assigned to your profile!",
                color=role_color
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Failed to create role: {e}"), ephemeral=True)


class CustomRolePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎨 Create Custom Role", style=discord.ButtonStyle.blurple, custom_id="orca_custom_role_btn")
    async def create_role_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomRoleModal())


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
# Main Cog Engine
# ----------------------------------------------------------------------
class Orca(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

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

    async def cog_check(self, ctx: commands.Context) -> bool:
        guild_id = ctx.guild.id if ctx.guild else 0
        if is_lockdown_active(guild_id, ctx.author.id):
            await ctx.send(embed=error_embed("System is in Total Lockdown mode."))
            return False
        if is_blacklisted(ctx.author.id):
            await ctx.send(embed=error_embed("You are blacklisted."))
            return False
        return True

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
                        await opener_member.add_roles(buyer_role, reason=f"ORCA Ticket closed by {interaction.user}")
                        role_granted_status = f"Yes ({buyer_role.mention})"

                        # DM client channel link and instructions to make their custom role
                        cr_config = config.get("custom_role_config", {})
                        cr_channel_id = cr_config.get("channel_id")
                        cr_channel = guild.get_channel(int(cr_channel_id)) if cr_channel_id else None

                        dm_embed = discord.Embed(
                            title="🎨 Your Custom Role is Ready to Create!",
                            description=f"Your ticket in **{guild.name}** has been closed and your Buyer role has been granted!\n\n" +
                                        (f"You can now head over to {cr_channel.mention} to create your custom role!" if cr_channel else "Head over to the custom role channel in our server to create your custom role!"),
                            color=SUCCESS_COLOR
                        )
                        await opener_member.send(embed=dm_embed)
                    except Exception as e:
                        role_granted_status = f"Failed: {e}"

        messages = []
        try:
            async for msg in channel.history(limit=500, oldest_first=True):
                time_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                messages.append(f"[{time_str}] {msg.author}: {msg.clean_content}")
        except Exception:
            pass
        transcript_text = "\n".join(messages)

        log_channel_id = cat_data.get("logChannel") or config.get("log_channel_id")
        if log_channel_id:
            log_ch = guild.get_channel(int(log_channel_id))
            if log_ch:
                l_embed = discord.Embed(
                    title=f"🔒 Ticket #{ticket_data.get('number', 'N/A')} Closed [{category}]",
                    color=ERROR_COLOR,
                    timestamp=discord.utils.utcnow()
                )
                l_embed.add_field(name="Opener", value=f"<@{opener_id}>" if opener_id else "Unknown", inline=True)
                l_embed.add_field(name="Closed By", value=f"{interaction.user.mention}", inline=True)
                l_embed.add_field(name="Buyer Role Granted", value=role_granted_status, inline=True)
                l_embed.add_field(name="Reason", value=f"*{reason}*", inline=False)

                for ans in ticket_data.get("answers", []):
                    l_embed.add_field(name=ans["label"][:256], value=ans["value"][:1024], inline=False)

                file = discord.File(io.BytesIO(transcript_text.encode('utf-8')), filename=f"transcript-{channel.name}.txt") if transcript_text else None
                try:
                    await log_ch.send(embed=l_embed, file=file)
                except Exception:
                    pass

        try:
            await interaction.followup.send(embed=info_embed("🔒 Closing Ticket", "Archiving and deleting channel in 5 seconds..."), ephemeral=True)
            await asyncio.sleep(5)
            await channel.delete(reason=f"ORCA Ticket Closed by {interaction.user}")
        except Exception:
            pass

    # ====================================================================
    # Slash Commands
    # ====================================================================
    @app_commands.command(name="add-staff", description="Add a staff member (Grants role + Staff Header Role).")
    @app_commands.describe(user="User to promote", role="Specific staff role")
    async def add_staff_cmd(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role):
        if not interaction.guild or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Administrator permissions required."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        staff_config = config.get("staff_config", {})
        header_role = guild.get_role(int(staff_config.get("staff_header_role_id", 0))) if staff_config.get("staff_header_role_id") else None

        roles_to_add = [role]
        if header_role:
            roles_to_add.append(header_role)

        try:
            await user.add_roles(*roles_to_add, reason=f"ORCA Staff Promotion by {interaction.user}")
            await interaction.followup.send(embed=discord.Embed(title="✅ Staff Promoted", description=f"Granted {role.mention}" + (f" and {header_role.mention}" if header_role else ""), color=SUCCESS_COLOR), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Failed: {e}"), ephemeral=True)

    @app_commands.command(name="remove-staff", description="Remove staff role and Staff Header Role.")
    @app_commands.describe(user="User to demote")
    async def remove_staff_cmd(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.guild or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Administrator permissions required."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        staff_config = config.get("staff_config", {})
        header_role = guild.get_role(int(staff_config.get("staff_header_role_id", 0))) if staff_config.get("staff_header_role_id") else None

        try:
            if header_role and header_role in user.roles:
                await user.remove_roles(header_role, reason=f"ORCA Staff Demotion by {interaction.user}")
            await interaction.followup.send(embed=discord.Embed(title="⛔ Staff Demoted", description=f"Successfully updated staff status for {user.mention}.", color=ERROR_COLOR), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Failed: {e}"), ephemeral=True)

    @app_commands.command(name="staff-list", description="Display all staff members in a clean list.")
    async def staff_list_cmd(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(embed=error_embed("Server only."), ephemeral=True)

        await interaction.response.defer()
        guild = interaction.guild
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        staff_config = config.get("staff_config", {})
        header_id = staff_config.get("staff_header_role_id")
        header_id_int = int(header_id) if header_id and str(header_id).isdigit() else None

        staff_entries = []
        for member in guild.members:
            if member.bot:
                continue
            if header_id_int and header_id_int in [r.id for r in member.roles]:
                h_role = guild.get_role(header_id_int)
                staff_entries.append(f"{member.mention} — {h_role.mention if h_role else 'Staff'}")
            elif member.guild_permissions.administrator:
                staff_entries.append(f"{member.mention} — Admin")

        embed = discord.Embed(title="🛡️ ORCA Studio Staff Directory", description="\n".join(staff_entries) if staff_entries else "*(No staff found)*", color=EMBED_COLOR)
        embed.set_footer(text=f"Total Staff: {len(staff_entries)}")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="role-list", description="Display server roles in hierarchical order.")
    async def role_list_cmd(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(embed=error_embed("Server only."), ephemeral=True)

        await interaction.response.defer()
        guild = interaction.guild
        sorted_roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
        role_lines = [f"`{r.position}` — {r.mention} (`{len(r.members)} members`)" for r in sorted_roles if r.name != "@everyone"]

        embed = discord.Embed(title="📜 Server Role Hierarchy Order", description="\n".join(role_lines[:40]) if role_lines else "*(No roles)*", color=EMBED_COLOR)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="verify", description="Claim configured server access roles.")
    async def verify_slash_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
        v_config = config.get("verification_config", {})
        role_ids = v_config.get("roles", [])

        added = []
        for rid in role_ids:
            if rid:
                role = guild.get_role(int(rid))
                if role:
                    try:
                        await interaction.user.add_roles(role, reason="ORCA Verification")
                        added.append(role.mention)
                    except Exception:
                        pass

        if added:
            await interaction.followup.send(embed=discord.Embed(title="✅ Verified!", description=f"Granted: {', '.join(added)}", color=SUCCESS_COLOR), ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Verification failed."), ephemeral=True)

    @app_commands.command(name="close", description="Close current ticket channel (Staff only).")
    @app_commands.describe(reason="Reason for closing", give_buyer_role="Grant Buyer Role?")
    @app_commands.choices(give_buyer_role=[
        app_commands.Choice(name="Yes (Grant Buyer Role & DM Custom Role Link)", value="yes"),
        app_commands.Choice(name="No", value="no")
    ])
    async def close_slash_cmd(self, interaction: discord.Interaction, reason: Optional[str] = "No reason provided", give_buyer_role: app_commands.Choice[str] = None):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel) or not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message(embed=error_embed("Execute inside an active ticket channel."), ephemeral=True)

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
            return await interaction.response.send_message(embed=error_embed("Staff permission required."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        give_role = give_buyer_role.value == "yes" if give_buyer_role else False

        await self.execute_ticket_close(interaction, interaction.channel, category, ticket_data, reason, give_role)

    @app_commands.command(name="system-stats", description="Shows bot ping and uptime.")
    async def system_stats(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        uptime = int(time.time() - self.start_time)
        h, rem = divmod(uptime, 3600)
        m, s = divmod(rem, 60)
        await interaction.response.send_message(embed=discord.Embed(title="⚙️ ORCA System Stats", description=f"Ping: `{latency}ms`\nUptime: `{h}h {m}m {s}s`", color=EMBED_COLOR), ephemeral=True)

    async def deploy_verification_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        db = get_db()
        v_config = db["guild_config"].find_one({"guild_id": channel.guild.id}).get("verification_config", {}) if db else {}
        embed = discord.Embed(title=v_config.get("title", "🛡️ MEMBER VERIFICATION PORTAL"), description=v_config.get("description", "Click below to verify!"), color=EMBED_COLOR)
        await channel.send(embed=embed, view=VerificationView(v_config.get("button_label", "✅ Verify Access")))

    async def deploy_custom_role_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        embed = discord.Embed(title="🎨 CLIENT CUSTOM ROLE CREATOR STUDIO", description="Click the button below to create your custom role!", color=EMBED_COLOR)
        await channel.send(embed=embed, view=CustomRolePanelView())

    async def deploy_form_panel_from_web(self, channel_id: int, category: str = "Custom Bot Commission"):
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        embed = discord.Embed(title=f"📋 {category.upper()} TICKET PANEL", description="Click below to open a ticket!", color=EMBED_COLOR)
        await channel.send(embed=embed, view=FormPanelView(category=category))


async def setup(bot: commands.Bot):
    await bot.add_cog(Orca(bot))

