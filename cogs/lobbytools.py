import random
import re
import uuid
import asyncio
import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> Optional[int]:
    """Parses '30s', '10m', '2h', '1d' into seconds. Returns None if invalid."""
    match = DURATION_RE.match(text)
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * UNIT_SECONDS[unit.lower()]


def make_bar(votes: int, total: int, length: int = 12) -> str:
    if total == 0:
        return "░" * length
    filled = round((votes / total) * length)
    return "█" * filled + "░" * (length - filled)


class Poll:
    def __init__(
        self,
        poll_id: str,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        question: str,
        options: list[str],
        end_at: datetime.datetime,
        poll_type: str,
        auto_action: bool,
    ):
        self.id = poll_id
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.creator_id = creator_id
        self.question = question
        self.options = options
        self.votes: dict[int, set[int]] = {i: set() for i in range(len(options))}
        self.end_at = end_at
        self.poll_type = poll_type
        self.auto_action = auto_action
        self.message_id: Optional[int] = None
        self.resolved = False

    def total_votes(self) -> int:
        return sum(len(v) for v in self.votes.values())

    def cast_vote(self, user_id: int, option_index: int):
        # Single-choice poll: clear any previous vote first
        for voters in self.votes.values():
            voters.discard(user_id)
        self.votes[option_index].add(user_id)

    def build_embed(self, resolved: bool = False, winners: Optional[list[int]] = None) -> discord.Embed:
        total = self.total_votes()
        color = discord.Color.green() if resolved else discord.Color.blurple()
        title_prefix = "📊 Poll Ended" if resolved else "📊 Poll Open"
        embed = discord.Embed(title=f"{title_prefix}: {self.question}", color=color)

        for idx, option in enumerate(self.options):
            count = len(self.votes[idx])
            bar = make_bar(count, total)
            pct = f"{(count / total * 100):.0f}%" if total else "0%"
            marker = "🏆 " if resolved and winners and idx in winners else ""
            embed.add_field(
                name=f"{marker}{idx + 1}. {option}",
                value=f"`{bar}` {count} vote(s) • {pct}",
                inline=False,
            )

        if resolved:
            names = ", ".join(self.options[i] for i in winners) if winners else "No votes cast"
            embed.add_field(name="Result", value=f"🏆 **{names}**", inline=False)
            embed.set_footer(text=f"LobbyBot • Poll closed • {total} total vote(s)")
        else:
            unix_ts = int(self.end_at.timestamp())
            embed.description = f"Vote below • Ends <t:{unix_ts}:R>"
            embed.set_footer(text=f"LobbyBot • {total} total vote(s) so far")

        return embed


# ------------------------------------------------------------------
# Voting UI
# ------------------------------------------------------------------

class PollOptionSelect(discord.ui.Select):
    """One dropdown handles up to 25 options. Polls with more options get multiple selects."""

    def __init__(self, poll: Poll, cog: "LobbyTools", start_index: int, options_slice: list[str]):
        self.poll = poll
        self.cog = cog
        self.start_index = start_index

        select_options = [
            discord.SelectOption(label=opt[:100], value=str(start_index + i))
            for i, opt in enumerate(options_slice)
        ]
        placeholder = f"Vote (options {start_index + 1}-{start_index + len(options_slice)})"
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=select_options,
            custom_id=f"poll:{poll.id}:{start_index}",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.poll.resolved:
            return await interaction.response.send_message("⚠️ This poll has already ended.", ephemeral=True)

        option_index = int(self.values[0])
        self.poll.cast_vote(interaction.user.id, option_index)

        embed = self.poll.build_embed()
        await interaction.response.edit_message(embed=embed, view=self.view)


class EndPollButton(discord.ui.Button):
    def __init__(self, poll: Poll, cog: "LobbyTools"):
        super().__init__(label="End Poll Now", style=discord.ButtonStyle.danger, custom_id=f"pollend:{poll.id}")
        self.poll = poll
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        is_creator = interaction.user.id == self.poll.creator_id
        has_perms = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild
        if not (is_creator or has_perms):
            return await interaction.response.send_message(
                "❌ Only the poll creator or a moderator can end this poll early.", ephemeral=True
            )
        await interaction.response.send_message("⏹️ Ending poll now...", ephemeral=True)
        await self.cog.resolve_poll(self.poll)


