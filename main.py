import os
import sys
import time
import logging
import discord
from discord.ext import commands
from keep_alive import keep_alive

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
        # Loading from the cogs folder as shown in your repo structure
        for ext in ['cogs.orca']:
            try:
                await self.load_extension(ext)
                print(f"[SUCCESS] Extension loaded: {ext}")
            except Exception as e:
                print(f"[WARNING] Extension load issue {ext}: {e}")

        try:
            synced = await self.tree.sync()
            print(f"[INFO] Synced {len(synced)} slash command(s).")
        except Exception as e:
            print(f"[ERROR] Failed to sync slash commands: {e}")

    async def on_ready(self):
        print(f"[INFO] ORCA Bot online as: {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="/custom-server | ORCA AI"
            )
        )


bot = OrcaClient()


def start_bot():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("[CRITICAL] 'DISCORD_TOKEN' environment variable is missing!")
        sys.exit(1)

    # Boot the threaded Flask server for Render to detect port binding immediately
    keep_alive(bot)
    print("[INFO] Web server thread started.")

    retry_delay = 15
    while True:
        try:
            bot.run(token)
            break
        except discord.errors.HTTPException as e:
            if getattr(e, 'status', 0) == 429:
                print(f"[WARNING] Discord 429 Rate Limit. Sleeping for {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 300)
            else:
                print(f"[ERROR] Discord HTTP Error: {e}")
                time.sleep(10)
        except Exception as e:
            print(f"[ERROR] Bot runtime error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    start_bot()
