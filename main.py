import os
import logging
import discord
from discord.ext import commands
from keep_alive import keep_alive

# Suppress verbose discord / asyncio logs from printing to stdout/cron logs
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
                loaded = True
                break
            except Exception:
                continue

        try:
            await self.tree.sync()
        except Exception:
            pass

    async def on_ready(self):
        pass


bot = OrcaClient()

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if token:
        keep_alive(bot)
        bot.run(token)

