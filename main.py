import os
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
        # Dynamically load the cogs matching your cogs folder directory
        extensions = ['cogs.echo']
        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Extension loaded: {ext}")
            except Exception as e:
                print(f"❌ Failed to load extension {ext}: {e}")
        
        # Sync slash commands globally
        await self.tree.sync()
        print("🔁 Application command trees synced successfully.")

    async def on_ready(self):
        print(f"👑 ECHO is online! Logged in as: {self.user} (ID: {self.user.id})")

bot = EchoClient()

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ Error: 'DISCORD_TOKEN' environment variable is missing inside Render settings!")
    else:
        keep_alive()  # Runs the background Flask server to prevent Render from sleeping
        bot.run(token)

