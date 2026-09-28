import os
import time
import json
import asyncio
import datetime
import collections
import aiohttp
import requests
import discord
from discord import app_commands
from discord.ext import commands
from pymongo import MongoClient

# Import blueprint retrieval helper and dynamic web URL from ai_brain.py
from ai_brain import get_blueprint_data, WEB_BUILDER_URL, BOT_API_KEY

AUTHORIZED_USER_ID = 1219266886143967245
ALLOWED_BUILDERS = {AUTHORIZED_USER_ID}  # Hardcoded primary owner + dynamic allowed users
is_lockdown = False
start_time = time.time()

# Webhook URL for web system logs and auto-ban alerts
SYSTEM_LOG_WEBHOOK_URL = os.environ.get("SYSTEM_LOG_WEBHOOK_URL", "")

# Spam detection thresholds
SPAM_THRESHOLD = 5  # Max messages allowed
TIME_WINDOW = 5     # Time window in seconds

# Design rate limits: Max 3 designs per 10 minutes (600 seconds)
DESIGN_LIMIT_MAX = 3
DESIGN_LIMIT_WINDOW = 600

# ---------------------------------------------------------------------------
# PYMONGO DATABASE CONNECTION & HELPER FUNCTIONS
# Exported for use in keep_alive.py
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None

db = mongo_client["bot_database"] if mongo_client is not None else None
bans_collection = db["website_bans"] if db is not None else None


def is_user_banned(discord_id: str) -> bool:
    """Checks if a user ID is banned in MongoDB."""
    if bans_collection is None:
        return False
    user = bans_collection.find_one({"discord_id": str(discord_id)})
    return user is not None


def ban_user(discord_id: str, reason: str = "No reason provided", dev_message: str = "") -> bool:
    """Bans a user ID in MongoDB with optional developer notes."""
    if bans_collection is None:
        return False
    str_id = str(discord_id)
    result = bans_collection.update_one(
        {"discord_id": str_id},
        {
            "$set": {
                "discord_id": str_id,
                "reason": reason,
                "dev_message": dev_message,
                "updated_at": time.time()
            }
        },
        upsert=True
    )
    return result.upserted_id is not None or result.modified_count > 0


def unban_user(discord_id: str) -> bool:
    """Unbans a user ID from MongoDB."""
    if bans_collection is None:
        return False
    result = bans_collection.delete_one({"discord_id": str(discord_id)})
    return result.deleted_count > 0


def get_all_bans() -> list:
    """Returns a list of all banned user dictionaries from MongoDB."""
    if bans_collection is None:
        return []
    return list(bans_collection.find({}, {"_id": 0, "discord_id": 1, "reason": 1, "dev_message": 1}))


def get_user_ban_details(discord_id: str) -> dict:
    """Retrieves full ban details (reason, dev_message) for a specific user ID."""
    if bans_collection is None:
        return None
    return bans_collection.find_one({"discord_id": str(discord_id)}, {"_id": 0})


# ---------------------------------------------------------------------------
# ACCESS CONTROL CHECKS
# ---------------------------------------------------------------------------

def is_owner():
    """Custom check restricting administrative commands strictly to primary AUTHORIZED_USER_ID."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == AUTHORIZED_USER_ID:
            return True
        embed = discord.Embed(
            title="403 Access Denied",
            description="```\nAccess Denied: You do not have permission to execute this administrative command.\n```",
            color=0xE74C3C
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return app_commands.check(predicate)


def can_build():
    """Custom check allowing primary AUTHORIZED_USER_ID and granted builders to run /build."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id in ALLOWED_BUILDERS:
            return True
        embed = discord.Embed(
            title="403 Access Denied",
            description="```\nAccess Denied: You do not have permission to execute the /build command.\n```",
            color=0xE74C3C
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False
    return app_commands.check(predicate)


async def update_website_lockdown_status(enable: bool):
    """Helper function to synchronize website lockdown maintenance mode."""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {"enable": enable}
            headers = {"X-Bot-Auth": BOT_API_KEY}
            async with session.post(f"{WEB_BUILDER_URL}/api/security/lockdown", json=payload, headers=headers, timeout=5) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"[Echo API] Failed to update website lockdown state: {e}")
        return False


