import io
import os
import re
import time
import asyncio
from datetime import datetime
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

DEFAULT_SHOP_TITLE = "🛒 ECHO Bot Shop"
DEFAULT_SHOP_DESCRIPTION = "Custom Discord bots built for your server. Reach out to get started!"
DEFAULT_SHOP_FOOTER = "ECHO • Bot Development Services"

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


def calculate_discount_price(price_str: str, percent: int) -> str:
    if not price_str or percent <= 0:
        return price_str
    num_match = re.search(r'\d+', price_str)
    if not num_match:
        return price_str
    original_num = int(num_match.group(0))
    discounted_num = max(1, round(original_num * (1 - percent / 100)))
    return price_str.replace(str(original_num), f"{discounted_num} (~~{original_num}~~ `{percent}% OFF`)")


def get_shop_items():
    try:
        db = get_db()
        if db is None:
            return []
        cursor = db["shop_items"].find({"available": {"$ne": False}}).sort("position", 1)
        return [(doc["item_id"], doc["name"], doc.get("description"), doc.get("price")) for doc in cursor]
    except Exception:
        return []


# ----------------------------------------------------------------------
# Persistent ticket views
# ----------------------------------------------------------------------
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.blurple, custom_id="echo_ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted from using the ticket system."), ephemeral=True)

        if is_maintenance(interaction.guild.id):
            return await interaction.response.send_message(embed=error_embed("The system is currently undergoing maintenance. Please try again later!"), ephemeral=True)

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

    async def cog_load(self):
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketCloseView())

    async def cog_check(self, ctx: commands.Context) -> bool:
        if is_blacklisted(ctx.author.id):
            await ctx.send(embed=error_embed("You are blacklisted from using ECHO commands."))
            return False
        return True

    def build_shop_embed(self) -> discord.Embed:
        embed = discord.Embed(title=DEFAULT_SHOP_TITLE, description=DEFAULT_SHOP_DESCRIPTION, color=EMBED_COLOR)

        db = get_db()
        guild_id = self.bot.guilds[0].id if self.bot.guilds else 0
        config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}
        global_discount = config.get("global_discount", 0)

        # Check for discount expiration
        discount_expires = config.get("discount_expires_at")
        if discount_expires and int(time.time()) > discount_expires:
            global_discount = 0
            if db is not None:
                db["guild_config"].update_one({"guild_id": guild_id}, {"$set": {"global_discount": 0, "discount_expires_at": None}})

        items = get_shop_items()
        if not items:
            embed.add_field(name="No items available", value="Check back later for new packages!", inline=False)
        else:
            if global_discount > 0:
                embed.description += f"\n\n🔥 **STOREWIDE SALE ACTIVE: {global_discount}% OFF ALL PACKAGES!**"

            for item_id, name, item_desc, price in items:
                field_value = item_desc or "\u200b"
                if price:
                    display_price = calculate_discount_price(price, global_discount) if global_discount > 0 else price
                    field_value += f"\n**Price:** {display_price}"
                embed.add_field(name=f"{name} — `#{item_id}`", value=field_value, inline=False)

        embed.set_footer(text=DEFAULT_SHOP_FOOTER)
        return embed

    # ====================================================================
    # 1. /shop
    # ====================================================================
    @app_commands.command(name="shop", description="View available bots and services privately.")
    async def shop(self, interaction: discord.Interaction):
        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted from using ECHO."), ephemeral=True)

        if is_maintenance(interaction.guild.id):
            return await interaction.response.send_message(embed=error_embed("The store is currently in maintenance mode."), ephemeral=True)

        embed = self.build_shop_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ====================================================================
    # 2. /system-stats
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

    # ====================================================================
    # 3. /redeem
    # ====================================================================
    @app_commands.command(name="redeem", description="Redeem a promo discount code.")
    async def redeem(self, interaction: discord.Interaction, code: str):
        if is_blacklisted(interaction.user.id):
            return await interaction.response.send_message(embed=error_embed("You are blacklisted."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        clean_code = code.strip().upper()
        promo = db["promo_codes"].find_one({"code": clean_code})
        if not promo:
            return await interaction.response.send_message(embed=error_embed("Invalid promo code!"), ephemeral=True)

        # Check Expiration Limit
        expires_at = promo.get("expires_at")
        if expires_at and int(time.time()) > expires_at:
            return await interaction.response.send_message(embed=error_embed("This promo code has expired!"), ephemeral=True)

        # Check Per-User Limit
        user_limit = promo.get("user_limit", 0)
        user_id_str = str(interaction.user.id)
        
        redemption_doc = db["promo_redemptions"].find_one({"code": clean_code, "user_id": user_id_str})
        times_used = redemption_doc.get("count", 0) if redemption_doc else 0

        if user_limit > 0 and times_used >= user_limit:
            return await interaction.response.send_message(embed=error_embed(f"You have reached the limit of {user_limit} redemption(s) for this code!"), ephemeral=True)

        # Increment User Redemption Count
        db["promo_redemptions"].update_one(
            {"code": clean_code, "user_id": user_id_str},
            {"$inc": {"count": 1}},
            upsert=True
        )

        discount = promo.get("discount", 0)
        embed = discord.Embed(
            title="🎉 Promo Code Redeemed!",
            description=f"Code **{clean_code}** is valid for **{discount}% OFF** your next purchase!\n\nMention this code inside your purchase ticket to claim your discount.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ====================================================================
    # 4. /check-promo (Owner Only)
    # ====================================================================
    @app_commands.command(name="check-promo", description="Check promo codes redeemed or won by a user (Owner Only).")
    async def check_promo(self, interaction: discord.Interaction, user: discord.User):
        is_bot_owner = await self.bot.is_owner(interaction.user)
        is_guild_owner = interaction.guild and (interaction.user.id == interaction.guild.owner_id)
        if not (is_bot_owner or is_guild_owner or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message(embed=error_embed("Only the server or bot owner can use this command."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        user_id_str = str(user.id)
        redemptions = list(db["promo_redemptions"].find({"user_id": user_id_str}))

        embed = discord.Embed(
            title=f"🎟️ Promo Code History for {user.name}",
            color=EMBED_COLOR
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        if not redemptions:
            embed.description = f"User {user.mention} (`{user.id}`) has not redeemed or used any promo codes yet."
        else:
            embed.description = f"Showing promo code activity for {user.mention} (`{user.id}`):"
            for doc in redemptions:
                code_name = doc.get("code", "UNKNOWN")
                count = doc.get("count", 0)
                promo = db["promo_codes"].find_one({"code": code_name})

                if promo:
                    discount = promo.get("discount", 0)
                    expires_at = promo.get("expires_at")
                    status = "Expired" if (expires_at and int(time.time()) > expires_at) else "Active"
                    value_str = f"**Discount:** `{discount}% OFF` | **Times Redeemed:** `{count}` | **Status:** `{status}`"
                else:
                    value_str = f"**Times Redeemed:** `{count}` | **Status:** `Code Deleted`"

                embed.add_field(name=f"🏷️ Code: `{code_name}`", value=value_str, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ====================================================================
    # 5. /blacklist
    # ====================================================================
    @app_commands.command(name="blacklist", description="Add or remove a user from the bot blacklist (Admins Only).")
    async def blacklist_cmd(self, interaction: discord.Interaction, user: discord.User, reason: Optional[str] = "No reason provided"):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin permission required."), ephemeral=True)

        db = get_db()
        if db is None:
            return await interaction.response.send_message(embed=error_embed("Database unavailable."), ephemeral=True)

        existing = db["blacklist"].find_one({"user_id": str(user.id)})
        if existing:
            db["blacklist"].delete_one({"user_id": str(user.id)})
            await interaction.response.send_message(embed=info_embed(f"✅ Removed {user.mention} (`{user.id}`) from the blacklist."), ephemeral=True)
        else:
            db["blacklist"].insert_one({"user_id": str(user.id), "username": str(user), "reason": reason})
            await interaction.response.send_message(embed=info_embed(f"⛔ Blacklisted {user.mention} (`{user.id}`). Reason: {reason}"), ephemeral=True)

    # ====================================================================
    # 6. /dashboard
    # ====================================================================
    @app_commands.command(name="dashboard", description="Get the link to access the ECHO Web Control Dashboard.")
    async def dashboard(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Only server admins can access the dashboard link."), ephemeral=True)

        dashboard_url = os.getenv("DASHBOARD_URL", "https://echo-dashboard.duckdns.org").strip()
        if not dashboard_url.startswith(("http://", "https://")):
            dashboard_url = f"https://{dashboard_url}"

        embed = discord.Embed(
            title="🌐 ECHO Web Dashboard",
            description="Manage shop packages, customize ticket panels, set discounts, and manage blacklists directly from the web panel.",
            color=EMBED_COLOR
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Web Control Panel", url=dashboard_url, style=discord.ButtonStyle.link, emoji="🎛️"))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ====================================================================
    # Web Deploy & Edit Handlers
    # ====================================================================
    async def deploy_ticket_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            raise Exception(f"Channel ID {channel_id} not found.")

        db = get_db()
        config = db["guild_config"].find_one({"guild_id": channel.guild.id}) if db is not None else {}

        title = config.get("title") or "💳 MARKETPLACE REGISTER"
        desc = config.get("description") or "Click the button below to open a private ticket channel."

        embed = discord.Embed(title=title, description=desc, color=EMBED_COLOR)
        msg = await channel.send(embed=embed, view=TicketPanelView())

        if db is not None:
            db["guild_config"].update_one({"guild_id": channel.guild.id}, {"$set": {"last_ticket_msg_id": msg.id, "panel_channel_id": channel.id}})

    async def update_ticket_panel_from_web(self):
        db = get_db()
        guild_id = self.bot.guilds[0].id if self.bot.guilds else 0
        config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}

        msg_id = config.get("last_ticket_msg_id")
        channel_id = config.get("panel_channel_id")
        if not msg_id or not channel_id:
            raise Exception("No active ticket panel message stored.")

        channel = self.bot.get_channel(channel_id)
        if not channel:
            raise Exception("Ticket panel channel not found.")

        msg = await channel.fetch_message(msg_id)
        title = config.get("title") or "💳 MARKETPLACE REGISTER"
        desc = config.get("description") or "Click the button below to open a private ticket channel."

        embed = discord.Embed(title=title, description=desc, color=EMBED_COLOR)
        await msg.edit(embed=embed, view=TicketPanelView())

    async def deploy_shop_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            raise Exception(f"Channel ID {channel_id} not found.")

        embed = self.build_shop_embed()
        msg = await channel.send(embed=embed)

        db = get_db()
        if db is not None:
            db["guild_config"].update_one({"guild_id": channel.guild.id}, {"$set": {"last_shop_msg_id": msg.id, "shop_channel_id": channel.id}})

    async def update_shop_panel_from_web(self):
        db = get_db()
        guild_id = self.bot.guilds[0].id if self.bot.guilds else 0
        config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}

        msg_id = config.get("last_shop_msg_id")
        channel_id = config.get("shop_channel_id")
        if not msg_id or not channel_id:
            raise Exception("No active shop message stored.")

        channel = self.bot.get_channel(channel_id)
        if not channel:
            raise Exception("Shop channel not found.")

        msg = await channel.fetch_message(msg_id)
        embed = self.build_shop_embed()
        await msg.edit(embed=embed)

    # ====================================================================
    # Ticket open/close & Web Logging Handlers
    # ====================================================================
    async def handle_ticket_open(self, interaction: discord.Interaction):
        guild = interaction.guild
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else None

        if not config:
            return await interaction.response.send_message(
                embed=error_embed("The ticket system isn't configured for this server yet."), ephemeral=True
            )

        staff_role_id = config.get("staff_role_id")
        category_id = config.get("category_id")
        welcome_message = config.get("welcome_message", "Thanks for opening a ticket!")
        counter = config.get("ticket_counter", 0) + 1

        if db is not None:
            db["guild_config"].update_one({"guild_id": guild.id}, {"$set": {"ticket_counter": counter}})

        safe_name = re.sub(r"[^a-z0-9-]", "", interaction.user.name.lower().replace(" ", "-"))[:20] or "user"
        channel_name = f"{safe_name}-{counter}"

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

        welcome_embed = discord.Embed(title="🎫 New Ticket", description=welcome_message, color=EMBED_COLOR)
        welcome_embed.add_field(name="Opened by", value=interaction.user.mention, inline=True)
        welcome_embed.add_field(name="Ticket #", value=f"`{counter}`", inline=True)
        await ticket_channel.send(content=interaction.user.mention, embed=welcome_embed, view=TicketCloseView())

        await interaction.response.send_message(embed=info_embed(f"✅ Ticket created: {ticket_channel.mention}"), ephemeral=True)

        # Log Ticket Creation to Web MongoDB
        if db is not None:
            db["ticket_logs"].insert_one({
                "ticket_number": counter,
                "username": str(interaction.user),
                "user_id": str(interaction.user.id),
                "action": "OPENED",
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            })

        # Send Automatic Receipt DM to User
        try:
            receipt_embed = discord.Embed(title="🧾 ECHO Purchase Ticket Receipt", color=discord.Color.green())
            receipt_embed.add_field(name="Ticket Number", value=f"`#{counter}`", inline=True)
            receipt_embed.add_field(name="Server", value=guild.name, inline=True)
            receipt_embed.add_field(name="Date & Time", value=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
            receipt_embed.set_footer(text="Keep this receipt for your records.")
            await interaction.user.send(embed=receipt_embed)
        except discord.Forbidden:
            pass

    async def handle_ticket_close(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else None

        await interaction.response.send_message(embed=info_embed("🔒 Closing ticket, generating transcript..."), ephemeral=True)

        lines = []
        async for msg in channel.history(limit=500, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.content or "*(no text content — attachment/embed)*"
            lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {content}")
        transcript_text = "\n".join(lines) or "(no messages)"

        # Extract ticket number from channel name
        match = re.search(r'\d+', channel.name)
        ticket_num = match.group(0) if match else "N/A"

        # Log Ticket Closure and Transcript to Web Dashboard Database
        if db is not None:
            db["ticket_logs"].insert_one({
                "ticket_number": ticket_num,
                "username": str(interaction.user),
                "user_id": str(interaction.user.id),
                "action": "CLOSED",
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                "transcript": transcript_text
            })

        await asyncio.sleep(2)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Echo(bot))

