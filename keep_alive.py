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
            "global_discount": 0,
            "discount_expires_at": None
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

    # Check for discount expiration
    discount_val = config.get("global_discount", 0)
    discount_expires = config.get("discount_expires_at")
    if discount_expires and int(time.time()) > discount_expires:
        discount_val = 0
        discount_expires = None
        if db is not None:
            db["guild_config"].update_one({"guild_id": guild_id}, {"$set": {"global_discount": 0, "discount_expires_at": None}})

    avatar_url = _bot_ref.user.display_avatar.url if _bot_ref.user else "https://cdn.discordapp.com/embed/avatars/0.png"

    return jsonify({
        "channels": channels,
        "roles": roles,
        "categories": categories,
        "latency": round(_bot_ref.latency * 1000),
        "bot_avatar": avatar_url,
        "bot_name": _bot_ref.user.name if _bot_ref.user else "ECHO",
        "maintenance": config.get("maintenance", False),
        "global_discount": discount_val,
        "discount_expires_at": discount_expires
    })


@app.route('/api/ticket-stats', methods=['GET'])
def get_ticket_stats():
    db = get_db()
    if db is None:
        return jsonify({"saved_config": {}})

    saved_config = {}
    try:
        guild_id = _bot_ref.guilds[0].id if _bot_ref and _bot_ref.guilds else 0
        doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
        saved_config = doc
    except Exception:
        pass

    return jsonify({"saved_config": saved_config})


@app.route('/api/ticket-logs', methods=['GET'])
def get_ticket_logs():
    db = get_db()
    if db is None:
        return jsonify([])

    cursor = db["ticket_logs"].find().sort("_id", -1).limit(100)
    logs = [{
        "ticket_number": doc.get("ticket_number"),
        "username": doc.get("username"),
        "user_id": doc.get("user_id"),
        "action": doc.get("action"),
        "timestamp": doc.get("timestamp"),
        "transcript": doc.get("transcript", "")
    } for doc in cursor]

    return jsonify(logs)


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


@app.route('/api/global-discount', methods=['POST'])
def handle_global_discount():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if _bot_ref and _bot_ref.guilds else 0
    discount_val = int(data.get("discount", 0))
    hours = int(data.get("hours", 0))

    expires_at = (int(time.time()) + (hours * 3600)) if (discount_val > 0 and hours > 0) else None

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {
            "global_discount": discount_val,
            "discount_expires_at": expires_at
        }},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/shop-items', methods=['GET', 'POST'])
def handle_shop_items():
    db = get_db()
    if db is None:
        return jsonify([])

    if request.method == 'POST':
        data = request.json or {}
        last_item = db["shop_items"].find_one(sort=[("item_id", -1)])
        next_id = (last_item["item_id"] + 1) if last_item and "item_id" in last_item else 1

        db["shop_items"].insert_one({
            "item_id": next_id,
            "name": data.get("name"),
            "description": data.get("description"),
            "price": data.get("price"),
            "available": data.get("available", True),
            "position": next_id
        })
        return jsonify({"success": True})

    cursor = db["shop_items"].find().sort("position", 1)
    items = [{
        "id": d["item_id"],
        "name": d["name"],
        "desc": d.get("description"),
        "price": d.get("price"),
        "available": d.get("available", True)
    } for d in cursor]
    return jsonify(items)


@app.route('/api/shop-items/<int:item_id>', methods=['PUT', 'DELETE'])
def edit_or_delete_shop_item(item_id):
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    if request.method == 'DELETE':
        db["shop_items"].delete_one({"item_id": item_id})
        return jsonify({"success": True})

    if request.method == 'PUT':
        data = request.json or {}
        db["shop_items"].update_one(
            {"item_id": item_id},
            {
                "$set": {
                    "name": data.get("name"),
                    "description": data.get("description"),
                    "price": data.get("price"),
                    "available": data.get("available", True)
                }
            }
        )
        return jsonify({"success": True})


@app.route('/api/promo-codes', methods=['GET', 'POST'])
def handle_promo_codes():
    db = get_db()
    if db is None:
        return jsonify([])

    if request.method == 'POST':
        data = request.json or {}
        code = str(data.get("code", "")).strip().upper()
        discount = int(data.get("discount", 0))
        user_limit = int(data.get("user_limit", 0))
        expire_hours = int(data.get("expire_hours", 0))

        expires_at = (int(time.time()) + (expire_hours * 3600)) if expire_hours > 0 else None

        if code and discount > 0:
            db["promo_codes"].update_one(
                {"code": code},
                {"$set": {
                    "code": code,
                    "discount": discount,
                    "user_limit": user_limit,
                    "expires_at": expires_at
                }},
                upsert=True
            )
        return jsonify({"success": True})

    cursor = db["promo_codes"].find()
    codes = [{
        "code": doc["code"],
        "discount": doc["discount"],
        "user_limit": doc.get("user_limit", 0),
        "expires_at": doc.get("expires_at")
    } for doc in cursor]
    return jsonify(codes)


@app.route('/api/promo-codes/<string:code>', methods=['DELETE'])
def delete_promo_code(code):
    db = get_db()
    if db:
        db["promo_codes"].delete_one({"code": code.upper()})
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


@app.route('/api/save-ticket-config', methods=['POST'])
def save_ticket_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if _bot_ref and _bot_ref.guilds else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {
            "$set": {
                "guild_id": guild_id,
                "title": data.get("title"),
                "description": data.get("description"),
                "welcome_message": data.get("welcome_message"),
                "panel_channel_id": int(data["channel_id"]) if data.get("channel_id") else None,
                "staff_role_id": int(data["staff_role_id"]) if data.get("staff_role_id") else None,
                "category_id": int(data["category_id"]) if data.get("category_id") else None
            }
        },
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/deploy-ticket-panel', methods=['POST'])
def deploy_ticket_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    cog = _bot_ref.get_cog("Echo")
    if not cog or not channel_id:
        return jsonify({"error": "Invalid request"}), 400

    future = asyncio.run_coroutine_threadsafe(cog.deploy_ticket_panel_from_web(int(channel_id)), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/update-ticket-panel', methods=['POST'])
def update_ticket_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    cog = _bot_ref.get_cog("Echo")
    if not cog:
        return jsonify({"error": "Cog not loaded"}), 500

    future = asyncio.run_coroutine_threadsafe(cog.update_ticket_panel_from_web(), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/deploy-shop-panel', methods=['POST'])
def deploy_shop_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    cog = _bot_ref.get_cog("Echo")
    if not cog or not channel_id:
        return jsonify({"error": "Invalid request"}), 400

    future = asyncio.run_coroutine_threadsafe(cog.deploy_shop_panel_from_web(int(channel_id)), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/update-shop-panel', methods=['POST'])
def update_shop_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    cog = _bot_ref.get_cog("Echo")
    if not cog:
        return jsonify({"error": "Cog not loaded"}), 500

    future = asyncio.run_coroutine_threadsafe(cog.update_shop_panel_from_web(), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)


def keep_alive(bot=None):
    global _bot_ref
    _bot_ref = bot
    t = Thread(target=run)
    t.daemon = True
    t.start()

