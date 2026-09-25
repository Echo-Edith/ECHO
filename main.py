import asyncio
import os
import discord
from discord.ext import commands

# 1. Import keep_alive from your existing keep_alive.py
from keep_alive import keep_alive

# 2. Import your ai_brain module
import ai_brain

# 3. Import your OrcaCog from cogs/orca.py
from cogs.orca import OrcaCog

# 4. Setup Discord Intents
intents = discord.Intents.default()
intents.message_content = True  # Required if your bot reads message content

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

async def main():
    async with bot:
        # Start the Flask web server (and pass bot reference if your keep_alive supports it)
        keep_alive(bot)
        
        # Load the Orca cog
        await bot.add_cog(OrcaCog(bot))
        
        # Fetch the token from Render environment variables
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("DISCORD_TOKEN environment variable is missing!")
            
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
