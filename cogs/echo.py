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

OWNER_ID = 1219266886143967245

MAX_MESSAGES_PER_CHANNEL = 500
MAX_SEARCH_RESULTS = 15

LOG_CHANNEL_NAME = "echo-ticket-logs"
TICKET_CATEGORY_NAME = "Tickets"

DEFAULT_SHOP_TITLE = "🛒 ECHO Bot Shop"
DEFAULT_SHOP_DESCRIPTION = "Custom Discord bots built for your server. Reach out to get started!"
DEFAULT_SHOP_FOOTER = "ECHO • Bot Development Services"
DEFAULT_SHOP_COLOR = "#5865F2"


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="❌ Error", description=message, color=ERROR_COLOR)


def info_embed(title: str, description: str = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR)


def parse_hex_strict(hex_str: str):
    try:
        h = hex_str.lstrip("#")
        if len(h) != 6:
            return None
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return None


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID


# ----------------------------------------------------------------------
# Storage
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS shop_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            title TEXT,
            description TEXT,
            color TEXT,
            footer TEXT,
            thumbnail_url TEXT,
            banner_url TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS shop_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            price TEXT,
            position INTEGER
        )
    ''')
    c.execute('''
        INSERT OR IGNORE INTO shop_settings (id, title, description, color, footer, thumbnail_url, banner_url)
        VALUES (1, ?, ?, ?, ?, NULL, NULL)
    ''', (DEFAULT_SHOP_TITLE, DEFAULT_SHOP_DESCRIPTION, DEFAULT_SHOP_COLOR, DEFAULT_SHOP_FOOTER))
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


# --- shop settings ---
def get_shop_settings():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT title, description, color, footer, thumbnail_url, banner_url FROM shop_settings WHERE id = 1')
    row = c.fetchone()
    conn.close()
    return row


def update_shop_settings(**fields):
    current = dict(zip(
        ["title", "description", "color", "footer", "thumbnail_url", "banner_url"],
        get_shop_settings(),
    ))
    current.update({k: v for k, v in fields.items() if v is not None})
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        UPDATE shop_settings SET title=?, description=?, color=?, footer=?, thumbnail_url=?, banner_url=? WHERE id=1
    ''', (current["title"], current["description"], current["color"], current["footer"],
          current["thumbnail_url"], current["banner_url"]))
    conn.commit()
    conn.close()


