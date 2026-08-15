import io
import re
import time
import sqlite3
import asyncio
import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

DB_FILE = "echo_data.db"
EMBED_COLOR = discord.Color.blurple()
ERROR_COLOR = discord.Color.red()

# Search safety caps — scanning a server's full history unbounded would hammer
# Discord's rate limits on any server with real message volume.
MAX_MESSAGES_PER_CHANNEL = 500
MAX_SEARCH_RESULTS = 15


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="❌ Error", description=message, color=ERROR_COLOR)


def info_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR)


def hex_to_rgb(hex_str: str, fallback=(153, 170, 181)):
    try:
        hex_str = hex_str.lstrip("#")
        return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


# ----------------------------------------------------------------------
# Storage (only used by the ticket system — everything else is stateless)
# ----------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            log_channel_id INTEGER,
            staff_role_id INTEGER,
            category_id INTEGER,
            welcome_message TEXT,
            ticket_counter INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()


def save_guild_config(guild_id, log_channel_id, staff_role_id, category_id, welcome_message):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO guild_config (guild_id, log_channel_id, staff_role_id, category_id, welcome_message, ticket_counter)
        VALUES (?, ?, ?, ?, ?, COALESCE((SELECT ticket_counter FROM guild_config WHERE guild_id = ?), 0))
        ON CONFLICT(guild_id) DO UPDATE SET
            log_channel_id=excluded.log_channel_id,
            staff_role_id=excluded.staff_role_id,
            category_id=excluded.category_id,
            welcome_message=excluded.welcome_message
    ''', (guild_id, log_channel_id, staff_role_id, category_id, welcome_message, guild_id))
    conn.commit()
    conn.close()


def get_guild_config(guild_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT log_channel_id, staff_role_id, category_id, welcome_message, ticket_counter FROM guild_config WHERE guild_id = ?', (guild_id,))
    row = c.fetchone()
    conn.close()
    return row


def increment_ticket_counter(guild_id, new_value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE guild_config SET ticket_counter = ? WHERE guild_id = ?', (new_value, guild_id))
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Persistent ticket views
# ----------------------------------------------------------------------
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.blurple, custom_id="echo_ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Echo")
        if cog is None:
            return await interaction.response.send_message(embed=error_embed("Ticket system unavailable."), ephemeral=True)
        await cog.handle_ticket_open(interaction)


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.red, custom_id="echo_ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Echo")
        if cog is None:
            return await interaction.response.send_message(embed=error_embed("Ticket system unavailable."), ephemeral=True)
        await cog.handle_ticket_close(interaction)


# ----------------------------------------------------------------------
# Main cog
# ----------------------------------------------------------------------
class Echo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()
        init_db()
        # Register persistent views so buttons keep working after restarts
        # without needing /setup to be run again.
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketCloseView())

    # ====================================================================
    # /search
    # ====================================================================
    @app_commands.command(
        name="search",
        description=f"Search server messages (scans up to {MAX_MESSAGES_PER_CHANNEL} messages/channel, {MAX_SEARCH_RESULTS} results max)."
    )
    @app_commands.describe(
        query="Text or keyword to search for.",
        match_type="Partial keyword match (case-insensitive) or exact match.",
        channel="Limit search to a specific channel (default: all readable channels).",
        user="Filter results by a specific user.",
    )
    @app_commands.choices(match_type=[
        app_commands.Choice(name="Partial / Keyword (case-insensitive)", value="partial"),
        app_commands.Choice(name="Exact Match", value="exact"),
    ])
    async def search(
        self,
        interaction: discord.Interaction,
        query: str,
        match_type: str = "partial",
        channel: Optional[discord.TextChannel] = None,
        user: Optional[discord.Member] = None,
    ):
        if not interaction.guild:
            return await interaction.response.send_message(embed=error_embed("This command only works in a server."), ephemeral=True)

        await interaction.response.defer()

        guild = interaction.guild
        channels_to_search = [channel] if channel else guild.text_channels
        query_lower = query.lower()
        results = []
        channels_scanned = 0
        channels_skipped_permissions = 0

        for ch in channels_to_search:
            if len(results) >= MAX_SEARCH_RESULTS:
                break

            perms = ch.permissions_for(guild.me)
            if not (perms.read_messages and perms.read_message_history):
                channels_skipped_permissions += 1
                continue

            channels_scanned += 1
            try:
                async for message in ch.history(limit=MAX_MESSAGES_PER_CHANNEL):
                    if user and message.author.id != user.id:
                        continue
                    content = message.content or ""
                    if not content:
                        continue
                    if match_type == "exact":
                        if query not in content:
                            continue
                    else:
                        if query_lower not in content.lower():
                            continue
                    results.append(message)
                    if len(results) >= MAX_SEARCH_RESULTS:
                        break
            except (discord.Forbidden, discord.HTTPException):
                channels_skipped_permissions += 1
                continue

        if not results:
            note = f"\n(Skipped {channels_skipped_permissions} channel(s) I can't read.)" if channels_skipped_permissions else ""
            return await interaction.followup.send(embed=error_embed(f"No matching messages found.{note}"))

        embed = discord.Embed(title=f"🔍 Search results for `{query}`", color=EMBED_COLOR)
        for msg in results:
            snippet = msg.content if len(msg.content) <= 150 else msg.content[:150] + "…"
            ts = int(msg.created_at.timestamp())
            embed.add_field(
                name=f"{msg.author.display_name} in #{msg.channel.name}",
                value=f"{snippet}\n[Jump to message]({msg.jump_url}) • <t:{ts}:R>",
                inline=False,
            )
        footer = f"Searched {channels_scanned} channel(s) • {len(results)} result(s)"
        if len(results) >= MAX_SEARCH_RESULTS:
            footer += f" (capped at {MAX_SEARCH_RESULTS})"
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed)

    # ====================================================================
    # /system-stats
    # ====================================================================
    @app_commands.command(name="system-stats", description="Shows bot ping, uptime, server count, and user count.")
    async def system_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        latency_ms = round(self.bot.latency * 1000)
        uptime_seconds = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count or 0 for g in self.bot.guilds)

        embed = discord.Embed(title="⚙️ ECHO System Stats", color=EMBED_COLOR)
        embed.add_field(name="📶 Ping", value=f"`{latency_ms}ms`", inline=True)
        embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str}`", inline=True)
        embed.add_field(name="🌐 Servers", value=f"`{guild_count}`", inline=True)
        embed.add_field(name="👥 Users (approx)", value=f"`{user_count}`", inline=True)
        await interaction.followup.send(embed=embed)

    # ====================================================================
    # /custom-role create
    # ====================================================================
    custom_role_group = app_commands.Group(name="custom-role", description="Manage custom server roles.")

    @custom_role_group.command(name="create", description="Create a role with just a name and color (no permissions).")
    @app_commands.describe(name="Role name.", color="Hex color, e.g. #ff6b6b (default: Discord gray).")
    async def custom_role_create(self, interaction: discord.Interaction, name: str, color: str = "#99aab5"):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                embed=error_embed("You need the **Manage Roles** permission to use this."), ephemeral=True
            )

        r, g, b = hex_to_rgb(color)
        try:
            role = await interaction.guild.create_role(
                name=name,
                colour=discord.Colour.from_rgb(r, g, b),
                permissions=discord.Permissions.none(),
                reason=f"Created by {interaction.user} via /custom-role create",
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("I don't have permission to create roles."), ephemeral=True
            )

        embed = discord.Embed(title="✅ Role Created", color=discord.Colour.from_rgb(r, g, b))
        embed.add_field(name="Name", value=f"`{role.name}`", inline=True)
        embed.add_field(name="Color", value=f"`{color}`", inline=True)
        embed.add_field(name="Role", value=role.mention, inline=True)
        await interaction.response.send_message(embed=embed)

    # ====================================================================
    # /role-list
    # ====================================================================
    @app_commands.command(name="role-list", description="Shows every role in the server with its ID.")
    async def role_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        roles = [r for r in sorted(guild.roles, key=lambda r: r.position, reverse=True) if not r.is_default()]

        if not roles:
            return await interaction.followup.send(embed=error_embed("This server has no roles besides @everyone."))

        lines = [f"{r.mention} — `{r.id}`" for r in roles]
        embed = discord.Embed(title=f"📋 Roles in {guild.name}", color=EMBED_COLOR)

        chunk = ""
        chunk_num = 1
        for line in lines:
            if len(chunk) + len(line) + 1 > 1000:
                embed.add_field(name=f"Roles ({chunk_num})", value=chunk, inline=False)
                chunk = ""
                chunk_num += 1
            chunk += line + "\n"
        if chunk:
            embed.add_field(name=f"Roles ({chunk_num})" if chunk_num > 1 else "Roles", value=chunk, inline=False)

        embed.set_footer(text=f"{len(roles)} role(s) total")
        await interaction.followup.send(embed=embed)

    # ====================================================================
    # /poll — native Discord poll, admin only
    # ====================================================================
    @app_commands.command(name="poll", description="Create a native Discord poll. Server admins only.")
    @app_commands.describe(
        question="The poll question.",
        options="Answer options separated by | (2-10 options).",
        duration_hours="How long the poll runs, in hours (1-168, default 24).",
        multiple_choice="Allow selecting more than one answer (default: off).",
        ping_role="Optional role to ping when the poll is posted.",
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        duration_hours: app_commands.Range[int, 1, 168] = 24,
        multiple_choice: bool = False,
        ping_role: Optional[discord.Role] = None,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                embed=error_embed("Only server admins can create polls."), ephemeral=True
            )

        opts = [o.strip()[:55] for o in options.split("|") if o.strip()]
        if not (2 <= len(opts) <= 10):
            return await interaction.response.send_message(
                embed=error_embed("Provide between 2 and 10 options, separated by `|`."), ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        poll_obj = discord.Poll(
            question=question[:300],
            duration=datetime.timedelta(hours=duration_hours),
            multiple=multiple_choice,
        )
        for opt in opts:
            poll_obj.add_answer(text=opt)

        try:
            await interaction.channel.send(
                content=ping_role.mention if ping_role else None,
                poll=poll_obj,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except Exception as e:
            return await interaction.followup.send(embed=error_embed(f"Couldn't post the poll.\n```{str(e)[:300]}```"), ephemeral=True)

        await interaction.followup.send(embed=info_embed("✅ Poll posted."), ephemeral=True)

    # ====================================================================
    # /shop — EDIT THE PLACEHOLDER TEXT BELOW WITH YOUR REAL DETAILS
    # ====================================================================
    @app_commands.command(name="shop", description="View available bots and services.")
    async def shop(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛒 ECHO Bot Shop",
            description="Custom Discord bots built for your server. Reach out to get started!",
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="What I offer",
            value="*(edit me)* Custom-built bots tailored to your server — utility, moderation, tickets, and more.",
            inline=False,
        )
        embed.add_field(
            name="Pricing",
            value="*(edit me)* Contact for a quote based on your requirements.",
            inline=False,
        )
        embed.add_field(
            name="Interested?",
            value="*(edit me)* DM `@yourusername` or contact the server owner to discuss your project.",
            inline=False,
        )
        embed.set_footer(text="ECHO • Bot Development Services")
        await interaction.response.send_message(embed=embed)

    # ====================================================================
    # /setup — configure + deploy the ticket system, admin only
    # ====================================================================
    @app_commands.command(name="setup", description="Configure and deploy the ticket system. Server admins only.")
    @app_commands.describe(
        panel_channel="Channel to post the 'Open Ticket' panel in.",
        log_channel="Channel where ticket info and transcripts get logged.",
        staff_role="Role that can see and manage all tickets (optional).",
        category="Category to create ticket channels under (optional).",
        welcome_message="Message posted inside each new ticket channel (optional).",
    )
    async def setup_command(
        self,
        interaction: discord.Interaction,
        panel_channel: discord.TextChannel,
        log_channel: discord.TextChannel,
        staff_role: Optional[discord.Role] = None,
        category: Optional[discord.CategoryChannel] = None,
        welcome_message: Optional[str] = None,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                embed=error_embed("Only server admins can run setup."), ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        final_welcome = welcome_message or "Thanks for opening a ticket! Staff will be with you shortly."
        save_guild_config(
            interaction.guild.id,
            log_channel.id,
            staff_role.id if staff_role else None,
            category.id if category else None,
            final_welcome,
        )

        panel_embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Click the button below to open a private ticket channel.",
            color=EMBED_COLOR,
        )
        try:
            await panel_channel.send(embed=panel_embed, view=TicketPanelView())
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=error_embed(f"I don't have permission to post in {panel_channel.mention}."), ephemeral=True
            )

        confirm = discord.Embed(title="✅ Ticket System Configured", color=discord.Color.green())
        confirm.add_field(name="Panel posted in", value=panel_channel.mention, inline=True)
        confirm.add_field(name="Logs go to", value=log_channel.mention, inline=True)
        if staff_role:
            confirm.add_field(name="Staff role", value=staff_role.mention, inline=True)
        if category:
            confirm.add_field(name="Category", value=category.name, inline=True)
        await interaction.followup.send(embed=confirm, ephemeral=True)

    # ====================================================================
    # Ticket open/close handlers (called by the persistent views above)
    # ====================================================================
    async def handle_ticket_open(self, interaction: discord.Interaction):
        guild = interaction.guild
        config = get_guild_config(guild.id)
        if not config:
            return await interaction.response.send_message(
                embed=error_embed("The ticket system isn't set up yet. Ask an admin to run `/setup`."), ephemeral=True
            )

        log_channel_id, staff_role_id, category_id, welcome_message, counter = config
        new_counter = counter + 1

        safe_name = re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower().replace(" ", "-"))[:20] or "user"
        channel_name = f"{safe_name}-{new_counter}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        category = guild.get_channel(category_id) if category_id else None

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name, overwrites=overwrites, category=category,
                reason=f"Ticket opened by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("I don't have permission to create channels."), ephemeral=True
            )

        increment_ticket_counter(guild.id, new_counter)

        welcome_embed = discord.Embed(title="🎫 New Ticket", description=welcome_message, color=EMBED_COLOR)
        welcome_embed.add_field(name="Opened by", value=interaction.user.mention, inline=True)
        welcome_embed.add_field(name="Ticket #", value=f"`{new_counter}`", inline=True)
        await ticket_channel.send(content=interaction.user.mention, embed=welcome_embed, view=TicketCloseView())

        await interaction.response.send_message(
            embed=info_embed(f"✅ Ticket created: {ticket_channel.mention}"), ephemeral=True
        )

        log_channel = guild.get_channel(log_channel_id)
        if log_channel:
            log_embed = discord.Embed(title="🎫 Ticket Opened", color=discord.Color.green())
            log_embed.add_field(name="User", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
            log_embed.add_field(name="Channel", value=ticket_channel.mention, inline=False)
            log_embed.add_field(name="Ticket #", value=f"`{new_counter}`", inline=False)
            log_embed.timestamp = discord.utils.utcnow()
            try:
                await log_channel.send(embed=log_embed)
            except discord.Forbidden:
                pass

    async def handle_ticket_close(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild
        config = get_guild_config(guild.id)
        if not config:
            return await interaction.response.send_message(embed=error_embed("The ticket system isn't configured."), ephemeral=True)

        log_channel_id = config[0]
        await interaction.response.send_message(embed=info_embed("🔒 Closing ticket, generating transcript..."), ephemeral=True)

        lines = []
        async for msg in channel.history(limit=500, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.content or "*(no text content — attachment/embed)*"
            lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {content}")
        transcript_text = "\n".join(lines) or "(no messages)"
        transcript_file = discord.File(io.BytesIO(transcript_text.encode("utf-8")), filename=f"{channel.name}-transcript.txt")

        log_channel = guild.get_channel(log_channel_id)
        if log_channel:
            log_embed = discord.Embed(title="🔒 Ticket Closed", color=discord.Color.red())
            log_embed.add_field(name="Channel", value=f"`#{channel.name}`", inline=True)
            log_embed.add_field(name="Closed by", value=interaction.user.mention, inline=True)
            log_embed.timestamp = discord.utils.utcnow()
            try:
                await log_channel.send(embed=log_embed, file=transcript_file)
            except discord.Forbidden:
                pass

        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Echo(bot))
