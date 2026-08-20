import os
import time
import logging
import asyncio
import psutil
from threading import Thread
from flask import Flask, render_template_string, jsonify, request

try:
    import pymongo
except ImportError:
    pymongo = None

# Suppress verbose Flask / Werkzeug HTTP request logs to keep logs clean
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
_bot_ref = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "index.html")


def get_db():
    mongo_uri = os.getenv("MONGO_URI")
    if not mongo_uri or not pymongo:
        return None
    try:
        client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        return client["echo_bot"]
    except Exception:
        return None


@app.route('/')
def home():
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                return render_template_string(f.read())
        except Exception as e:
            return f"Error loading dashboard: {e}", 500
    return "<h1>ORCA Automation Studio Engine Active!</h1>", 200


@app.route('/ping')
@app.route('/health')
@app.route('/cron')
def cron_ping():
    # Silent return for uptime monitors and cron jobs to prevent terminal output clutter
    return jsonify({"status": "ok"}), 200


@app.route('/api/stats', methods=['GET'])
def get_stats():
    process = psutil.Process()
    ram_mb = round(process.memory_info().rss / (1024 * 1024), 1)
    cpu_pct = psutil.cpu_percent(interval=None)

    cog = _bot_ref.get_cog("Orca") if _bot_ref else None
    uptime_sec = int(time.time() - cog.start_time) if cog else 0
    latency_ms = round(_bot_ref.latency * 1000) if (_bot_ref and _bot_ref.is_ready()) else 0

    db = get_db()
    db_usage_str = "0.01 MB"
    if db is not None:
        try:
            stats = db.command("dbStats")
            data_bytes = stats.get("dataSize", 0) + stats.get("indexSize", 0)
            data_mb = round(data_bytes / (1024 * 1024), 2)
            if data_mb < 0.01:
                db_usage_str = f"{round(data_bytes / 1024, 2)} KB"
            else:
                db_usage_str = f"{data_mb} MB"
        except Exception:
            pass

    return jsonify({
        "ping": latency_ms,
        "uptime_seconds": uptime_sec,
        "ram": f"{ram_mb} MB",
        "cpu": f"{cpu_pct}%",
        "db_usage": {"display": db_usage_str}
    })


@app.route('/api/guild-data', methods=['GET'])
def get_guild_data():
    categories = []
    roles = []
    guild_name = "Connected Server"

    if _bot_ref and _bot_ref.guilds:
        try:
            guild = _bot_ref.guilds[0]
            guild_name = guild.name

            # Preserve full category and channel hierarchy from Discord server
            for cat in guild.categories:
                cat_channels = []
                for ch in cat.text_channels:
                    cat_channels.append({"id": str(ch.id), "name": ch.name})
                categories.append({"name": cat.name, "channels": cat_channels})

            # Add uncategorized channels if any exist
            uncategorized = [ch for ch in guild.text_channels if ch.category is None]
            if uncategorized:
                categories.insert(0, {
                    "name": "General Channels",
                    "channels": [{"id": str(ch.id), "name": ch.name} for ch in uncategorized]
                })

            # Real server roles sorted by hierarchy position
            sorted_roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
            for r in sorted_roles:
                if not r.is_default():
                    roles.append({"id": str(r.id), "name": r.name})

        except Exception as e:
            pass

    db = get_db()
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}

    avatar_url = _bot_ref.user.display_avatar.url if (_bot_ref and _bot_ref.user) else "https://cdn.discordapp.com/embed/avatars/0.png"
    bot_name = _bot_ref.user.name if (_bot_ref and _bot_ref.user) else "ORCA"

    return jsonify({
        "guild_name": guild_name,
        "categories": categories,
        "roles": roles,
        "bot_avatar": avatar_url,
        "bot_name": bot_name,
        "maintenance": config.get("maintenance", False),
        "lockdown": config.get("lockdown", False),
        "category_configs": config.get("category_configs", {})
    })


@app.route('/api/save-category', methods=['POST'])
def save_category():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    category_name = data.get("category_name")
    category_config = data.get("config", {})

    if not category_name:
        return jsonify({"error": "Category name required"}), 400

    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    cat_configs = doc.get("category_configs", {})
    cat_configs[category_name] = category_config

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"guild_id": guild_id, "category_configs": cat_configs}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/deploy-form-panel', methods=['POST'])
def deploy_form_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot is currently offline or reconnecting"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    category = data.get("category", "Custom Bot Commission")

    cog = _bot_ref.get_cog("Orca")
    if not cog or not channel_id:
        return jsonify({"error": "Invalid channel or Orca cog not initialized"}), 400

    # Thread-safe thread execution into Discord asyncio event loop
    future = asyncio.run_coroutine_threadsafe(
        cog.deploy_form_panel_from_web(int(channel_id), category),
        _bot_ref.loop
    )
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/maintenance', methods=['POST'])
def handle_maintenance():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"guild_id": guild_id, "maintenance": bool(data.get("maintenance"))}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/lockdown', methods=['POST'])
def handle_lockdown():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"guild_id": guild_id, "lockdown": bool(data.get("lockdown"))}},
        upsert=True
    )
    return jsonify({"success": True})


def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)


def keep_alive(bot=None):
    global _bot_ref
    _bot_ref = bot
    t = Thread(target=run)
    t.daemon = True
    t.start()

