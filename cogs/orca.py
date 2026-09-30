import asyncio
import collections
import datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from pymongo import MongoClient
from pymongo.database import Database

# Import blueprint retrieval helper and dynamic web URL from ai_brain.py
from ai_brain import BOT_API_KEY, WEB_BUILDER_URL, get_blueprint_data

# Configuration & Constants
AUTHORIZED_USER_ID: int = 1219266886143967245
ALLOWED_BUILDERS: set[int] = {AUTHORIZED_USER_ID}  # Dynamic set of allowed user IDs
is_lockdown: bool = False
start_time: float = time.time()

SYSTEM_LOG_WEBHOOK_URL: str = os.environ.get(
    "SYSTEM_LOG_WEBHOOK_URL", os.environ.get("WEBHOOK_LOG_URL", "")
)
RECAPTCHA_SECRET_KEY: str = os.environ.get(
    "RECAPTCHA_SECRET_KEY", "YOUR_RECAPTCHA_SECRET_KEY"
)

# Spam Detection & Rate Limit Constraints
SPAM_THRESHOLD: int = 5  # Max messages
TIME_WINDOW: int = 5  # Time window in seconds

DESIGN_LIMIT_MAX: int = 3
DESIGN_LIMIT_WINDOW: int = 600  # 10 minutes
DESIGN_BAN_DURATION: int = 43200  # 12 hours

# ---------------------------------------------------------------------------
# DATABASE INITIALIZATION
# ---------------------------------------------------------------------------
MONGO_URI: Optional[str] = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
mongo_client: Optional[MongoClient] = MongoClient(MONGO_URI) if MONGO_URI else None

db: Optional[Database] = mongo_client["bot_database"] if mongo_client is not None else None
bans_collection = db["website_bans"] if db is not None else None
designs_collection = db["designs"] if db is not None else None
telemetry_collection = db["telemetry"] if db is not None else None
system_status_collection = db["system_status"] if db is not None else None


# ---------------------------------------------------------------------------
# HELPER & DATABASE UTILITIES
# ---------------------------------------------------------------------------
def is_user_banned(discord_id: str) -> bool:
    """Check if a user ID is banned in MongoDB (evaluating ban expiration). Owner is immune."""
    str_id = str(discord_id).strip()
    if str_id == str(AUTHORIZED_USER_ID) or bans_collection is None:
        return False

    try:
        user = bans_collection.find_one({"$or": [{"discord_id": str_id}, {"user_id": str_id}]})
        if not user:
            return False

        ban_expires_at = user.get("expires_at", 0)
        if ban_expires_at == 0 or time.time() < ban_expires_at:
            return True

        unban_user(discord_id)
        return False
    except Exception as e:
        print(f"[Database Error] Failed to check ban status: {e}")
        return False


