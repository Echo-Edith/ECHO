import os
import asyncio
import discord
from discord.ext import commands
from keep_alive import keep_alive, WEBSITE_WEBHOOK_URL, DESIGN_WEBHOOK_URL

# Initialize Discord Bot with required intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    """Runs setup operations before the bot starts accepting events."""
    # 1. Load the primary command cog
    try:
        await bot.load_extension("cogs.orca")
        print("✅ Successfully loaded cog: cogs.orca")
    except Exception as e:
        print(f"❌ Failed to load cog cogs.orca: {e}")

    # 2. Sync slash commands globally across Discord
    try:
        synced = await bot.tree.sync()
        print(f"⚡ Synced {len(synced)} slash command(s) with Discord.")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")


@bot.event
async def on_ready():
    print(f"✅ Bot is ONLINE and logged in as: {bot.user} (ID: {bot.user.id})")
    print("--- WEBHOOK CONFIGURATION CHECK ---")
    print(f"🔗 WEBSITE_WEBHOOK_URL: {'LOADED (' + WEBSITE_WEBHOOK_URL[:30] + '...)' if WEBSITE_WEBHOOK_URL else '❌ MISSING'}")
    print(f"🔗 DESIGN_WEBHOOK_URL:  {'LOADED (' + DESIGN_WEBHOOK_URL[:30] + '...)' if DESIGN_WEBHOOK_URL else '❌ MISSING'}")
    print("-----------------------------------")


async def main():
    # 1. Start Flask web server in a non-blocking background daemon thread
    keep_alive()

    # 2. Get Discord Bot Token and boot bot
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("CRITICAL: DISCORD_BOT_TOKEN environment variable is missing!")

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
