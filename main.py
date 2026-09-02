import os
import sys
import time
import asyncio
import logging
import discord
from discord.ext import commands
from keep_alive import keep_alive

# Suppress verbose discord logs
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


def start_bot():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ CRITICAL: 'DISCORD_TOKEN' environment variable is missing!")
        sys.exit(1)

    keep_alive(bot)

    retry_delay = 15
    while True:
        try:
            bot.run(token)
            break
        except discord.errors.HTTPException as e:
            if getattr(e, 'status', 0) == 429:
                print(f"⚠️ Discord 429 Rate Limited. Sleeping {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 300)
            else:
                print(f"❌ Discord HTTP Error: {e}")
                time.sleep(10)
        except Exception as e:
            print(f"❌ Bot runtime error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    start_bot()

