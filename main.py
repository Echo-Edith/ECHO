import os
import threading
import logging
import asyncio
from flask import Flask, render_template, request, jsonify
import discord
from discord.ext import commands

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# Initialize Flask App
app = Flask(__name__, template_folder=".", static_folder=".")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/generate-layout', methods=['POST'])
def api_generate_layout():
    try:
        data = request.get_json() or {}
        prompt = data.get('prompt', '')
        theme = data.get('theme', 'custom')
        
        # Import AI Brain dynamically
        import ai_brain
        layout = ai_brain.generate_discord_layout(prompt_theme=f"{prompt} {theme}".strip())
        return jsonify(layout)
    except Exception as e:
        logger.error(f"Error in layout generation: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/lockdown', methods=['GET'])
def api_lockdown():
    state = request.args.get('state', 'false').lower() == 'true'
    logger.info(f"[LOCKDOWN] Web maintenance mode state updated: {state}")
    return jsonify({"status": "success", "lockdown": state})

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# Initialize Discord Bot
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await bot.load_extension("orca")
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to load extensions or sync commands: {e}")

async def run_bot():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set. Bot will not start.")
        return
    await bot.start(token)

if __name__ == "__main__":
    # Start Web Server in a background thread
    web_thread = threading.Thread(target=run_flask, daemon=True)
    web_thread.start()
    
    # Run Discord Bot in main loop
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Application shut down cleanly.")