def ban_user(
    discord_id: str,
    reason: str = "No reason provided",
    dev_message: str = "",
    duration_seconds: int = 0,
) -> bool:
    """Ban a user ID in MongoDB with an optional expiration window."""
    str_id = str(discord_id).strip()
    if str_id == str(AUTHORIZED_USER_ID) or bans_collection is None:
        return False

    now = time.time()
    expires_at = now + duration_seconds if duration_seconds > 0 else 0

    try:
        result = bans_collection.update_one(
            {"$or": [{"discord_id": str_id}, {"user_id": str_id}]},
            {
                "$set": {
                    "discord_id": str_id,
                    "user_id": str_id,
                    "reason": reason,
                    "dev_message": dev_message,
                    "updated_at": now,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
        )
        return result.upserted_id is not None or result.modified_count > 0
    except Exception as e:
        print(f"[Database Error] Failed to ban user {str_id}: {e}")
        return False


def unban_user(discord_id: str) -> bool:
    """Remove ban records for a target user ID from MongoDB."""
    if bans_collection is None:
        return False
    str_id = str(discord_id).strip()
    try:
        result = bans_collection.delete_many(
            {"$or": [{"discord_id": str_id}, {"user_id": str_id}]}
        )
        return result.deleted_count > 0
    except Exception as e:
        print(f"[Database Error] Failed to unban user {str_id}: {e}")
        return False


def get_all_bans() -> List[Dict[str, Any]]:
    """Fetch active banned user entries from MongoDB."""
    if bans_collection is None:
        return []
    now = time.time()
    active_bans = []
    try:
        cursor = bans_collection.find(
            {},
            {
                "_id": 0,
                "discord_id": 1,
                "user_id": 1,
                "reason": 1,
                "dev_message": 1,
                "expires_at": 1,
            },
        )
        for ban in cursor:
            uid = ban.get("discord_id") or ban.get("user_id")
            if str(uid) == str(AUTHORIZED_USER_ID):
                unban_user(str(uid))
                continue

            expires_at = ban.get("expires_at", 0)
            if expires_at == 0 or now < expires_at:
                active_bans.append(ban)
            else:
                unban_user(str(uid))
    except Exception as e:
        print(f"[Database Error] Failed to fetch active bans: {e}")
    return active_bans


def get_user_ban_details(discord_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full ban record for a specific user ID if active."""
    str_id = str(discord_id).strip()
    if str_id == str(AUTHORIZED_USER_ID) or bans_collection is None:
        return None

    try:
        ban = bans_collection.find_one(
            {"$or": [{"discord_id": str_id}, {"user_id": str_id}]}, {"_id": 0}
        )
        if not ban:
            return None
        expires_at = ban.get("expires_at", 0)
        if expires_at != 0 and time.time() >= expires_at:
            unban_user(discord_id)
            return None
        return ban
    except Exception as e:
        print(f"[Database Error] Failed to get ban details for {str_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# ACCESS CONTROL CHECKS
# ---------------------------------------------------------------------------
def is_owner():
    """Restrict execution strictly to primary AUTHORIZED_USER_ID."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == AUTHORIZED_USER_ID:
            return True
        embed = discord.Embed(
            title="403 Access Denied",
            description="```\nAccess Denied: You do not have permission to execute this administrative command.\n```",
            color=0xE74C3C,
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    return app_commands.check(predicate)


def can_build():
    """Allow primary AUTHORIZED_USER_ID and allowed builders to run build operations."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id in ALLOWED_BUILDERS or interaction.user.id == AUTHORIZED_USER_ID:
            return True
        embed = discord.Embed(
            title="403 Access Denied",
            description="```\nAccess Denied: You do not have permission to execute the /build command.\n```",
            color=0xE74C3C,
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    return app_commands.check(predicate)


async def update_website_lockdown_status(enable: bool) -> bool:
    """Synchronize lockdown status across database and external REST endpoint."""
    if system_status_collection is not None:
        try:
            system_status_collection.update_one(
                {"_id": "global_status"},
                {"$set": {"is_lockdown": enable, "updated_at": time.time()}},
                upsert=True,
            )
        except Exception as e:
            print(f"[Database Error] Failed to persist status update: {e}")

    try:
        async with aiohttp.ClientSession() as session:
            payload = {"enable": enable}
            headers = {"X-Bot-Auth": BOT_API_KEY}
            async with session.post(
                f"{WEB_BUILDER_URL}/api/security/lockdown",
                json=payload,
                headers=headers,
                timeout=5,
            ) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"[Echo API] Failed to update website lockdown state: {e}")
        return False


async def send_system_webhook_log(
    content: Optional[str] = None, embed: Optional[discord.Embed] = None
) -> None:
    """Dispatch alert payloads to the configured webhook log channel."""
    if not SYSTEM_LOG_WEBHOOK_URL:
        return
    try:
        async with aiohttp.ClientSession() as session:
            payload: Dict[str, Any] = {}
            if content:
                payload["content"] = content
            if embed:
                payload["embeds"] = [embed.to_dict()]
            async with session.post(SYSTEM_LOG_WEBHOOK_URL, json=payload, timeout=5):
                pass
    except Exception as e:
        print(f"[Webhook Log Error] {e}")


# ---------------------------------------------------------------------------
# UI VIEWS
# ---------------------------------------------------------------------------
class BotJoinTosView(discord.ui.View):
    """Terms acknowledgment view dispatched when joining new guilds."""

    def __init__(self, inviter_id: int):
        super().__init__(timeout=None)
        self.inviter_id = inviter_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild
            and (interaction.user.id == self.inviter_id or interaction.user.id == interaction.guild.owner_id)
        ):
            return True
        await interaction.response.send_message(
            "❌ Only the server owner or the administrator who added Echo Studio can accept these terms.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="I Understand & Accept Terms",
        style=discord.ButtonStyle.danger,
        emoji="⚠️",
    )
    async def accept_terms(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        button.disabled = True
        button.label = "Terms Accepted"
        button.style = discord.ButtonStyle.success

        accepted_embed = discord.Embed(
            title="✅ Agreement Acknowledged",
            description=(
                f"Terms accepted by {interaction.user.mention}.\n"
                "Echo Studio is active and initialized for this server."
            ),
            color=0x2ECC71,
        )
        accepted_embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.edit_message(embed=accepted_embed, view=self)


# ---------------------------------------------------------------------------
# COG IMPLEMENTATION
# ---------------------------------------------------------------------------
class OrcaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_message_logs: Dict[int, List[float]] = collections.defaultdict(list)
        self.design_rate_limits: Dict[str, List[float]] = collections.defaultdict(list)

    # --- API & BACKEND HANDLERS ---
    async def verify_recaptcha(self, token: str) -> bool:
        """Validate Google reCAPTCHA token."""
        if not token or token == "YOUR_RECAPTCHA_SITE_KEY":
            return False

        try:
            async with aiohttp.ClientSession() as session:
                url = "https://www.google.com/recaptcha/api/siteverify"
                payload = {"secret": RECAPTCHA_SECRET_KEY, "response": token}
                async with session.post(url, data=payload, timeout=5) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("success", False)
        except Exception as e:
            print(f"[Recaptcha Error] Failed to verify token: {e}")
        return False

    async def handle_api_check_auth(self, user_id: str) -> Dict[str, Any]:
        """Process web auth verification state."""
        global is_lockdown
        uid = str(user_id).strip()

        if uid == str(AUTHORIZED_USER_ID):
            return {
                "authenticated": True,
                "user_id": uid,
                "is_owner": True,
                "is_banned": False,
                "is_lockdown": False,
            }

        if is_lockdown:
            return {"authenticated": True, "user_id": uid, "is_lockdown": True, "is_banned": False}

        ban_info = get_user_ban_details(uid)
        if ban_info:
            return {
                "authenticated": True,
                "user_id": uid,
                "is_banned": True,
                "is_lockdown": False,
                "ban_reason": ban_info.get("reason", "Account suspended."),
                "dev_message": ban_info.get("dev_message", ""),
            }

        return {"authenticated": True, "user_id": uid, "is_banned": False, "is_lockdown": False}

    async def check_user_oauth_guild_admin(
        self, user_id: int, guild_id: str, access_token: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Validate user administrative status in target guild via OAuth2."""
        if user_id == AUTHORIZED_USER_ID:
            return True, "Owner immunity granted."

        if not access_token:
            return False, "Missing Discord OAuth access token."

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {access_token}"}
                async with session.get(
                    "https://discord.com/api/v10/users/@me/guilds",
                    headers=headers,
                    timeout=5,
                ) as resp:
                    if resp.status != 200:
                        return False, "Could not verify user permissions with Discord API."

                    guilds = await resp.json()
                    target_guild = next((g for g in guilds if str(g.get("id")) == str(guild_id)), None)

                    if not target_guild:
                        return False, "You are not a member of the specified target server."

                    if target_guild.get("owner"):
                        return True, "Server Owner verified."

                    permissions = int(target_guild.get("permissions", 0))
                    # Check ADMINISTRATOR (0x8) or MANAGE_GUILD (0x20)
                    if (permissions & 0x8) == 0x8 or (permissions & 0x20) == 0x20:
                        return True, "User administrative permissions verified."

                    return (
                        False,
                        "You must be the Server Owner or have Admin/Manage Server permissions in the target server.",
                    )
        except Exception as e:
            return False, f"Error verifying user server access: {e}"

    async def handle_api_verify_server(
        self, payload: Dict[str, Any], user_id: int, access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Verify invite integrity, CAPTCHA, and permissions for web layout generation."""
        captcha_token = payload.get("captcha_token")
        if captcha_token and not await self.verify_recaptcha(captcha_token):
            return {"valid": False, "error": "Security CAPTCHA verification failed."}

        guild_id = payload.get("guild_id") or payload.get("server_id")
        server_link = payload.get("server_link") or payload.get("invite_url", "")

        user_authorized, auth_msg = await self.check_user_oauth_guild_admin(
            user_id, str(guild_id), access_token
        )
        if not user_authorized:
            return {"valid": False, "error": auth_msg}

        valid, msg = await self.verify_invite_matches_server(server_link, str(guild_id))
        return {"valid": valid, "message" if valid else "error": msg}

    async def handle_api_submit_design(self, payload: Dict[str, Any], user_id: int) -> Dict[str, Any]:
        """Persist design records to MongoDB and dispatch webhook metrics."""
        if designs_collection is not None:
            try:
                doc = {
                    "user_id": str(user_id),
                    "guild_id": payload.get("target_guild_id"),
                    "server_link": payload.get("server_link"),
                    "separator": payload.get("separator"),
                    "categories": payload.get("categories", []),
                    "roles": payload.get("roles", []),
                    "submitted_at": time.time(),
                }
                designs_collection.insert_one(doc)
            except Exception as e:
                print(f"[Database Error] Failed to insert design record: {e}")

        embed = discord.Embed(
            title="📐 New Server Design Submitted",
            description=f"User <@{user_id}> generated and submitted a new blueprint.",
            color=0x9333EA,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="Target Guild ID", value=str(payload.get("target_guild_id")), inline=True)
        embed.add_field(name="Categories", value=str(len(payload.get("categories", []))), inline=True)
        embed.add_field(name="Roles Configured", value=str(len(payload.get("roles", []))), inline=True)

        await send_system_webhook_log(embed=embed)
        return {"success": True}

    # --- DISCORD EVENT LISTENERS ---
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Send initialization instructions and TOS upon joining a guild."""
        if not guild.me.guild_permissions.administrator:
            return

        inviter_id = guild.owner_id
        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=5):
                if entry.target and entry.target.id == self.bot.user.id:
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
                "• **Automated Structure Deployment**: When `/build` is executed, existing channels and roles will be removed and rebuilt.\n"
                "• **Moderation & Security**: Active 12-hour automated anti-spam protection applies.\n\n"
                "### ⚖️ Terms of Service Disclaimer:\n"
                "**By confirming below, you acknowledge that the bot developers are NOT liable for any lost messages, deleted roles, or purged channels executed during operations.**"
            ),
            color=0xF1C40F,
        )
        tos_embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await target_channel.send(embed=tos_embed, view=BotJoinTosView(inviter_id=inviter_id))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Apply anti-spam timeouts to non-owners exceeding message thresholds."""
        if message.author.bot or not message.guild or message.author.id == AUTHORIZED_USER_ID:
            return

        user_id = message.author.id
        now = time.time()

        self.user_message_logs[user_id].append(now)
        self.user_message_logs[user_id] = [
            t for t in self.user_message_logs[user_id] if now - t <= TIME_WINDOW
        ]

        if len(self.user_message_logs[user_id]) >= SPAM_THRESHOLD:
            try:
                duration = datetime.timedelta(hours=12)
                if isinstance(message.author, discord.Member):
                    await message.author.timeout(
                        duration, reason="Automated Anti-Spam: Excessive message frequency."
                    )
                    self.user_message_logs[user_id] = []

                    embed = discord.Embed(
                        title="🚫 Member Restricted",
                        description=f"{message.author.mention} has been restricted for **12 hours** due to spamming.",
                        color=0xE74C3C,
                    )
                    embed.set_footer(text="Echo Studio — Anti-Spam Protection")
                    await message.channel.send(embed=embed)
            except discord.Forbidden:
                print(f"Failed to restrict {message.author}: Missing permissions.")
            except Exception as e:
                print(f"Error applying timeout: {e}")

    # --- SECURITY & RATE LIMITING ---
    async def verify_invite_matches_server(
        self, invite_url: str, server_id: str
    ) -> Tuple[bool, str]:
        code = invite_url.strip().split("/")[-1].split("?")[0]
        try:
            invite = await self.bot.fetch_invite(code)
            if not invite or not invite.guild:
                return False, "Invalid Invite: Could not retrieve server details."

            target_id_str = str(server_id).strip()
            if str(invite.guild.id) != target_id_str:
                return (
                    False,
                    f"Mismatch: Invite belongs to **{invite.guild.name}** (`{invite.guild.id}`), not Target Server ID `{target_id_str}`.",
                )

            if invite.max_age is not None and 0 < invite.max_age < 86400:
                return (
                    False,
                    f"Invalid Expiration: Invite must remain active for 24+ hours (Current: {invite.max_age // 3600}h).",
                )

            guild = self.bot.get_guild(invite.guild.id) or await self.bot.fetch_guild(invite.guild.id)
            if not guild:
                return False, f"Bot Missing: Echo Studio is not present in server **{invite.guild.name}**."

            if not guild.me or not guild.me.guild_permissions.administrator:
                return False, f"Missing Permissions: Echo Studio lacks Administrator rights in **{guild.name}**."

            return True, f"Verified target server **{guild.name}** (`{guild.id}`)."
        except discord.NotFound:
            return False, "Invalid Invite: The link does not exist or has expired."
        except Exception as e:
            return False, f"Verification Error: `{e}`"

    async def check_and_apply_design_ratelimit(self, user_id: str) -> Tuple[bool, str]:
        now = time.time()
        uid = str(user_id).strip()

        if uid == str(AUTHORIZED_USER_ID):
            return True, "OK"

        self.design_rate_limits[uid] = [
            t for t in self.design_rate_limits[uid] if now - t <= DESIGN_LIMIT_WINDOW
        ]
        self.design_rate_limits[uid].append(now)

        if len(self.design_rate_limits[uid]) > DESIGN_LIMIT_MAX:
            reason = f"Exceeded layout limit ({DESIGN_LIMIT_MAX} designs per 10m)."
            dev_msg = "Automated Anti-Abuse: Temporary 12-hour restriction applied."

            ban_user(uid, reason=reason, dev_message=dev_msg, duration_seconds=DESIGN_BAN_DURATION)

            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "user_id": uid,
                        "action": "ban",
                        "reason": reason,
                        "dev_message": dev_msg,
                        "duration_seconds": DESIGN_BAN_DURATION,
                    }
                    headers = {"X-Bot-Auth": BOT_API_KEY}
                    await session.post(
                        f"{WEB_BUILDER_URL}/api/security/ban",
                        json=payload,
                        headers=headers,
                        timeout=5,
                    )
            except Exception as e:
                print(f"[Echo RateLimit] Failed to sync web ban: {e}")

            embed = discord.Embed(
                title="🚨 Automated Web Ban — Rate Limit Exceeded",
                description=f"<@{AUTHORIZED_USER_ID}> **User restricted due to rate limiting.**",
                color=0xFF0000,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            embed.add_field(name="User ID", value=f"`{uid}` (<@{uid}>)", inline=True)
            embed.add_field(name="Action Taken", value="Banned from Web Portal for **12 Hours**", inline=False)
            embed.set_footer(text="Echo Studio — Security Enforcement")

            await send_system_webhook_log(content=f"<@{AUTHORIZED_USER_ID}>", embed=embed)
            return False, "Rate limit exceeded. Banned from portal for 12 hours."

        return True, "OK"

    async def log_web_entry_and_check_alt(
        self, user_id: int, ip_address: str = "N/A", user_agent: str = "N/A"
    ) -> Tuple[bool, str]:
        uid_str = str(user_id).strip()
        if uid_str == str(AUTHORIZED_USER_ID):
            return True, "OK"

        try:
            user = await self.bot.fetch_user(user_id)
        except Exception:
            user = None

        now = datetime.datetime.now(datetime.timezone.utc)
        is_alt = False
        account_age_days = 0

        if user:
            account_age_days = (now - user.created_at).days
            if account_age_days <= 30:
                is_alt = True

        if telemetry_collection is not None:
            try:
                telemetry_collection.insert_one(
                    {
                        "user_id": uid_str,
                        "ip_address": ip_address,
                        "user_agent": user_agent,
                        "is_alt": is_alt,
                        "account_age_days": account_age_days,
                        "logged_at": time.time(),
                    }
                )
            except Exception as e:
                print(f"[Database Error] Failed to log telemetry: {e}")

        embed = discord.Embed(
            title="🌐 Website Access Log" if not is_alt else "🚨 Alt Account Detected — Automated Ban",
            color=0x3498DB if not is_alt else 0xE74C3C,
            timestamp=now,
        )

        if user:
            badges = [flag.name.replace("_", " ").title() for flag, value in user.public_flags if value]
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="Username", value=f"**{user.name}** (`{user}`)", inline=True)
            embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
            embed.add_field(name="Account Age", value=f"`{account_age_days} days`", inline=True)
            embed.add_field(name="Badges", value=f"`{', '.join(badges) if badges else 'None'}`", inline=True)
        else:
            embed.add_field(name="User ID", value=f"`{user_id}` (Unknown User)", inline=False)

        embed.add_field(name="IP Address", value=f"`{ip_address}`", inline=True)
        embed.add_field(name="User Agent", value=f"```\n{user_agent[:250]}\n```", inline=False)
        embed.set_footer(text="Echo Studio — Web Security Tracker")

        await send_system_webhook_log(embed=embed)

        if is_alt:
            reason = f"Alt Account Detected: Account age is {account_age_days} days (30 required)."
            dev_msg = "Automated Shield: Accounts under 30 days old are restricted."
            ban_user(uid_str, reason=reason, dev_message=dev_msg, duration_seconds=0)

            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "user_id": uid_str,
                        "action": "ban",
                        "reason": reason,
                        "dev_message": dev_msg,
                    }
                    headers = {"X-Bot-Auth": BOT_API_KEY}
                    await session.post(
                        f"{WEB_BUILDER_URL}/api/security/ban",
                        json=payload,
                        headers=headers,
                        timeout=5,
                    )
            except Exception as e:
                print(f"[Echo Alt Shield] Failed to sync web ban: {e}")

            return False, reason

        return True, "OK"

    # --- SLASH COMMANDS ---
    @app_commands.command(
        name="help",
        description="Learn how to create and deploy a custom Discord server.",
    )
    async def help_command(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🛠️ How to Create & Deploy a Custom Discord Server",
            description=(
                "Echo Studio lets you automatically generate and deploy complete server layouts!\n\n"
                f"1️⃣ **Design on Web Builder**: Visit the [Echo Web Builder]({WEB_BUILDER_URL}).\n"
                "2️⃣ **Generate Layout**: Preview your AI-generated layout.\n"
                "3️⃣ **Submit Design**: Get your blueprint file link.\n"
                "4️⃣ **Deploy via `/build`**: Run `/build` attaching your blueprint JSON."
            ),
            color=0x5865F2,
        )
        embed.add_field(
            name="🌐 Web Builder Link",
            value=f"[Launch Web Builder]({WEB_BUILDER_URL})",
            inline=False,
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="website", description="Provides the link to the web layout builder."
    )
    async def website(self, interaction: discord.Interaction) -> None:
        global is_lockdown
        if is_lockdown and interaction.user.id != AUTHORIZED_USER_ID:
            embed = discord.Embed(
                title="530 Site Under Maintenance",
                description="```\nHTTP 530: Portal is undergoing scheduled maintenance.\n```",
                color=0xF1C40F,
            )
            embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠️ Interactive Server Builder",
            description=f"Launch the Web Builder:\n\n🔗 **[Echo Web Builder Portal]({WEB_BUILDER_URL})**",
            color=0x5865F2,
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="server-info",
        description="Displays formatted Server ID and Invite Link.",
    )
    @app_commands.describe(
        server_id="Target Discord Server ID", invite_link="Invite URL to target server"
    )
    async def server_info(
        self,
        interaction: discord.Interaction,
        server_id: Optional[str] = None,
        invite_link: Optional[str] = None,
    ) -> None:
        target_id = (
            server_id.strip()
            if server_id
            else (str(interaction.guild.id) if interaction.guild else "N/A")
        )
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

        guild_name = interaction.guild.name if interaction.guild else "Server Details"
        embed = discord.Embed(
            title=f"📌 {guild_name} — Information", color=0x5865F2
        )
        embed.add_field(name="🆔 Server ID", value=f"`{target_id}`", inline=False)
        embed.add_field(
            name="🔗 Invite Link", value=f"`{formatted_invite or 'N/A'}`", inline=False
        )
        embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ban", description="Ban a user from Website, Discord, or Both.")
    @app_commands.describe(
        user="Target user to ban",
        location="Ban target location",
        dev_message="Developer note shown to user",
        reason="Internal reason",
    )
    @app_commands.choices(
        location=[
            app_commands.Choice(name="Website Only", value="website"),
            app_commands.Choice(name="Discord Server Only", value="discord"),
            app_commands.Choice(name="Both Website & Discord", value="both"),
        ]
    )
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban_command(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        location: app_commands.Choice[str],
        dev_message: str = "Violating platform rules.",
        reason: str = "No internal reason specified.",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target_id = str(user.id).strip()

        if target_id == str(AUTHORIZED_USER_ID):
            await interaction.followup.send(
                "❌ Action forbidden: Primary owner is immune.", ephemeral=True
            )
            return

        loc = location.value
        report: List[str] = []

        if loc in ("website", "both"):
            ban_user(target_id, reason=reason, dev_message=dev_message, duration_seconds=0)
            try:
                async with aiohttp.ClientSession() as session:
                    payload = {
                        "user_id": target_id,
                        "action": "ban",
                        "reason": reason,
                        "dev_message": dev_message,
                    }
                    headers = {"X-Bot-Auth": BOT_API_KEY}
                    async with session.post(
                        f"{WEB_BUILDER_URL}/api/security/ban",
                        json=payload,
                        headers=headers,
                        timeout=5,
                    ) as resp:
                        if resp.status == 200:
                            report.append("✅ **Website Ban:** Applied.")
                        else:
                            report.append(f"⚠️ **Website API Warning:** HTTP {resp.status}")
            except Exception as e:
                report.append(f"⚠️ **Website Sync Failed:** {e}")

        if loc in ("discord", "both"):
            if interaction.guild:
                try:
                    await interaction.guild.ban(
                        user, reason=f"{reason} | Dev Note: {dev_message}"
                    )
                    report.append("✅ **Discord Ban:** Applied.")
                except Exception as e:
                    report.append(f"❌ **Discord Ban Failed:** {e}")
            else:
                report.append("❌ **Discord Ban Error:** Execution must occur inside a server.")

        embed = discord.Embed(
            title="🔨 Ban Summary", description="\n".join(report), color=0xE74C3C
        )
        embed.add_field(name="Target", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Location", value=f"`{location.name}`", inline=True)
        embed.add_field(name="Dev Message", value=f"```{dev_message}
