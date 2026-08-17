import os
import time
import asyncio
from threading import Thread
from flask import Flask, render_template_string, jsonify, request

try:
    import pymongo
except ImportError:
    pymongo = None

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
    return "<h1>ECHO Dashboard Active!</h1>", 200


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/api/guild-data', methods=['GET'])
def get_guild_data():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({
            "channels": [],
            "roles": [],
            "categories": [],
            "latency": 0,
            "bot_avatar": "https://cdn.discordapp.com/embed/avatars/0.png",
            "bot_name": "ECHO",
            "maintenance": False,
            "uptime_seconds": 0,
            "db_usage": {"display": "0.24 MB", "percent": 5}
        })

    channels = []
    roles = []
    categories = []

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

    db = get_db()
    guild_id = _bot_ref.guilds[0].id if _bot_ref.guilds else 0
    config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}

    cog = _bot_ref.get_cog("Echo")
    uptime_sec = int(time.time() - cog.start_time) if cog else 0

    db_usage_str = "0.24 MB"
    percent_val = 5
    if db is not None:
        try:
            stats = db.command("dbStats")
            data_size_bytes = stats.get("dataSize", 250000)
            data_size_mb = round(data_size_bytes / (1024 * 1024), 2)
            db_usage_str = f"{data_size_mb} MB"
            percent_val = max(1, min(100, int((data_size_mb / 512.0) * 100)))
        except Exception:
            pass

    avatar_url = _bot_ref.user.display_avatar.url if _bot_ref.user else "https://cdn.discordapp.com/embed/avatars/0.png"

    return jsonify({
        "channels": channels,
        "roles": roles,
        "categories": categories,
        "latency": round(_bot_ref.latency * 1000),
        "bot_avatar": avatar_url,
        "bot_name": _bot_ref.user.name if _bot_ref.user else "ECHO",
        "maintenance": config.get("maintenance", False),
        "uptime_seconds": uptime_sec,
        "db_usage": {"display": db_usage_str, "percent": percent_val}
    })


@app.route('/api/form-config', methods=['GET'])
def get_form_config():
    db = get_db()
    if db is None:
        return jsonify({})

    guild_id = _bot_ref.guilds[0].id if _bot_ref and _bot_ref.guilds else 0
    doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return jsonify(doc)


@app.route('/api/save-form-config', methods=['POST'])
def save_form_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if _bot_ref and _bot_ref.guilds else 0

    channel_id = int(data["channel_id"]) if data.get("channel_id") else None
    ping_role_id = int(data["ping_role_id"]) if data.get("ping_role_id") else None

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {
            "$set": {
                "guild_id": guild_id,
                "title": data.get("title"),
                "description": data.get("description"),
                "button_label": data.get("button_label"),
                "channel_id": channel_id,
                "ping_role_id": ping_role_id
            }
        },
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/form-questions', methods=['GET', 'POST'])
def handle_form_questions():
    db = get_db()
    if db is None:
        return jsonify([])

    guild_id = _bot_ref.guilds[0].id if _bot_ref and _bot_ref.guilds else 0

    if request.method == 'POST':
        data = request.json or {}
        questions = data.get("questions", [])
        db["guild_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {"questions": questions}},
            upsert=True
        )
        return jsonify({"success": True})

    doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    return jsonify(doc.get("questions", []))


@app.route('/api/form-submissions', methods=['GET'])
def get_form_submissions():
    db = get_db()
    if db is None:
        return jsonify([])

    cursor = db["form_submissions"].find().sort("_id", -1).limit(100)
    subs = [{
        "username": doc.get("username"),
        "user_id": doc.get("user_id"),
        "answers": doc.get("answers", []),
        "timestamp": doc.get("timestamp")
    } for doc in cursor]

    return jsonify(subs)


@app.route('/api/deploy-form-panel', methods=['POST'])
def deploy_form_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    cog = _bot_ref.get_cog("Echo")
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

    cog = _bot_ref.get_cog("Echo")
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
    guild_id = _bot_ref.guilds[0].id if _bot_ref and _bot_ref.guilds else 0
    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"maintenance": bool(data.get("maintenance"))}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/blacklist', methods=['GET', 'POST'])
def handle_blacklist():
    db = get_db()
    if db is None:
        return jsonify([])

    if request.method == 'POST':
        data = request.json or {}
        user_id = str(data.get("user_id")).strip()
        reason = str(data.get("reason", "No reason provided"))

        username = "Unknown User"
        if _bot_ref and _bot_ref.is_ready():
            try:
                user_obj = _bot_ref.get_user(int(user_id))
                if user_obj:
                    username = str(user_obj)
            except Exception:
                pass

        if user_id:
            db["blacklist"].update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id, "username": username, "reason": reason}},
                upsert=True
            )
        return jsonify({"success": True})

    cursor = db["blacklist"].find()
    return jsonify([{
        "user_id": doc["user_id"],
        "username": doc.get("username", "Unknown User"),
        "reason": doc.get("reason", "No reason provided")
    } for doc in cursor])


@app.route('/api/blacklist/<string:user_id>', methods=['DELETE'])
def delete_blacklist(user_id):
    db = get_db()
    if db:
        db["blacklist"].delete_one({"user_id": str(user_id)})
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

