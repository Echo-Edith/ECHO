import asyncio
import os
import discord
from discord.ext import commands

# 1. Import keep_alive to start your web server and render templates/index.html
from keep_alive import keep_alive

# 2. Import your local modules/cogs
import ai_brain
from cogs.orca import OrcaCog

# 3. Setup Discord Intents and Bot
intents = discord.Intents.default()
intents.message_content = True  # Make sure Message Content Intent is enabled in Discord Developer Portal

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

async def main():
    async with bot:
        # Load the Orca cog
        await bot.add_cog(OrcaCog(bot))
        
        # Start the web dashboard / keep-alive server
        keep_alive()
        
        # Fetch bot token from Render Environment Variables
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("DISCORD_TOKEN environment variable is missing!")
            
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
