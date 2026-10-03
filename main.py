import os
import asyncio
import discord
from discord.ext import commands
from keep_alive import keep_alive

# Initialize Discord Bot with full privileged intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot is ONLINE and logged in as: {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"⚡ Successfully synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")

async def load_cogs():
    await bot.load_extension("cogs.orca")

async def main():
    # 1. Start background Flask server
    keep_alive()
    
    # 2. Verify token and start Discord Bot
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("CRITICAL: DISCORD_BOT_TOKEN environment variable is missing!")

    async with bot:
        await load_cogs()
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