async def send_system_webhook_log(content: str = None, embed: discord.Embed = None):
    """Sends log notifications to SYSTEM_LOG_WEBHOOK_URL."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            payload = {}
            if content:
                payload["content"] = content
            if embed:
                payload["embeds"] = [embed.to_dict()]
            async with session.post(SYSTEM_LOG_WEBHOOK_URL, json=payload, timeout=5) as resp:
                pass
    except Exception as e:
        print(f"[Webhook Log Error] {e}")


class BotJoinTosView(discord.ui.View):
    """Interactive button view on join requiring acknowledgment of terms by the inviter/owner."""
    def __init__(self, inviter_id: int):
        super().__init__(timeout=None)  # Persistent button state
        self.inviter_id = inviter_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.inviter_id or interaction.user.id == interaction.guild.owner_id:
            return True
        await interaction.response.send_message(
            "❌ Only the server owner or the administrator who added Echo Studio can accept these terms.", 
            ephemeral=True
        )
        return False

    @discord.ui.button(label="I Understand & Accept Terms", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def accept_terms(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = "Terms Accepted"
        button.style = discord.ButtonStyle.success
        
        accepted_embed = discord.Embed(
            title="✅ Agreement Acknowledged",
            description=(
                f"Terms accepted by {interaction.user.mention}.\n"
                "Echo Studio is active and initialized for this server."
            ),
            color=0x2ECC71
        )
        accepted_embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.edit_message(embed=accepted_embed, view=self)


class OrcaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_message_logs = collections.defaultdict(list)
        self.design_rate_limits = collections.defaultdict(list)  # User rate limits for designing

    # --- BOT JOIN EVENT ---
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if not guild.me.guild_permissions.administrator:
            return

        inviter_id = guild.owner_id
        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=5):
                if entry.target.id == self.bot.user.id:
                    inviter_id = entry.user.id
                    break
        except Exception:
            pass

        target_channel = guild.system_channel
        if not target_channel or not target_channel.permissions_for(guild.me).send_messages:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    target_channel = channel
                    break

        if not target_channel:
            return

        tos_embed = discord.Embed(
            title="⚠️ Echo Studio — Server Integration & Terms of Service",
            description=(
                "**Echo Studio has joined your server with Administrator privileges.**\n\n"
                "### 🛠️ Automated Operations Overview:\n"
                "• **Automated Structure Deployment**: When `/build` is executed, existing server channels, categories, and custom roles will be permanently removed and rebuilt.\n"
                "• **Moderation & Security**: Active 12-hour automated anti-spam restrictions apply to non-administrative members.\n\n"
                "### ⚖️ Terms of Service & Accountability Disclaimer:\n"
                "**By confirming below, you acknowledge that the bot developer is NOT accountable or liable for any lost messages, deleted roles, purged channels, or configuration updates executed during building or nuking operations.**\n\n"
                "*Only the person who invited this bot or the Server Owner can acknowledge these terms.*"
            ),
            color=0xF1C40F
        )
        tos_embed.set_footer(text="Echo Studio — Automated Server Infrastructure")

        view = BotJoinTosView(inviter_id=inviter_id)
        await target_channel.send(embed=tos_embed, view=view)

    # --- AUTOMATED ANTI-SPAM LISTENER ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        user_id = message.author.id
        current_time = time.time()

        self.user_message_logs[user_id].append(current_time)
        self.user_message_logs[user_id] = [
            t for t in self.user_message_logs[user_id] if current_time - t <= TIME_WINDOW
        ]

        if len(self.user_message_logs[user_id]) >= SPAM_THRESHOLD:
            try:
                duration = datetime.timedelta(hours=12)
                await message.author.timeout(duration, reason="Automated Anti-Spam: Excessive message frequency.")

                self.user_message_logs[user_id] = []

                embed = discord.Embed(
                    title="🚫 Member Restricted",
                    description=f"{message.author.mention} has been restricted for **12 hours** due to spamming.",
                    color=0xE74C3C
                )
                embed.set_footer(text="Echo Studio — Anti-Spam Protection")
                await message.channel.send(embed=embed)
            except discord.Forbidden:
                print(f"Failed to restrict {message.author}: Missing 'Moderate Members' permission.")
            except Exception as e:
                print(f"Error applying timeout: {e}")

    # --- HELPER: VERIFY INVITE LINK MATCHES SERVER ID ---
    async def verify_invite_matches_server(self, invite_url: str, server_id: str) -> tuple[bool, str]:
        """Validates if the provided server invite URL actually leads to the target server_id."""
        clean_invite = invite_url.strip()
        code = clean_invite.split("/")[-1].split("?")[0]
        
        try:
            invite = await self.bot.fetch_invite(code)
            if invite and invite.guild:
                if str(invite.guild.id) == str(server_id).strip():
                    return True, f"Verified: Invite matches server **{invite.guild.name}** (`{invite.guild.id}`)."
                else:
                    return False, f"Mismatch: Invite belongs to **{invite.guild.name}** (`{invite.guild.id}`), not Target Server ID `{server_id}`."
            return False, "Invalid Invite: Could not retrieve server information from invite code."
        except discord.NotFound:
            return False, "Invalid Invite: The invite link does not exist or is expired."
        except discord.HTTPException as e:
            return False, f"Verification Error: Failed to check invite ({e})."
        except Exception as e:
            return False, f"Verification Error: `{e}`"

    # --- HELPER: TRACK WEB DESIGN RATE LIMITS ---
    async def check_and_apply_design_ratelimit(self, user_id: str) -> tuple[bool, str]:
        """Tracks web design limit (3 per 10 mins). Instantly bans and alerts if exceeded."""
        now = time.time()
        uid = str(user_id)
        
        # Clean older requests outside the 10 minute window
        self.design_rate_limits[uid] = [
            t for t in self.design_rate_limits[uid] if now - t <= DESIGN_LIMIT_WINDOW
        ]
        
        self.design_rate_limits[uid].append(now)

        if len(self.design_rate_limits[uid]) > DESIGN_LIMIT_MAX:
            reason = f"Exceeded web layout generation rate limit ({DESIGN_LIMIT_MAX} designs per 10 minutes)."
            dev_msg = "Automated System Action: Excessive layout generation attempts."
            
            # Ban from website
            ban_user(uid, reason=reason, dev_message=dev_msg)

            # Sync ban to Web Builder API
            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "user_id": uid,
                        "action": "ban",
                        "reason": reason,
                        "dev_message": dev_msg
                    }
                    headers = {"X-Bot-Auth": BOT_API_KEY}
                    await session.post(f"{WEB_BUILDER_URL}/api/security/ban", json=payload, headers=headers, timeout=5)
            except Exception as e:
                print(f"[Echo RateLimit] Failed to sync web ban: {e}")

            # Send System Webhook Alert with Ping
            embed = discord.Embed(
                title="🚨 Automated Web Ban — Rate Limit Exceeded",
                description=f"<@{AUTHORIZED_USER_ID}> **A user was automatically banned for spamming layout generation.**",
                color=0xFF0000,
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="User ID", value=f"`{uid}` (<@{uid}>)", inline=True)
            embed.add_field(name="Limit Exceeded", value=f"`{len(self.design_rate_limits[uid])}` requests in 10 mins", inline=True)
            embed.add_field(name="Action Taken", value="Instantly Banned from Website Portal", inline=False)
            embed.set_footer(text="Echo Studio — Security Enforcement")

            await send_system_webhook_log(content=f"<@{AUTHORIZED_USER_ID}>", embed=embed)
            return False, "Rate limit exceeded. You have been automatically banned from the web portal for spamming."

        return True, "OK"

    # --- HELPER: DETAILED WEB ENTRY LOGGER ---
    async def log_web_entry(self, user_id: int, ip_address: str = "N/A", user_agent: str = "N/A"):
        """Logs detailed user profile, account age, and metadata when entering the website."""
        try:
            user = await self.bot.fetch_user(user_id)
        except Exception:
            user = None

        now = datetime.datetime.now(datetime.timezone.utc)
        
        embed = discord.Embed(
            title="🌐 Website Access Log",
            color=0x3498DB,
            timestamp=now
        )

        if user:
            created_at = user.created_at
            account_age_days = (now - created_at).days
            badges = [flag.name.replace("_", " ").title() for flag, value in user.public_flags if value]
            badge_str = ", ".join(badges) if badges else "None"

            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="Username / Tag", value=f"**{user.name}** (`{user}`)", inline=True)
            embed.add_field(name="Display Name", value=f"{user.display_name}", inline=True)
            embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
            embed.add_field(name="Account Created", value=f"<t:{int(created_at.timestamp())}:F>", inline=True)
            embed.add_field(name="Account Age", value=f"`{account_age_days} days old`", inline=True)
            embed.add_field(name="Badges / Flags", value=f"`{badge_str}`", inline=True)
            embed.add_field(name="Avatar URL", value=f"[Link to Avatar]({user.display_avatar.url})", inline=False)
        else:
            embed.add_field(name="User ID", value=f"`{user_id}` (Unable to fetch user profile)", inline=False)

        embed.add_field(name="IP Address", value=f"`{ip_address}`", inline=True)
        embed.add_field(name="User Agent", value=f"```\n{user_agent[:250]}\n```", inline=False)
        embed.set_footer(text="Echo Studio — Detailed Web Entry Tracker")

        await send_system_webhook_log(embed=embed)

    # --- 1. /help COMMAND ---
    @app_commands.command(name="help", description="Learn how to create and deploy a custom Discord server.")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛠️ How to Create & Deploy a Custom Discord Server",
            description=(
                "Echo Studio lets you automatically generate and deploy complete server layouts "
                "including categories, channels, and roles in seconds!\n\n"
                "### 📋 Step-by-Step Guide:\n\n"
                f"1️⃣ **Design on Web Builder**\n"
                f"Go to the [Echo Web Builder]({WEB_BUILDER_URL}) and enter your prompt, Target Server ID, and Invite Link.\n\n"
                "2️⃣ **Generate Layout**\n"
                "Click **Generate Layout** to preview the AI-generated categories, channels, and roles.\n\n"
                "3️⃣ **Submit Design**\n"
                "Click **Submit Design**. A direct file link will be generated and logged.\n\n"
                "4️⃣ **Deploy via `/build`**\n"
                "Authorized staff can run `/build file:<direct_link>` or attach the blueprint JSON file to deploy instantly."
            ),
            color=0x5865F2
        )
        embed.add_field(
            name="🌐 Web Builder Link",
            value=f"[Click Here to Launch Web Builder]({WEB_BUILDER_URL})",
            inline=False
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 2. /website COMMAND ---
    @app_commands.command(name="website", description="Provides the link to the web-based layout builder.")
    async def website(self, interaction: discord.Interaction):
        if is_lockdown:
            embed = discord.Embed(
                title="530 Site Under Maintenance",
                description="```\nHTTP 530: The web portal is currently undergoing scheduled maintenance.\nPlease check back later.\n```",
                color=0xF1C40F
            )
            embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠️ Interactive Server Builder",
            description=f"Click below to launch the AI Web Builder:\n\n🔗 **[Echo Web Builder Portal]({WEB_BUILDER_URL})**",
            color=0x5865F2
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 3. /server-info COMMAND ---
    @app_commands.command(name="server-info", description="Displays formatted Server ID and Invite Link in copyable code blocks.")
    @app_commands.describe(
        server_id="Target Discord Server ID (optional if executed inside a server)",
        invite_link="Invite URL to the target server (optional)"
    )
    async def server_info(
        self, 
        interaction: discord.Interaction, 
        server_id: str = None, 
        invite_link: str = None
    ):
        target_id = server_id.strip() if server_id else (str(interaction.guild.id) if interaction.guild else "N/A")
        
        formatted_invite = invite_link.strip() if invite_link else None
        if not formatted_invite and interaction.guild:
            try:
                for channel in interaction.guild.text_channels:
                    if channel.permissions_for(interaction.guild.me).create_instant_invite:
                        inv = await channel.create_invite(max_age=0, max_uses=0)
                        formatted_invite = inv.url
                        break
            except Exception:
                pass

        if not formatted_invite:
            formatted_invite = "N/A"

        guild_name = interaction.guild.name if interaction.guild else "Server Details"

        embed = discord.Embed(
            title=f"📌 {guild_name} — Information",
            description="Copy the Server ID or Invite Link below for use in the Echo Web Builder:",
            color=0x5865F2
        )
        embed.add_field(name="🆔 Server ID", value=f"`{target_id}`", inline=False)
        embed.add_field(name="🔗 Invite Link", value=f"`{formatted_invite}`", inline=False)
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    # --- 4. /ban COMMAND ---
    @app_commands.command(name="ban", description="Ban a user from Website, Discord, or Both.")
    @app_commands.describe(
        user="Target user to ban",
        location="Ban target location",
        dev_message="Developer message shown on the clean glass ban screen",
        reason="Internal reason for audit logs"
    )
    @app_commands.choices(location=[
        app_commands.Choice(name="Website Only", value="website"),
        app_commands.Choice(name="Discord Server Only", value="discord"),
        app_commands.Choice(name="Both Website & Discord", value="both")
    ])
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban_command(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        location: app_commands.Choice[str],
        dev_message: str = "Violating platform rules.",
        reason: str = "No internal reason specified."
    ):
        await interaction.response.defer(ephemeral=True)
        target_id = str(user.id)
        loc = location.value
        report = []

        # 1. Apply Website Ban (Updates MongoDB and syncs Clean Glass UI ban page payload)
        if loc in ("website", "both"):
            ban_user(target_id, reason=reason, dev_message=dev_message)

            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "user_id": target_id,
                        "action": "ban",
                        "reason": reason,
                        "dev_message": dev_message
                    }
                    headers = {"X-Bot-Auth": BOT_API_KEY}
                    async with session.post(f"{WEB_BUILDER_URL}/api/security/ban", json=payload, headers=headers, timeout=5) as resp:
                        if resp.status == 200:
                            report.append("✅ **Website Ban:** Applied. Clean Glass UI active with header **BANNED**.")
                        else:
                            report.append(f"⚠️ **Website Ban API Warning:** Returned HTTP {resp.status}")
            except Exception as e:
                report.append(f"⚠️ **Website Ban API Sync Failed:** {e}")

        # 2. Apply Discord Server Ban
        if loc in ("discord", "both"):
            if interaction.guild:
                try:
                    await interaction.guild.ban(user, reason=f"{reason} | Dev Note: {dev_message}")
                    report.append("✅ **Discord Ban:** User banned from current server.")
                except discord.Forbidden:
                    report.append("❌ **Discord Ban Error:** Missing permissions to ban user.")
                except Exception as e:
                    report.append(f"❌ **Discord Ban Error:** {e}")
            else:
                report.append("❌ **Discord Ban Error:** Command must be executed inside a server for Discord bans.")

        embed = discord.Embed(
            title="🔨 Ban Execution Summary",
            description="\n".join(report),
            color=0xE74C3C
        )
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Location", value=f"`{location.name}`", inline=True)
        embed.add_field(name="Developer Message (Visible to User)", value=f"```{dev_message}```", inline=False)
        embed.add_field(name="Internal Audit Reason", value=f"`{reason}`", inline=False)
        embed.set_footer(text="Echo Studio — Security Enforcement")
        
        await interaction.followup.send(embed=embed)

    # --- 5. /unban COMMAND ---
    @app_commands.command(name="unban", description="Unban a user from either the website portal or Discord server.")
    @app_commands.describe(
        user_id="The Discord User ID to unban",
        location="Target platform for the unban (Website or Discord/Server)",
        reason="Reason for unbanning the user"
    )
    @app_commands.choices(location=[
        app_commands.Choice(name="Website", value="website"),
        app_commands.Choice(name="Discord/Server", value="server")
    ])
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban_command(
        self,
        interaction: discord.Interaction,
        user_id: str,
        location: app_commands.Choice[str],
        reason: str = "Appeal accepted"
    ):
        target_id = user_id.strip()

        if location.value == "website":
            if not target_id.isdigit():
                await interaction.response.send_message("❌ Invalid input. Please enter a valid numeric Discord User ID.", ephemeral=True)
                return

            unbanned = unban_user(target_id)

            # Sync unban to web server API
            try:
                async with aiohttp.ClientSession() as session:
                    payload = {"user_id": target_id, "action": "unban"}
                    headers = {"X-Bot-Auth": BOT_API_KEY}
                    await session.post(f"{WEB_BUILDER_URL}/api/security/ban", json=payload, headers=headers, timeout=5)
            except Exception as e:
                print(f"[Echo Security] Web unban API sync failed: {e}")

            if unbanned:
                embed = discord.Embed(
                    title="🌐 Website Access Restored",
                    description=f"Successfully unbanned <@{target_id}> (`{target_id}`) from accessing the web builder portal.",
                    color=0x2ECC71
                )
            else:
                embed = discord.Embed(
                    title="⚠️ User Not Banned",
                    description=f"User ID `{target_id}` was not found in the website ban list.",
                    color=0xF1C40F
                )
            embed.set_footer(text="Echo Studio — Web Moderation")
            await interaction.response.send_message(embed=embed)

        elif location.value == "server":
            if not interaction.guild:
                await interaction.response.send_message("❌ This command must be executed within a Discord server.", ephemeral=True)
                return

            try:
                numeric_id = int(target_id)
                user = await self.bot.fetch_user(numeric_id)
                await interaction.guild.unban(user, reason=reason)

                embed = discord.Embed(
                    title="✅ Server User Unbanned",
                    description=f"Successfully unbanned <@{numeric_id}> (`{numeric_id}`) from the Discord server.\n**Reason:** {reason}",
                    color=0x2ECC71
                )
                embed.set_footer(text="Echo Studio — Server Moderation")
                await interaction.response.send_message(embed=embed)
            except ValueError:
                await interaction.response.send_message("❌ Invalid input. Please enter a valid numeric Discord User ID.", ephemeral=True)
            except discord.NotFound:
                await interaction.response.send_message(f"❌ User ID `{user_id}` is not banned in this server.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I do not have permission to unban members from Discord.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message(f"❌ Failed to unban user: {e}", ephemeral=True)

    # --- 6. /ban-list COMMAND ---
    @app_commands.command(name="ban-list", description="Display banned users for website portal or Discord server.")
    @app_commands.describe(location="Target platform ban list to view (Website or Discord)")
    @app_commands.choices(location=[
        app_commands.Choice(name="Website", value="website"),
        app_commands.Choice(name="Discord", value="discord")
    ])
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban_list_command(
        self,
        interaction: discord.Interaction,
        location: app_commands.Choice[str]
    ):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        async def send_reply(embed=None, content=None, ephemeral=False):
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
                else:
                    await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
            except Exception as e:
                print(f"[Echo Error] Could not deliver ban-list response: {e}")

        if location.value == "website":
            banned_users = get_all_bans()

            if not banned_users:
                embed = discord.Embed(
                    title="🌐 Website Ban List",
                    description="No users are currently banned from the web portal.",
                    color=0x5865F2
                )
                embed.set_footer(text="Echo Studio — Web Moderation")
                await send_reply(embed=embed)
                return

            description_lines = []
            for entry in banned_users[:25]:
                uid = entry.get("discord_id", "Unknown")
                reason = entry.get("reason", "No reason specified")
                dev_msg = entry.get("dev_message", "")
                dev_line = f"\n  └ **Dev Note:** `{dev_msg}`" if dev_msg else ""
                description_lines.append(f"• <@{uid}> (`{uid}`)\n  └ **Reason:** {reason}{dev_line}")

            embed = discord.Embed(
                title=f"🌐 Website Ban List ({len(banned_users)} Total Banned)",
                description="\n".join(description_lines),
                color=0xE74C3C
            )
            embed.set_footer(text="Echo Studio — Web Moderation")
            await send_reply(embed=embed)

        elif location.value == "discord":
            if not interaction.guild:
                await send_reply(content="❌ This command must be executed within a Discord server.", ephemeral=True)
                return

            try:
                ban_entries = [entry async for entry in interaction.guild.bans(limit=100)]
                if not ban_entries:
                    embed = discord.Embed(
                        title=f"📋 Server Ban List — {interaction.guild.name}",
                        description="No users are currently banned from this server.",
                        color=0x5865F2
                    )
                    embed.set_footer(text="Echo Studio — Server Moderation")
                    await send_reply(embed=embed)
                    return

                description_lines = []
                for entry in ban_entries[:25]:
                    reason = entry.reason if entry.reason else "No reason specified"
                    user_tag = f"{entry.user.name}" if hasattr(entry.user, 'name') else str(entry.user)
                    description_lines.append(
                        f"• **{user_tag}** (<@{entry.user.id}> | `{entry.user.id}`)\n  └ **Reason:** {reason}"
                    )

                embed = discord.Embed(
                    title=f"📋 Server Ban List — {interaction.guild.name} ({len(ban_entries)} Total Banned)",
                    description="\n".join(description_lines),
                    color=0xE74C3C
                )
                embed.set_footer(text="Echo Studio — Server Moderation")
                await send_reply(embed=embed)
            except discord.Forbidden:
                await send_reply(content="❌ I do not have permission to view server bans.", ephemeral=True)
            except Exception as e:
                await send_reply(content=f"❌ Failed to fetch server ban list: {e}", ephemeral=True)

    # --- 7. /un-restrict COMMAND ---
    @app_commands.command(name="un-restrict", description="Remove a 12-hour spam timeout from a user.")
    @app_commands.describe(
        member="The member to un-restrict",
        reason="Reason for removing the timeout"
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    async def un_restrict_command(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "False positive spam flag"
    ):
        try:
            await member.timeout(None, reason=reason)
            self.user_message_logs[member.id] = []

            embed = discord.Embed(
                title="✅ Restriction Removed",
                description=f"Removed timeout restriction from {member.mention}.\n**Reason:** {reason}",
                color=0x2ECC71
            )
            embed.set_footer(text="Echo Studio — Server Moderation")
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I do not have permission to modify timeouts for this member.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to un-restrict member: {e}", ephemeral=True)

    # --- 8. /manage-access COMMAND ---
    @app_commands.command(name="manage-access", description="Grant or revoke build command access for a specific User ID.")
    @app_commands.describe(user_id="The Discord User ID to toggle access for")
    @is_owner()
    async def manage_access(self, interaction: discord.Interaction, user_id: str):
        try:
            target_id = int(user_id.strip())
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Input",
                description="Please enter a valid numeric Discord User ID.",
                color=0xE74C3C
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if target_id == AUTHORIZED_USER_ID:
            embed = discord.Embed(
                title="⚠️ Permanent Owner",
                description="You cannot revoke permissions from the primary hardcoded owner.",
                color=0xF1C40F
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if target_id in ALLOWED_BUILDERS:
            ALLOWED_BUILDERS.remove(target_id)
            embed = discord.Embed(
                title="🚫 Access Revoked",
                description=f"Revoked `/build` permissions from User ID: `{target_id}`",
                color=0xE74C3C
            )
        else:
            ALLOWED_BUILDERS.add(target_id)
            embed = discord.Embed(
                title="✅ Access Granted",
                description=f"Granted `/build` permissions to User ID: `{target_id}`",
                color=0x2ECC71
            )

        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 9. /build COMMAND ---
    @app_commands.command(name="build", description="Builds server layout from blueprint URL or uploaded JSON file.")
    @app_commands.describe(
        file="Direct file link or HTTP URL to blueprint JSON",
        attachment="Optional JSON blueprint file attachment"
    )
    @can_build()
    async def build(
        self, 
        interaction: discord.Interaction, 
        file: str = None, 
        attachment: discord.Attachment = None
    ):
        await interaction.response.send_message("Building Server...")

        blueprint = None
        source_identifier = "Unknown"

        if attachment:
            if not attachment.filename.endswith(".json"):
                embed = discord.Embed(
                    title="❌ Invalid File Format",
                    description="Please attach a valid `.json` blueprint file.",
                    color=0xE74C3C
                )
                embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
                await interaction.followup.send(embed=embed)
                return
            
            try:
                content = await attachment.read()
                blueprint = json.loads(content.decode("utf-8"))
                source_identifier = attachment.filename
            except Exception as e:
                embed = discord.Embed(
                    title="❌ File Parse Error",
                    description=f"Failed to read attached JSON file: `{e}`",
                    color=0xE74C3C
                )
                embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
                await interaction.followup.send(embed=embed)
                return

        elif file:
            clean_file = file.strip()
            source_identifier = clean_file

            if clean_file.startswith("http://") or clean_file.startswith("https://"):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(clean_file, timeout=10) as resp:
                            if resp.status == 200:
                                blueprint = await resp.json()
                            else:
                                embed = discord.Embed(
                                    title="❌ Download Error",
                                    description=f"Received status code `{resp.status}` when fetching URL.",
                                    color=0xE74C3C
                                )
                                embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
                                await interaction.followup.send(embed=embed)
                                return
                except Exception as e:
                    embed = discord.Embed(
                        title="❌ Network Request Failed",
                        description=f"Could not download blueprint from link: `{e}`",
                        color=0xE74C3C
                    )
                    embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
                    await interaction.followup.send(embed=embed)
                    return
            else:
                blueprint = get_blueprint_data(clean_file)

        else:
            embed = discord.Embed(
                title="❌ Missing Blueprint Input",
                description="Please provide a file URL in the `file:` parameter or attach a `.json` file.",
                color=0xE74C3C
            )
            embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        if not blueprint:
            embed = discord.Embed(
                title="❌ Blueprint Not Found",
                description=f"Could not locate or load blueprint data from `{source_identifier}`.",
                color=0xE74C3C
            )
            embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        target_guild_id = str(blueprint.get("target_guild_id", "")).strip()
        current_guild_id = str(interaction.guild.id)

        if target_guild_id and target_guild_id != current_guild_id:
            embed = discord.Embed(
                title="⛔ Build Denied — Server ID Mismatch",
                description=(
                    f"Blueprint was generated for Server ID `{target_guild_id}`, "
                    f"but you are executing it in Server ID `{current_guild_id}`."
                ),
                color=0xE74C3C
            )
            embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
            await interaction.followup.send(embed=embed)
            return

        guild = interaction.guild
        current_channel = interaction.channel

        for channel in guild.channels:
            if channel.id != current_channel.id:
                try:
                    await channel.delete()
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

        for role in guild.roles:
            if role.name != "@everyone" and not role.managed and role < guild.me.top_role:
                try:
                    await role.delete()
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

        roles_created = 0
        channels_created = 0

        for role_name in blueprint.get("roles", []):
            if role_name.lower() in ["everyone", "@everyone"]:
                continue
            try:
                await guild.create_role(name=role_name, mentionable=True)
                roles_created += 1
                await asyncio.sleep(0.4)
            except Exception:
                pass

        for cat_data in blueprint.get("categories", []):
            try:
                category = await guild.create_category(name=cat_data.get("name", "CATEGORY"))
                await asyncio.sleep(0.4)
            except Exception:
                category = None

            for ch_data in cat_data.get("channels", []):
                emoji = ch_data.get("emoji", "").strip()
                ch_name = ch_data.get("name", "channel").strip()
                full_name = f"{emoji} | {ch_name}".strip() if emoji else ch_name
                
                ch_type = ch_data.get("type", "text")
                topic = ch_data.get("topic", "")

                try:
                    if ch_type == "voice":
                        await guild.create_voice_channel(name=full_name, category=category)
                    else:
                        await guild.create_text_channel(
                            name=full_name, 
                            category=category, 
                            topic=topic, 
                            news=(ch_type == "announcement")
                        )
                    channels_created += 1
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

        try:
            await current_channel.delete()
        except Exception:
            pass

        target_channel = guild.text_channels[0] if guild.text_channels else None
        if target_channel:
            embed = discord.Embed(
                title="🚀 Server Build Complete",
                description=f"Successfully deployed layout **`{blueprint.get('server_name', 'Discord Server')}`** onto **{guild.name}**.",
                color=0x2ECC71
            )
            embed.add_field(name="🔑 Source", value=f"`{source_identifier[:40]}`", inline=True)
            embed.add_field(name="• Roles Created", value=f"`{roles_created}`", inline=True)
            embed.add_field(name="• Channels Created", value=f"`{channels_created}`", inline=True)
            embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
            await target_channel.send(embed=embed)

    # --- 10. /lockdown COMMAND ---
    @app_commands.command(name="lockdown", description="Toggles web portal maintenance screen.")
    @is_owner()
    @app_commands.choices(state=[
        app_commands.Choice(name="ON (Enable Maintenance)", value="on"),
        app_commands.Choice(name="OFF (Disable Maintenance)", value="off")
    ])
    async def lockdown(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        global is_lockdown
        is_lockdown = (state.value == "on")
        
        await update_website_lockdown_status(is_lockdown)

        status_str = "ENABLED" if is_lockdown else "DISABLED"
        embed = discord.Embed(
            title="🔒 Web Portal Maintenance Screen",
            description=f"Maintenance mode is now **{status_str}** across bot and website.",
            color=0xE74C3C if is_lockdown else 0x2ECC71
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 11. /status COMMAND ---
    @app_commands.command(name="status", description="Displays real-time bot latency and operational statistics.")
    @is_owner()
    async def status(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        uptime = round(time.time() - start_time)
        embed = discord.Embed(title="⚡ System Operational Status", color=0x5865F2)
        embed.add_field(name="Latency", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="Uptime", value=f"`{uptime} s`", inline=True)
        embed.add_field(name="Maintenance Lock", value="`ACTIVE`" if is_lockdown else "`INACTIVE`", inline=True)
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 12. /nuke COMMAND ---
    @app_commands.command(name="nuke", description="Deletes all channels/categories except the command channel.")
    @is_owner()
    async def nuke(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        guild = interaction.guild
        current_channel = interaction.channel

        deleted_count = 0
        for channel in guild.channels:
            if channel.id != current_channel.id:
                try:
                    await channel.delete()
                    deleted_count += 1
                    await asyncio.sleep(0.3)
                except Exception:
                    pass

        embed = discord.Embed(
            title="💥 Server Nuked",
            description=f"Purged **{deleted_count}** channels and categories. Preserved this execution channel.",
            color=0xE74C3C
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OrcaCog(bot))
