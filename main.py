import os
import threading
from flask import Flask
import discord
from discord.ext import commands

# 1. Initialize lightweight Flask app to satisfy Render's free Web Service port requirement
app = Flask(__name__)

@app.route("/")
def health_check():
    return "Kumo Bot is online and running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 2. Initialize your Discord Bot with intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("-----------------------------------------")
    
    # Load your cogs if not already loaded
    try:
        if not "cogs.kumo" in bot.extensions:
            await bot.load_extension("cogs.kumo")
            print("Successfully loaded cog: cogs.kumo")
    except Exception as e:
        print(f"Failed to load cogs.kumo: {e}")

    # Automatically sync slash commands globally so Discord recognizes them
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

def main():
    # Start the Flask web server in a separate background thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("Started background HTTP health-check server for Render.")

    # Get your Discord bot token from environment variables
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN environment variable is missing!")
        return

    # Run the Discord bot
    bot.run(token)

if __name__ == "__main__":
    main()