def reset_shop_settings():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        UPDATE shop_settings SET title=?, description=?, color=?, footer=?, thumbnail_url=NULL, banner_url=NULL WHERE id=1
    ''', (DEFAULT_SHOP_TITLE, DEFAULT_SHOP_DESCRIPTION, DEFAULT_SHOP_COLOR, DEFAULT_SHOP_FOOTER))
    c.execute('DELETE FROM shop_items')
    conn.commit()
    conn.close()


# --- shop items ---
def add_shop_item(name, description, price):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT COALESCE(MAX(position), 0) + 1 FROM shop_items')
    next_pos = c.fetchone()[0]
    c.execute('INSERT INTO shop_items (name, description, price, position) VALUES (?, ?, ?, ?)',
               (name, description, price, next_pos))
    item_id = c.lastrowid
    conn.commit()
    conn.close()
    return item_id


def remove_shop_item(item_id) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT item_id FROM shop_items WHERE item_id = ?', (item_id,))
    exists = c.fetchone() is not None
    if exists:
        c.execute('DELETE FROM shop_items WHERE item_id = ?', (item_id,))
        conn.commit()
    conn.close()
    return exists


def edit_shop_item(item_id, name=None, description=None, price=None) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT name, description, price FROM shop_items WHERE item_id = ?', (item_id,))
    row = c.fetchone()
    if row is None:
        conn.close()
        return False
    new_name = name if name is not None else row[0]
    new_desc = description if description is not None else row[1]
    new_price = price if price is not None else row[2]
    c.execute('UPDATE shop_items SET name=?, description=?, price=? WHERE item_id=?',
               (new_name, new_desc, new_price, item_id))
    conn.commit()
    conn.close()
    return True


def get_shop_items():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT item_id, name, description, price FROM shop_items ORDER BY position ASC')
    rows = c.fetchall()
    conn.close()
    return rows


def move_shop_item(item_id, direction: str) -> bool:
    """direction: 'up' or 'down'. Swaps position with the neighboring item."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT item_id, position FROM shop_items ORDER BY position ASC')
    rows = c.fetchall()
    ids = [r[0] for r in rows]
    if item_id not in ids:
        conn.close()
        return False
    idx = ids.index(item_id)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(rows):
        conn.close()
        return False
    pos_a = rows[idx][1]
    pos_b = rows[swap_idx][1]
    id_b = rows[swap_idx][0]
    c.execute('UPDATE shop_items SET position=? WHERE item_id=?', (pos_b, item_id))
    c.execute('UPDATE shop_items SET position=? WHERE item_id=?', (pos_a, id_b))
    conn.commit()
    conn.close()
    return True


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
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketCloseView())

    def build_shop_embed(self) -> discord.Embed:
        title, description, color, footer, thumbnail_url, banner_url = get_shop_settings()
        rgb = parse_hex_strict(color) or parse_hex_strict(DEFAULT_SHOP_COLOR)
        embed = discord.Embed(title=title, description=description, color=discord.Color.from_rgb(*rgb))

        for item_id, name, item_desc, price in get_shop_items():
            field_value = item_desc or "\u200b"
            if price:
                field_value += f"\n**Price:** `{price}`"
            embed.add_field(name=f"{name} — `#{item_id}`", value=field_value, inline=False)

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        if banner_url:
            embed.set_image(url=banner_url)
        if footer:
            embed.set_footer(text=footer)
        return embed

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
    # /role-list
    # ====================================================================
    @app_commands.command(name="role-list", description="Shows every role in the server with its ID and color.")
    async def role_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        roles = [r for r in sorted(guild.roles, key=lambda r: r.position, reverse=True) if not r.is_default()]

        if not roles:
            return await interaction.followup.send(embed=error_embed("This server has no roles besides @everyone."))

        lines = [f"{r.mention} — `{r.id}` — `#{r.color.value:06x}`" for r in roles]
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
    # SHOP — public display command
    # ====================================================================
    @app_commands.command(name="shop", description="View available bots and services.")
    @app_commands.describe(channel="Post the shop info in a specific channel instead of here (optional).")
    async def shop(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        embed = self.build_shop_embed()
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                return await interaction.response.send_message(embed=error_embed(f"I can't post in {channel.mention}."), ephemeral=True)
            await interaction.response.send_message(embed=info_embed(f"✅ Posted in {channel.mention}."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed)

    # ====================================================================
    # SHOP — owner-only customization commands
    # ====================================================================
    @app_commands.command(name="shop-edit", description="Edit the shop's title, description, color, or footer. Restricted command.")
    @app_commands.describe(
        title="New embed title (optional).",
        description="New embed description (optional).",
        color="New hex color for the embed, e.g. #5865F2 (optional).",
        footer="New footer text (optional).",
    )
    async def shop_edit(
        self,
        interaction: discord.Interaction,
        title: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        footer: Optional[str] = None,
    ):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)

        if color is not None and parse_hex_strict(color) is None:
            return await interaction.response.send_message(embed=error_embed("`color` must be a valid hex code like `#5865F2`."), ephemeral=True)

        if not any([title, description, color, footer]):
            return await interaction.response.send_message(embed=error_embed("Provide at least one field to update."), ephemeral=True)

        update_shop_settings(title=title, description=description, color=color, footer=footer)
        await interaction.response.send_message(embed=info_embed("✅ Shop settings updated.", "Run `/shop` to preview."), ephemeral=True)

    @app_commands.command(name="shop-set-image", description="Set the shop's thumbnail and/or banner image. Restricted command.")
    @app_commands.describe(
        thumbnail_url="Small image shown top-right of the embed (optional).",
        banner_url="Large image shown at the bottom of the embed (optional).",
        clear="Remove both images instead of setting new ones.",
    )
    async def shop_set_image(
        self,
        interaction: discord.Interaction,
        thumbnail_url: Optional[str] = None,
        banner_url: Optional[str] = None,
        clear: bool = False,
    ):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)

        if clear:
            update_shop_settings(thumbnail_url="", banner_url="")
            return await interaction.response.send_message(embed=info_embed("✅ Shop images cleared."), ephemeral=True)

        if not thumbnail_url and not banner_url:
            return await interaction.response.send_message(embed=error_embed("Provide `thumbnail_url` and/or `banner_url`, or set `clear:true`."), ephemeral=True)

        update_shop_settings(thumbnail_url=thumbnail_url, banner_url=banner_url)
        await interaction.response.send_message(embed=info_embed("✅ Shop images updated.", "Run `/shop` to preview."), ephemeral=True)

    @app_commands.command(name="shop-reset", description="Reset the entire shop (settings + all items) to defaults. Restricted command.")
    async def shop_reset(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)
        reset_shop_settings()
        await interaction.response.send_message(embed=info_embed("✅ Shop reset to defaults."), ephemeral=True)

    # --- items ---
    @app_commands.command(name="shop-add-item", description="Add an item/package to the shop. Restricted command.")
    @app_commands.describe(
        name="Item name, e.g. 'Custom Utility Bot'.",
        description="What's included.",
        price="Price text, e.g. '$50' or 'Starting at $30' (optional).",
    )
    async def shop_add_item(self, interaction: discord.Interaction, name: str, description: str, price: Optional[str] = None):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)
        item_id = add_shop_item(name, description, price)
        embed = discord.Embed(title="✅ Item Added", color=EMBED_COLOR)
        embed.add_field(name="Item ID", value=f"`{item_id}`", inline=True)
        embed.add_field(name="Name", value=name, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shop-edit-item", description="Edit an existing shop item by ID. Restricted command.")
    @app_commands.describe(
        item_id="The item's ID (from /shop-list-items).",
        name="New name (optional).",
        description="New description (optional).",
        price="New price text (optional).",
    )
    async def shop_edit_item(
        self,
        interaction: discord.Interaction,
        item_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        price: Optional[str] = None,
    ):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)
        if not any([name, description, price]):
            return await interaction.response.send_message(embed=error_embed("Provide at least one field to update."), ephemeral=True)
        if not edit_shop_item(item_id, name, description, price):
            return await interaction.response.send_message(embed=error_embed(f"No item with ID `{item_id}`."), ephemeral=True)
        await interaction.response.send_message(embed=info_embed(f"✅ Item `#{item_id}` updated."), ephemeral=True)

    @app_commands.command(name="shop-remove-item", description="Remove an item from the shop by ID. Restricted command.")
    @app_commands.describe(item_id="The item's ID (from /shop-list-items).")
    async def shop_remove_item(self, interaction: discord.Interaction, item_id: int):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)
        if not remove_shop_item(item_id):
            return await interaction.response.send_message(embed=error_embed(f"No item with ID `{item_id}`."), ephemeral=True)
        await interaction.response.send_message(embed=info_embed(f"✅ Item `#{item_id}` removed."), ephemeral=True)

    @app_commands.command(name="shop-list-items", description="List all shop items with their IDs. Restricted command.")
    async def shop_list_items(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)
        items = get_shop_items()
        if not items:
            return await interaction.response.send_message(embed=error_embed("The shop has no items yet. Add one with `/shop-add-item`."), ephemeral=True)
        embed = discord.Embed(title="📋 Shop Items", color=EMBED_COLOR)
        for item_id, name, description, price in items:
            value = description or "\u200b"
            if price:
                value += f"\n**Price:** `{price}`"
            embed.add_field(name=f"`#{item_id}` — {name}", value=value, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shop-move-item", description="Reorder a shop item up or down. Restricted command.")
    @app_commands.describe(item_id="The item's ID (from /shop-list-items).", direction="Move it up or down in the list.")
    @app_commands.choices(direction=[
        app_commands.Choice(name="Up", value="up"),
        app_commands.Choice(name="Down", value="down"),
    ])
    async def shop_move_item(self, interaction: discord.Interaction, item_id: int, direction: str):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)
        if not move_shop_item(item_id, direction):
            return await interaction.response.send_message(
                embed=error_embed(f"Couldn't move item `#{item_id}` {direction} (it may already be at the {'top' if direction == 'up' else 'bottom'})."),
                ephemeral=True,
            )
        await interaction.response.send_message(embed=info_embed(f"✅ Moved item `#{item_id}` {direction}."), ephemeral=True)

    @app_commands.command(name="shop-preview", description="Preview the shop embed privately before posting it. Restricted command.")
    async def shop_preview(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            return await interaction.response.send_message(embed=error_embed("This command is restricted."), ephemeral=True)
        await interaction.response.send_message(embed=self.build_shop_embed(), ephemeral=True)

    # ====================================================================
    # /setup — configure + deploy the ticket system, admin only
    # ====================================================================
    @app_commands.command(name="setup", description="Configure and deploy the ticket system. Server admins only.")
    @app_commands.describe(
        panel_channel="Channel to post the 'Open Ticket' panel in.",
        staff_role="Role that can see and manage all tickets (optional).",
        category="Category for ticket channels (optional — auto-created as 'Tickets' if omitted).",
        welcome_message="Message posted inside each new ticket channel (optional).",
        panel_title="Title for the ticket panel embed (optional).",
        panel_description="Description/body for the ticket panel embed (optional).",
    )
    async def setup_command(
        self,
        interaction: discord.Interaction,
        panel_channel: discord.TextChannel,
        staff_role: Optional[discord.Role] = None,
        category: Optional[discord.CategoryChannel] = None,
        welcome_message: Optional[str] = None,
        panel_title: Optional[str] = None,
        panel_description: Optional[str] = None,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Only server admins can run setup."), ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        for ch in list(guild.text_channels):
            if ch.name == LOG_CHANNEL_NAME:
                try:
                    await ch.delete(reason="Replaced by /setup")
                except discord.Forbidden:
                    pass

        # Denying @everyone here is sufficient: members with the Administrator
        # permission bypass channel overwrites in Discord, so they can still
        # see this channel while nobody else can.
        log_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        try:
            log_channel = await guild.create_text_channel(name=LOG_CHANNEL_NAME, overwrites=log_overwrites, reason="Auto-created by /setup")
        except discord.Forbidden:
            return await interaction.followup.send(embed=error_embed("I don't have permission to create channels."), ephemeral=True)

        if category is None:
            category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
            if category is None:
                try:
                    category = await guild.create_category(TICKET_CATEGORY_NAME, reason="Auto-created by /setup")
                except discord.Forbidden:
                    category = None

        final_welcome = welcome_message or "Thanks for opening a ticket! Staff will be with you shortly."
        save_guild_config(guild.id, log_channel.id, staff_role.id if staff_role else None,
                           category.id if category else None, final_welcome)

        panel_embed = discord.Embed(
            title=panel_title or "🎫 Support Tickets",
            description=panel_description or "Click the button below to open a private ticket channel.",
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
        confirm.add_field(name="Category", value=category.name if category else "*(none)*", inline=True)
        if staff_role:
            confirm.add_field(name="Staff role", value=staff_role.mention, inline=True)
        await interaction.followup.send(embed=confirm, ephemeral=True)

    # ====================================================================
    # Ticket open/close handlers
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
            return await interaction.response.send_message(embed=error_embed("I don't have permission to create channels."), ephemeral=True)

        increment_ticket_counter(guild.id, new_counter)

        welcome_embed = discord.Embed(title="🎫 New Ticket", description=welcome_message, color=EMBED_COLOR)
        welcome_embed.add_field(name="Opened by", value=interaction.user.mention, inline=True)
        welcome_embed.add_field(name="Ticket #", value=f"`{new_counter}`", inline=True)
        await ticket_channel.send(content=interaction.user.mention, embed=welcome_embed, view=TicketCloseView())

        await interaction.response.send_message(embed=info_embed(f"✅ Ticket created: {ticket_channel.mention}"), ephemeral=True)

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