class PollView(discord.ui.View):
    def __init__(self, poll: Poll, cog: "LobbyTools"):
        super().__init__(timeout=None)
        for start in range(0, len(poll.options), 25):
            chunk = poll.options[start:start + 25]
            self.add_item(PollOptionSelect(poll, cog, start, chunk))
        self.add_item(EndPollButton(poll, cog))


# ------------------------------------------------------------------
# Cog
# ------------------------------------------------------------------

class LobbyTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_polls: dict[str, Poll] = {}
        self.check_polls.start()

    def cog_unload(self):
        self.check_polls.cancel()

    @commands.hybrid_command(
        name="randomize",
        aliases=["rdm"],
        description="Shuffles and lists users in your current VC in a random order (up to 100 users)."
    )
    async def randomize_users(self, ctx: commands.Context):
        """Prefix command: !rdm | Slash command: /randomize"""
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("❌ **Error:** You must be inside a voice channel to randomize users!")

        members = [m for m in ctx.author.voice.channel.members if not m.bot]
        if not members:
            return await ctx.send("❌ **Error:** No human users found in your voice channel to randomize!")

        if len(members) > 100:
            members = members[:100]

        random.shuffle(members)

        embed = discord.Embed(
            title="🎲 Randomized User Order",
            description=f"Shuffled **{len(members)}** users from **{ctx.author.voice.channel.name}**!",
            color=discord.Color.gold()
        )

        list_text = ""
        for idx, member in enumerate(members, 1):
            list_text += f"`{idx:02d}.` {member.mention} ({member.display_name})\n"

        embed.add_field(name="📋 Shuffled List (Pick/Draft Order)", value=list_text, inline=False)
        embed.set_footer(text="LobbyBot • Turn/Pick Order Settled!")

        await ctx.send(embed=embed)

    # ------------------------------------------------------------
    # /poll
    # ------------------------------------------------------------

    @commands.hybrid_command(
        name="poll",
        description="Start a poll with unlimited options. Ends automatically after the duration you set."
    )
    @app_commands.describe(
        question="What is this poll about?",
        options="Poll choices separated by | (pipe). Example: Inception | Interstellar | Dune",
        duration="How long the poll runs, e.g. 30s, 10m, 2h, 1d",
        poll_type="'movie' will auto-search YouTube for the winner. 'general' just shows results.",
        auto_action="If poll_type is movie, auto-search + post the winning video. Default: on."
    )
    @app_commands.choices(poll_type=[
        app_commands.Choice(name="General", value="general"),
        app_commands.Choice(name="Movie Night", value="movie"),
    ])
    async def poll(
        self,
        ctx: commands.Context,
        question: str,
        options: str,
        duration: str = "10m",
        poll_type: str = "general",
        auto_action: bool = True,
    ):
        opts = [o.strip() for o in options.split("|") if o.strip()]
        opts = list(dict.fromkeys(opts))  # de-dupe, preserve order

        if len(opts) < 2:
            return await ctx.send("❌ **Error:** Give me at least 2 options, separated by `|`. Example: `Inception | Dune`")
        if len(opts) > 125:
            return await ctx.send("❌ **Error:** Max 125 options supported (25 per dropdown × 5 dropdowns).")

        seconds = parse_duration(duration)
        if seconds is None or seconds < 10:
            return await ctx.send("❌ **Error:** Invalid duration. Use formats like `30s`, `10m`, `2h`, `1d` (minimum 10s).")
        if seconds > 7 * 86400:
            return await ctx.send("❌ **Error:** Max poll duration is 7 days.")

        if poll_type == "movie" and auto_action and not YTDLP_AVAILABLE:
            return await ctx.send(
                "❌ **Error:** `yt-dlp` isn't installed on the bot, so I can't auto-search YouTube.\n"
                "Run `pip install yt-dlp` on your Render service, or rerun this with `auto_action:false`."
            )

        end_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        poll_id = str(uuid.uuid4())

        poll = Poll(
            poll_id=poll_id,
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            creator_id=ctx.author.id,
            question=question,
            options=opts,
            end_at=end_at,
            poll_type=poll_type,
            auto_action=auto_action,
        )

        view = PollView(poll, self)
        embed = poll.build_embed()

        msg = await ctx.send(embed=embed, view=view)
        poll.message_id = msg.id
        self.active_polls[poll_id] = poll

    @tasks.loop(seconds=10)
    async def check_polls(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        expired = [p for p in self.active_polls.values() if not p.resolved and p.end_at <= now]
        for poll in expired:
            await self.resolve_poll(poll)

    @check_polls.before_loop
    async def before_check_polls(self):
        await self.bot.wait_until_ready()

    async def resolve_poll(self, poll: Poll):
        if poll.resolved:
            return
        poll.resolved = True

        channel = self.bot.get_channel(poll.channel_id)
        if channel is None:
            self.active_polls.pop(poll.id, None)
            return

        try:
            message = await channel.fetch_message(poll.message_id)
        except discord.NotFound:
            self.active_polls.pop(poll.id, None)
            return

        max_votes = max((len(v) for v in poll.votes.values()), default=0)
        winners = [i for i, v in poll.votes.items() if len(v) == max_votes and max_votes > 0]
        tie_note = None
        if len(winners) > 1:
            tie_note = f"⚖️ It was a tie between {len(winners)} options — picking one at random."
            winners = [random.choice(winners)]

        embed = poll.build_embed(resolved=True, winners=winners)

        # Disable the view
        disabled_view = discord.ui.View()
        try:
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

        if tie_note:
            await channel.send(tie_note)

        if not winners:
            await channel.send(f"📊 **{poll.question}** ended with no votes — nothing to act on.")
            self.active_polls.pop(poll.id, None)
            return

        winning_title = poll.options[winners[0]]

        if poll.poll_type == "movie" and poll.auto_action:
            await self.handle_movie_winner(channel, poll.question, winning_title)

        self.active_polls.pop(poll.id, None)

    async def handle_movie_winner(self, channel: discord.abc.Messageable, poll_question: str, title: str):
        if not YTDLP_AVAILABLE:
            await channel.send(
                f"🎬 **{title}** won **{poll_question}**, but `yt-dlp` isn't installed so I can't search YouTube for it."
            )
            return

        loop = asyncio.get_event_loop()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "default_search": "ytsearch1",
            "noplaylist": True,
            "skip_download": True,
        }

        def _search():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(title, download=False)

        try:
            info = await loop.run_in_executor(None, _search)
        except Exception:
            info = None

        entry = None
        if info:
            entries = info.get("entries") if "entries" in info else [info]
            entry = entries[0] if entries else None

        if not entry:
            embed = discord.Embed(
                title="⚠️ Couldn't find this on YouTube",
                description=(
                    f"**{title}** won the poll for **{poll_question}**, but I couldn't find an available "
                    f"result — it may be region-blocked, taken down, or under copyright restriction.\n\n"
                    f"Try searching manually or use another streaming source."
                ),
                color=discord.Color.orange(),
            )
            return await channel.send(embed=embed)

        # Basic availability / copyright flags yt-dlp can surface
        is_unavailable = entry.get("availability") in ("needs_auth", "premium_only", "subscriber_only", "private")
        is_blocked = bool(entry.get("age_limit")) and entry.get("age_limit", 0) >= 18

        video_url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id')}"

        if is_unavailable:
            embed = discord.Embed(
                title="⚠️ Video May Not Be Playable",
                description=(
                    f"**{title}** won **{poll_question}**, but the top match "
                    f"appears to be region-locked, age-restricted, or otherwise unavailable.\n\n"
                    f"[Check it here]({video_url}) — you may need another source."
                ),
                color=discord.Color.orange(),
            )
            return await channel.send(embed=embed)

        embed = discord.Embed(
            title=f"🎬 Now Showing: {entry.get('title', title)}",
            description=f"Winner of the poll: **{poll_question}**",
            url=video_url,
            color=discord.Color.green(),
        )
        if entry.get("thumbnail"):
            embed.set_thumbnail(url=entry["thumbnail"])
        if entry.get("uploader"):
            embed.add_field(name="Channel", value=entry["uploader"], inline=True)
        if entry.get("duration"):
            mins, secs = divmod(int(entry["duration"]), 60)
            embed.add_field(name="Length", value=f"{mins}m {secs}s", inline=True)

        embed.set_footer(text="LobbyBot • Hop in a VC and screen-share to watch together 🍿")

        if is_blocked:
            embed.add_field(
                name="⚠️ Heads up",
                value="This result is age-restricted — some members may need to be signed into YouTube to view it.",
                inline=False,
            )

        await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(LobbyTools(bot))
