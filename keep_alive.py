import os
import time
import logging
import asyncio
from threading import Thread
from flask import Flask, render_template_string, jsonify, request

try:
    import pymongo
except ImportError:
    pymongo = None

# Suppress verbose Flask / Werkzeug HTTP request logs
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
    return "<h1>ORCA Dashboard Active!</h1>", 200


@app.route('/ping')
@app.route('/health')
@app.route('/cron')
def cron_ping():
    return jsonify({"status": "ok"}), 200


@app.route('/api/guild-data', methods=['GET'])
def get_guild_data():
    channels = []
    roles = []
    categories = []

    if _bot_ref and _bot_ref.guilds:
        try:
            for guild in _bot_ref.guilds:
                for ch in guild.channels:
                    if str(ch.type) == "text":
                        channels.append({"id": str(ch.id), "name": ch.name, "type": "text"})
                    elif str(ch.type) == "category":
                        categories.append({"id": str(ch.id), "name": ch.name})

                for r in guild.roles:
                    if not r.is_default():
                        roles.append({"id": str(r.id), "name": r.name})
                break
        except Exception:
            pass

    db = get_db()
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}

    cog = _bot_ref.get_cog("Orca") if _bot_ref else None
    uptime_sec = int(time.time() - cog.start_time) if cog else 0

    db_usage_str = "0.01 MB"
    percent_val = 1
    if db is not None:
        try:
            stats = db.command("dbStats")
            data_bytes = stats.get("dataSize", 0) + stats.get("indexSize", 0)
            data_mb = round(data_bytes / (1024 * 1024), 2)
            if data_mb < 0.01:
                db_usage_str = f"{round(data_bytes / 1024, 2)} KB"
            else:
                db_usage_str = f"{data_mb} MB"
            percent_val = max(1, min(100, int((data_mb / 512.0) * 100)))
        except Exception:
            pass

    avatar_url = _bot_ref.user.display_avatar.url if (_bot_ref and _bot_ref.user) else "https://cdn.discordapp.com/embed/avatars/0.png"
    bot_name = _bot_ref.user.name if (_bot_ref and _bot_ref.user) else "ORCA"
    latency_ms = round(_bot_ref.latency * 1000) if _bot_ref else 0

    return jsonify({
        "channels": channels,
        "roles": roles,
        "categories": categories,
        "latency": latency_ms,
        "bot_avatar": avatar_url,
        "bot_name": bot_name,
        "maintenance": config.get("maintenance", False),
        "lockdown": config.get("lockdown", False),
        "uptime_seconds": uptime_sec,
        "db_usage": {"display": db_usage_str, "percent": percent_val}
    })


@app.route('/api/form-config', methods=['GET'])
def get_form_config():
    db = get_db()
    if db is None:
        return jsonify({})

    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return jsonify({
        "title": doc.get("title", ""),
        "description": doc.get("description", ""),
        "button_label": doc.get("button_label", ""),
        "category": doc.get("category", "Custom Bot Commission"),
        "channel_id": doc.get("channel_id"),
        "log_channel_id": doc.get("log_channel_id"),
        "ping_role_id": doc.get("ping_role_id"),
        "ping_toggle": doc.get("ping_toggle", True)
    })


@app.route('/api/save-form-config', methods=['POST'])
def save_form_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    channel_id = int(data["channel_id"]) if data.get("channel_id") else None
    log_channel_id = int(data["log_channel_id"]) if data.get("log_channel_id") else None
    ping_role_id = int(data["ping_role_id"]) if data.get("ping_role_id") else None

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {
            "$set": {
                "guild_id": guild_id,
                "title": data.get("title"),
                "description": data.get("description"),
                "button_label": data.get("button_label"),
                "category": data.get("category", "Custom Bot Commission"),
                "channel_id": channel_id,
                "log_channel_id": log_channel_id,
                "ping_role_id": ping_role_id,
                "ping_toggle": bool(data.get("ping_toggle", True))
            }
        },
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/all-form-questions', methods=['GET'])
def get_all_form_questions():
    db = get_db()
    if db is None:
        return jsonify({})

    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    categories_map = doc.get("category_questions", {})

    if not categories_map and doc.get("questions"):
        main_cat = doc.get("category", "Custom Bot Commission")
        categories_map = {main_cat: doc.get("questions", [])}

    return jsonify(categories_map)


@app.route('/api/form-questions', methods=['GET', 'POST'])
def handle_form_questions():
    db = get_db()
    if db is None:
        return jsonify([])

    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    if request.method == 'POST':
        data = request.json or {}
        category = data.get("category", "Custom Bot Commission")
        questions = data.get("questions", [])

        doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
        cat_map = doc.get("category_questions", {})
        cat_map[category] = questions

        db["guild_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {"category_questions": cat_map, "questions": cat_map.get(doc.get("category", "Custom Bot Commission"), [])}},
            upsert=True
        )
        return jsonify({"success": True})

    doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return jsonify(doc.get("questions", []))


@app.route('/api/form-presets', methods=['GET', 'POST'])
def handle_form_presets():
    db = get_db()
    if db is None:
        return jsonify([])

    if request.method == 'POST':
        data = request.json or {}
        preset_name = str(data.get("name", "")).strip()
        if preset_name:
            db["form_presets"].update_one(
                {"name": preset_name},
                {"$set": {
                    "name": preset_name,
                    "title": data.get("title"),
                    "description": data.get("description"),
                    "button_label": data.get("button_label"),
                    "channel_id": data.get("channel_id")
                }},
                upsert=True
            )
        return jsonify({"success": True})

    cursor = db["form_presets"].find()
    presets = [{
        "name": doc.get("name"),
        "title": doc.get("title"),
        "description": doc.get("description"),
        "button_label": doc.get("button_label"),
        "channel_id": doc.get("channel_id")
    } for doc in cursor]

    return jsonify(presets)


@app.route('/api/deploy-form-panel', methods=['POST'])
def deploy_form_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    cog = _bot_ref.get_cog("Orca")
    if not cog or not channel_id:
        return jsonify({"error": "Invalid request"}), 400

    future = asyncio.run_coroutine_threadsafe(cog.deploy_form_panel_from_web(int(channel_id)), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/update-form-panel', methods=['POST'])
def update_form_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    cog = _bot_ref.get_cog("Orca")
    if not cog:
        return jsonify({"error": "Cog not loaded"}), 500

    future = asyncio.run_coroutine_threadsafe(cog.update_form_panel_from_web(), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/maintenance', methods=['POST'])
def handle_maintenance():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"maintenance": bool(data.get("maintenance"))}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/lockdown', methods=['POST'])
def handle_lockdown():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"lockdown": bool(data.get("lockdown"))}},
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

