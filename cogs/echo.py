import io
import os
import re
import time
import asyncio
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
DEFAULT_SHOP_COLOR = "#5865F2"

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


def parse_hex_strict(hex_str: str):
    try:
        h = hex_str.lstrip("#")
        if len(h) != 6:
            return None
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return None


def get_shop_items():
    try:
        db = get_db()
        if db is None:
            return []
        cursor = db["shop_items"].find().sort("position", 1)
        return [(doc["item_id"], doc["name"], doc.get("description"), doc.get("price")) for doc in cursor]
    except Exception:
        return []


def get_total_tickets_count():
    try:
        db = get_db()
        if db is None:
            return 0
        total = 0
        cursor = db["guild_config"].find({}, {"ticket_counter": 1})
        for doc in cursor:
            total += doc.get("ticket_counter", 0)
        return total
    except Exception:
        return 0


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

    async def cog_load(self):
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketCloseView())

    def build_shop_embed(self) -> discord.Embed:
        embed = discord.Embed(title=DEFAULT_SHOP_TITLE, description=DEFAULT_SHOP_DESCRIPTION, color=EMBED_COLOR)

        items = get_shop_items()
        if not items:
            embed.add_field(name="No items available", value="Check back later for new packages!", inline=False)
        else:
            for item_id, name, item_desc, price in items:
                field_value = item_desc or "\u200b"
                if price:
                    field_value += f"\n**Price:** `{price}`"
                embed.add_field(name=f"{name} — `#{item_id}`", value=field_value, inline=False)

        embed.set_footer(text=DEFAULT_SHOP_FOOTER)
        return embed

    # ====================================================================
    # 1. /shop — Private view of bots and services
    # ====================================================================
    @app_commands.command(name="shop", description="View available bots and services privately.")
    async def shop(self, interaction: discord.Interaction):
        embed = self.build_shop_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ====================================================================
    # 2. /system-stats — Uptime, Ping, Active Servers & Total Tickets
    # ====================================================================
    @app_commands.command(name="system-stats", description="Shows bot ping, uptime, active servers, and total tickets opened.")
    async def system_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        latency_ms = round(self.bot.latency * 1000)
        uptime_seconds = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        guild_count = len(self.bot.guilds)
        total_tickets = await asyncio.to_thread(get_total_tickets_count)

        embed = discord.Embed(title="⚙️ ECHO System Stats", color=EMBED_COLOR)
        embed.add_field(name="📶 Ping", value=f"`{latency_ms}ms`", inline=True)
        embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str}`", inline=True)
        embed.add_field(name="🌐 Servers", value=f"`{guild_count}`", inline=True)
        embed.add_field(name="🎫 Total Tickets Opened", value=f"`{total_tickets}`", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ====================================================================
    # 3. /dashboard — Access Web Dashboard Link (Admins Only)
    # ====================================================================
    @app_commands.command(name="dashboard", description="Get the link to access the ECHO Web Control Dashboard.")
    async def dashboard(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Only server admins can access the dashboard link."), ephemeral=True)

        dashboard_url = os.getenv("DASHBOARD_URL", "https://your-bot.onrender.com")

        embed = discord.Embed(
            title="🌐 ECHO Web Dashboard",
            description="Manage your shop packages, customize ticket panels, and adjust embed settings directly from the web interface.",
            color=EMBED_COLOR
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Web Control Panel", url=dashboard_url, style=discord.ButtonStyle.link, emoji="🎛️"))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ====================================================================
    # Web Dashboard Deploy Commands
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
        await channel.send(embed=embed, view=TicketPanelView())

    async def deploy_shop_panel_from_web(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            raise Exception(f"Channel ID {channel_id} not found.")

        embed = self.build_shop_embed()
        await channel.send(embed=embed)

    # ====================================================================
    # Ticket open/close handlers
    # ====================================================================
    async def handle_ticket_open(self, interaction: discord.Interaction):
        guild = interaction.guild
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else None

        if not config:
            return await interaction.response.send_message(
                embed=error_embed("The ticket system isn't configured for this server yet. Set it up via the web dashboard."), ephemeral=True
            )

        log_channel_id = config.get("log_channel_id")
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

        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        if log_channel:
            log_embed = discord.Embed(title="🎫 Ticket Opened", color=discord.Color.green())
            log_embed.add_field(name="User", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
            log_embed.add_field(name="Channel", value=ticket_channel.mention, inline=False)
            log_embed.add_field(name="Ticket #", value=f"`{counter}`", inline=False)
            log_embed.timestamp = discord.utils.utcnow()
            try:
                await log_channel.send(embed=log_embed)
            except discord.Forbidden:
                pass

    async def handle_ticket_close(self, interaction: discord.Interaction):
        channel = interaction.channel
        guild = interaction.guild
        db = get_db()
        config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else None

        log_channel_id = config.get("log_channel_id") if config else None
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

        await asyncio.sleep(2)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Echo(bot))

