import os
import sys
import logging
import discord
from discord.ext import commands
from keep_alive import keep_alive

# Suppress spammy HTTP logging
logging.getLogger('discord').setLevel(logging.ERROR)
logging.getLogger('discord.http').setLevel(logging.ERROR)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True


class OrcaClient(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        loaded = False
        for ext in ['cogs.orca', 'orca']:
            try:
                await self.load_extension(ext)
                print(f"✅ Cog loaded successfully: {ext}")
                loaded = True
                break
            except commands.ExtensionAlreadyLoaded:
                loaded = True
                break
            except Exception as e:
                print(f"⚠️ Could not load extension {ext}: {e}")

        try:
            synced = await self.tree.sync()
            print(f"🔁 Synced {len(synced)} slash commands globally.")
        except Exception as e:
            print(f"❌ Failed to sync command tree: {e}")

    async def on_ready(self):
        print(f"👑 ORCA Bot online as: {self.user} (ID: {self.user.id})")


bot = OrcaClient()

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ CRITICAL: 'DISCORD_TOKEN' environment variable is missing!")
        sys.exit(1)
    else:
        keep_alive(bot)
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ Bot runtime error: {e}")

