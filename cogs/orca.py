import datetime
import json
import os
import time
from typing import Any, Dict, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ai_brain import WEB_BUILDER_URL, get_blueprint_data

AUTHORIZED_USER_ID: int = 1219266886143967245
ALLOWED_BUILDERS: set[int] = {AUTHORIZED_USER_ID}
START_TIME: float = time.time()

SYSTEM_LOG_WEBHOOK_URL: str = os.environ.get(
    "SYSTEM_LOG_WEBHOOK_URL", os.environ.get("WEBHOOK_LOG_URL", "")
)


def is_owner():
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


async def send_system_webhook_log(
    content: Optional[str] = None, embed: Optional[discord.Embed] = None
) -> None:
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


class BotJoinTosView(discord.ui.View):
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


class OrcaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def check_user_oauth_guild_admin(
        self, user_id: int, guild_id: str, access_token: Optional[str] = None
    ) -> Tuple[bool, str]:
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
                    if (permissions & 0x8) == 0x8 or (permissions & 0x20) == 0x20:
                        return True, "User administrative permissions verified."

                    return (
                        False,
                        "You must be the Server Owner or have Admin/Manage Server permissions in the target server.",
                    )
        except Exception as e:
            return False, f"Error verifying user server access: {e}"

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

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
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
                "• **Automated Structure Deployment**: When `/build` is executed, existing channels and roles will be created based on your template.\n\n"
                "### ⚖️️ Terms of Service Disclaimer:\n"
                "**By confirming below, you acknowledge that the bot developers are NOT liable for any issues arising during channel/role deployment.**"
            ),
            color=0xF1C40F,
        )
        tos_embed.set_footer(text="Echo Studio — Automated Server Infrastructure")
        await target_channel.send(embed=tos_embed, view=BotJoinTosView(inviter_id=inviter_id))

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

    @app_commands.command(
        name="build",
        description="Deploy a layout blueprint JSON to the current server.",
    )
    @app_commands.describe(
        file="Blueprint JSON attachment or URL to apply",
        guild_id="Target Guild ID to fetch layout for (optional)",
    )
    @can_build()
    async def build(
        self,
        interaction: discord.Interaction,
        file: Optional[discord.Attachment] = None,
        guild_id: Optional[str] = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be executed within a Discord server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        blueprint: Optional[Dict[str, Any]] = None

        if file:
            if not file.filename.endswith(".json"):
                await interaction.followup.send("❌ Attached file must be a `.json` file.")
                return
            try:
                content = await file.read()
                blueprint = json.loads(content.decode("utf-8"))
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to parse JSON attachment: `{e}`")
                return
        elif guild_id:
            blueprint = get_blueprint_data(guild_id.strip())
            if not blueprint:
                await interaction.followup.send(
                    f"❌ No blueprint found for Guild ID `{guild_id.strip()}`."
                )
                return
        else:
            blueprint = get_blueprint_data(str(interaction.guild.id))
            if not blueprint:
                await interaction.followup.send(
                    "❌ No blueprint attached and none found for this server. Please attach a `.json` blueprint or specify a `guild_id`."
                )
                return

        guild = interaction.guild
        sep = blueprint.get("separator", "|").strip() or "|"

        try:
            # Rebuild Roles
            roles = blueprint.get("roles", [])
            existing_role_names = [r.name for r in guild.roles]
            for role_name in roles:
                if role_name not in existing_role_names:
                    await guild.create_role(name=role_name)

            # Rebuild Categories & Channels
            categories = blueprint.get("categories", [])
            for cat_data in categories:
                cat_name = cat_data.get("name", "UNNAMED CATEGORY")
                category = await guild.create_category(cat_name)

                for ch_data in cat_data.get("channels", []):
                    ch_name = ch_data.get("name", "channel").strip()
                    ch_type = ch_data.get("type", "text")
                    ch_topic = ch_data.get("topic", "")
                    emoji = ch_data.get("emoji", "").strip()

                    # Format strictly with middle separator: Emoji | Channel Name
                    full_name = ch_data.get("formatted_name")
                    if not full_name:
                        if emoji and sep and ch_name:
                            full_name = f"{emoji} {sep} {ch_name}"
                        elif emoji and ch_name:
                            full_name = f"{emoji} {ch_name}"
                        else:
                            full_name = ch_name or emoji or "channel"

                    if ch_type == "voice":
                        await guild.create_voice_channel(full_name, category=category)
                    elif ch_type == "announcement":
                        await guild.create_text_channel(
                            full_name, category=category, topic=ch_topic, news=True
                        )
                    else:
                        await guild.create_text_channel(
                            full_name, category=category, topic=ch_topic
                        )

            embed = discord.Embed(
                title="🚀 Infrastructure Deployment Complete",
                description=f"Successfully deployed blueprint **{blueprint.get('server_name', guild.name)}**.",
                color=0x2ECC71,
            )
            embed.add_field(name="Categories Created", value=str(len(categories)), inline=True)
            embed.add_field(name="Roles Configured", value=str(len(roles)), inline=True)
            embed.set_footer(text="Echo Studio — Automated Server Infrastructure")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"❌ Deployment failed midway due to an error: `{e}`"
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OrcaCog(bot))
