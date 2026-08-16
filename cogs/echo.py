import io
import os
import re
import time
import asyncio
import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

try:
    import pymongo
except ImportError:
    pymongo = None

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
# Storage (MongoDB Cloud - Safe & Non-Blocking)
# ----------------------------------------------------------------------
def get_db():
    mongo_uri = os.getenv("MONGO_URI")
    if mongo_uri and pymongo:
        try:
            # 5 second timeout prevents startup hanging if connection is delayed
            client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            return client["echo_bot"]
        except Exception as e:
            print(f"⚠️ MongoDB Connection Error: {e}")
            return None
    return None


def init_db():
    try:
        db = get_db()
        if db is None:
            return
        # Ensure shop_settings document exists
        if not db["shop_settings"].find_one({"id": 1}):
            db["shop_settings"].insert_one({
                "id": 1,
                "title": DEFAULT_SHOP_TITLE,
                "description": DEFAULT_SHOP_DESCRIPTION,
                "color": DEFAULT_SHOP_COLOR,
                "footer": DEFAULT_SHOP_FOOTER,
                "thumbnail_url": None,
                "banner_url": None
            })
    except Exception as e:
        print(f"⚠️ init_db warning: {e}")


def save_guild_config(guild_id, log_channel_id, staff_role_id, category_id, welcome_message):
    try:
        db = get_db()
        if db is None:
            return
        existing = db["guild_config"].find_one({"guild_id": guild_id})
        counter = existing.get("ticket_counter", 0) if existing else 0
        db["guild_config"].update_one(
            {"guild_id": guild_id},
            {
                "$set": {
                    "guild_id": guild_id,
                    "log_channel_id": log_channel_id,
                    "staff_role_id": staff_role_id,
                    "category_id": category_id,
                    "welcome_message": welcome_message,
                    "ticket_counter": counter
                }
            },
            upsert=True
        )
    except Exception as e:
        print(f"⚠️ save_guild_config warning: {e}")


def get_guild_config(guild_id):
    try:
        db = get_db()
        if db is None:
            return None
        doc = db["guild_config"].find_one({"guild_id": guild_id})
        if not doc:
            return None
        return (
            doc.get("log_channel_id"),
            doc.get("staff_role_id"),
            doc.get("category_id"),
            doc.get("welcome_message"),
            doc.get("ticket_counter", 0)
        )
    except Exception as e:
        print(f"⚠️ get_guild_config warning: {e}")
        return None


def increment_ticket_counter(guild_id, new_value):
    try:
        db = get_db()
        if db is None:
            return
        db["guild_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {"ticket_counter": new_value}}
        )
    except Exception as e:
        print(f"⚠️ increment_ticket_counter warning: {e}")


# --- shop settings ---
def get_shop_settings():
    try:
        db = get_db()
        if db is None:
            return (DEFAULT_SHOP_TITLE, DEFAULT_SHOP_DESCRIPTION, DEFAULT_SHOP_COLOR, DEFAULT_SHOP_FOOTER, None, None)
        doc = db["shop_settings"].find_one({"id": 1})
        if not doc:
            return (DEFAULT_SHOP_TITLE, DEFAULT_SHOP_DESCRIPTION, DEFAULT_SHOP_COLOR, DEFAULT_SHOP_FOOTER, None, None)
        return (
            doc.get("title", DEFAULT_SHOP_TITLE),
            doc.get("description", DEFAULT_SHOP_DESCRIPTION),
            doc.get("color", DEFAULT_SHOP_COLOR),
            doc.get("footer", DEFAULT_SHOP_FOOTER),
            doc.get("thumbnail_url"),
            doc.get("banner_url")
        )
    except Exception as e:
        print(f"⚠️ get_shop_settings warning: {e}")
        return (DEFAULT_SHOP_TITLE, DEFAULT_SHOP_DESCRIPTION, DEFAULT_SHOP_COLOR, DEFAULT_SHOP_FOOTER, None, None)


def update_shop_settings(**fields):
    try:
        db = get_db()
        if db is None:
            return
        current_vals = get_shop_settings()
        current = {
            "title": current_vals[0],
            "description": current_vals[1],
            "color": current_vals[2],
            "footer": current_vals[3],
            "thumbnail_url": current_vals[4],
            "banner_url": current_vals[5],
        }
        current.update({k: v for k, v in fields.items() if v is not None})
        db["shop_settings"].update_one(
            {"id": 1},
            {"$set": current},
            upsert=True
        )
    except Exception as e:
        print(f"⚠️ update_shop_settings warning: {e}")


