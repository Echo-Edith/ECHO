import os
import asyncio
import discord
from discord.ext import commands
from keep_alive import keep_alive

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot Online: {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} Slash Commands globally.")
    except Exception as e:
        print(f"Error syncing slash commands: {e}")

async def main():
    keep_alive()
    await bot.load_extension("cogs.orca")
    
    if not TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN environment variable is missing!")
    
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
