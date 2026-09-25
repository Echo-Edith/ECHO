import os
import asyncio
import logging
import discord
from discord.ext import commands
from keep_alive import keep_alive
from orca import OrcaCog

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"==========================================")
    print(f"Logged in as: {bot.user.name} ({bot.user.id})")
    print(f"Connected Guilds: {len(bot.guilds)}")
    print(f"==========================================")
    
    try:
        synced = await bot.tree.sync()
        print(f"[INFO] Synced {len(synced)} application (slash) commands.")
    except Exception as e:
        print(f"[ERROR] Failed to sync slash commands: {e}")

async def main():
    keep_alive(bot)
    
    async with bot:
        await bot.add_cog(OrcaCog(bot))
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            print("[CRITICAL] DISCORD_TOKEN environment variable not found!")
            return
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