def reset_shop_settings():
    try:
        db = get_db()
        if db is None:
            return
        db["shop_settings"].update_one(
            {"id": 1},
            {
                "$set": {
                    "title": DEFAULT_SHOP_TITLE,
                    "description": DEFAULT_SHOP_DESCRIPTION,
                    "color": DEFAULT_SHOP_COLOR,
                    "footer": DEFAULT_SHOP_FOOTER,
                    "thumbnail_url": None,
                    "banner_url": None
                }
            },
            upsert=True
        )
        db["shop_items"].delete_many({})
    except Exception as e:
        print(f"⚠️ reset_shop_settings warning: {e}")


# --- shop items ---
def add_shop_item(name, description, price):
    try:
        db = get_db()
        if db is None:
            return 1
        last_item = db["shop_items"].find_one(sort=[("item_id", -1)])
        next_id = (last_item["item_id"] + 1) if last_item and "item_id" in last_item else 1

        last_pos = db["shop_items"].find_one(sort=[("position", -1)])
        next_pos = (last_pos["position"] + 1) if last_pos and "position" in last_pos else 1

        db["shop_items"].insert_one({
            "item_id": next_id,
            "name": name,
            "description": description,
            "price": price,
            "position": next_pos
        })
        return next_id
    except Exception as e:
        print(f"⚠️ add_shop_item warning: {e}")
        return 1


def remove_shop_item(item_id) -> bool:
    try:
        db = get_db()
        if db is None:
            return False
        res = db["shop_items"].delete_one({"item_id": item_id})
        return res.deleted_count > 0
    except Exception as e:
        print(f"⚠️ remove_shop_item warning: {e}")
        return False


def edit_shop_item(item_id, name=None, description=None, price=None) -> bool:
    try:
        db = get_db()
        if db is None:
            return False
        row = db["shop_items"].find_one({"item_id": item_id})
        if row is None:
            return False
        new_name = name if name is not None else row.get("name")
        new_desc = description if description is not None else row.get("description")
        new_price = price if price is not None else row.get("price")
        db["shop_items"].update_one(
            {"item_id": item_id},
            {"$set": {"name": new_name, "description": new_desc, "price": new_price}}
        )
        return True
    except Exception as e:
        print(f"⚠️ edit_shop_item warning: {e}")
        return False


def get_shop_items():
    try:
        db = get_db()
        if db is None:
            return []
        cursor = db["shop_items"].find().sort("position", 1)
        return [(doc["item_id"], doc["name"], doc["description"], doc["price"]) for doc in cursor]
    except Exception as e:
        print(f"⚠️ get_shop_items warning: {e}")
        return []


def move_shop_item(item_id, direction: str) -> bool:
    """direction: 'up' or 'down'. Swaps position with the neighboring item."""
    try:
        db = get_db()
        if db is None:
            return False
        rows = list(db["shop_items"].find().sort("position", 1))
        ids = [r["item_id"] for r in rows]
        if item_id not in ids:
            return False
        idx = ids.index(item_id)
        swap_idx = idx - 1 if direction == "up" else idx + 1
        if swap_idx < 0 or swap_idx >= len(rows):
            return False
        pos_a = rows[idx]["position"]
        pos_b = rows[swap_idx]["position"]
        id_b = rows[swap_idx]["item_id"]
        db["shop_items"].update_one({"item_id": item_id}, {"$set": {"position": pos_b}})
        db["shop_items"].update_one({"item_id": id_b}, {"$set": {"position": pos_a}})
        return True
    except Exception as e:
        print(f"⚠️ move_shop_item warning: {e}")
        return False


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
            return await interaction.followup.send(embed=error_embed(f"Couldn't post the poll.\n
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

        # Scan for existing log channel instead of deleting it
        log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)

        log_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }
        if staff_role:
            log_overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        if log_channel is None:
            try:
                log_channel = await guild.create_text_channel(name=LOG_CHANNEL_NAME, overwrites=log_overwrites, reason="Auto-created by /setup")
            except discord.Forbidden:
                return await interaction.followup.send(embed=error_embed("I don't have permission to create channels."), ephemeral=True)
        else:
            # Re-apply correct permissions if log channel already exists
            try:
                for target, overwrite in log_overwrites.items():
                    await log_channel.set_permissions(target, overwrite=overwrite)
            except discord.Forbidden:
                pass

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

        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
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

        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
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



