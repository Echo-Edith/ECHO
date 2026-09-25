import asyncio
import os
import threading
from flask import Flask
import discord
from discord.ext import commands

# --- Flask Health Check Server ---
# Render requires a web port to stay open if deployed as a Web Service
app = Flask(__name__)

@app.route("/")
def health_check():
    return "Bot is alive!", 200

def run_flask():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Run Flask in a separate thread so it doesn't block the Discord bot
threading.Thread(target=run_flask, daemon=True).start()


# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True  # Enable Message Content Intent if your bot uses text commands

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

async def main():
    async with bot:
        # Import and add the OrcaCog from cogs/orca.py
        from cogs.orca import OrcaCog
        await bot.add_cog(OrcaCog(bot))
        
        # Get token from environment variables
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("DISCORD_TOKEN environment variable is missing.")
            
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
