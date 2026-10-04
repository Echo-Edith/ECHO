import os
import asyncio
import discord
from discord.ext import commands

# Retrieve environment variable webhook URLs directly
WEBSITE_WEBHOOK_URL = os.getenv("WEBSITE_WEBHOOK_URL", "")
DESIGN_WEBHOOK_URL = os.getenv("DESIGN_WEBHOOK_URL", "")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    """Runs setup operations before the bot connects to Discord."""
    try:
        await bot.load_extension("cogs.kumo")
        print("✅ Successfully loaded cog: cogs.kumo")
    except Exception as e:
        print(f"❌ Failed to load cog cogs.kumo: {e}")


@bot.event
async def on_ready():
    print(f"✅ Bot is ONLINE and logged in as: {bot.user} (ID: {bot.user.id})")
    print("--- WEBHOOK CONFIGURATION CHECK ---")
    print(f"🔗 WEBSITE_WEBHOOK_URL: {'LOADED (' + WEBSITE_WEBHOOK_URL[:30] + '...)' if WEBSITE_WEBHOOK_URL else '❌ MISSING'}")
    print(f"🔗 DESIGN_WEBHOOK_URL:  {'LOADED (' + DESIGN_WEBHOOK_URL[:30] + '...)' if DESIGN_WEBHOOK_URL else '❌ MISSING'}")
    print("-----------------------------------")

    try:
        synced = await bot.tree.sync()
        print(f"⚡ Global command sync successful: Registered {len(synced)} command(s).")
    except Exception as e:
        print(f"❌ Failed to sync global commands: {e}")


@bot.command(name="sync")
@commands.is_owner()
async def sync(ctx: commands.Context, guild_only: bool = False):
    if guild_only:
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"⚡ **Instantly synced** `{len(synced)}` command(s) to **{ctx.guild.name}**!")
    else:
        synced = await bot.tree.sync()
        await ctx.send(f"🌐 Triggered global sync for `{len(synced)}` command(s). Badge should reappear shortly!")


async def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("CRITICAL: DISCORD_BOT_TOKEN environment variable is missing!")

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
