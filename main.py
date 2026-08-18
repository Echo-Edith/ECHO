import os
import asyncio
import discord
from discord.ext import commands
from keep_alive import keep_alive

# Initialize Bot Instance with required Gateway Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


class EchoClient(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Safely try loading extension from either cogs folder or root
        loaded = False
        for ext in ['cogs.orca', 'orca']:
            try:
                await self.load_extension(ext)
                print(f"✅ Extension loaded: {ext}")
                loaded = True
                break
            except commands.ExtensionAlreadyLoaded:
                loaded = True
                break
            except Exception as e:
                continue

        if not loaded:
            print("❌ Warning: Could not find or load 'echo.py' or 'cogs/echo.py'. Check file location!")

        # Sync slash commands globally across all servers
        try:
            synced = await self.tree.sync()
            print(f"🔁 Application command trees synced successfully. ({len(synced)} commands registered)")
        except Exception as e:
            print(f"❌ Failed to sync slash command tree: {e}")

    async def on_ready(self):
        print(f"👑 ECHO is online! Logged in as: {self.user} (ID: {self.user.id})")


bot = EchoClient()

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ Error: 'DISCORD_TOKEN' environment variable is missing inside Render settings!")
    else:
        # Start background Flask server & Web Dashboard (passes bot for live stats)
        keep_alive(bot)
        bot.run(token)

