import os
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


# Root Route: Serves Dashboard UI or Cron Job Ping
@app.route('/')
def home():
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                return render_template_string(f.read())
        except Exception as e:
            return f"Error loading dashboard: {e}", 500

    return "<h1>ECHO Dashboard Active!</h1><p>Warning: index.html missing in root folder.</p>", 200


@app.route('/ping')
def ping():
    return "pong", 200


# Fetch Channels & Roles dynamically from active Discord Guilds
@app.route('/api/guild-data', methods=['GET'])
def get_guild_data():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({
            "channels": [],
            "roles": [],
            "categories": [],
            "latency": 0,
            "guild_count": 0
        })

    channels = []
    roles = []
    categories = []

    # Get primary guild
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

    return jsonify({
        "channels": channels,
        "roles": roles,
        "categories": categories,
        "latency": round(_bot_ref.latency * 1000),
        "guild_count": len(_bot_ref.guilds)
    })


# Fetch Total Ticket Count
@app.route('/api/ticket-stats', methods=['GET'])
def get_ticket_stats():
    db = get_db()
    if db is None:
        return jsonify({"total_tickets": 0})

    total = 0
    try:
        cursor = db["guild_config"].find({}, {"ticket_counter": 1})
        for doc in cursor:
            total += doc.get("ticket_counter", 0)
    except Exception:
        pass

    return jsonify({"total_tickets": total})


# API Endpoint: Shop Item Management
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
            "position": next_id
        })
        return jsonify({"success": True})

    # GET
    cursor = db["shop_items"].find().sort("position", 1)
    items = [{"id": d["item_id"], "name": d["name"], "desc": d.get("description"), "price": d.get("price")} for d in cursor]
    return jsonify(items)


@app.route('/api/shop-items/<int:item_id>', methods=['DELETE'])
def delete_shop_item(item_id):
    db = get_db()
    if db:
        db["shop_items"].delete_one({"item_id": item_id})
    return jsonify({"success": True})


# Save Ticket Config
@app.route('/api/save-ticket-config', methods=['POST'])
def save_ticket_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}

    # Save globally for first available guild
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
                "category_id": int(data["category_id"]) if data.get("category_id") else None,
                "log_channel_id": int(data["log_channel_id"]) if data.get("log_channel_id") else None,
            }
        },
        upsert=True
    )
    return jsonify({"success": True})


# Deploy Ticket Panel directly into Discord
@app.route('/api/deploy-ticket-panel', methods=['POST'])
def deploy_ticket_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    if not channel_id:
        return jsonify({"error": "Missing channel_id"}), 400

    cog = _bot_ref.get_cog("Echo")
    if cog is None:
        return jsonify({"error": "Cog not loaded"}), 500

    future = asyncio.run_coroutine_threadsafe(cog.deploy_ticket_panel_from_web(int(channel_id)), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Deploy Shop Embed directly into Discord
@app.route('/api/deploy-shop-panel', methods=['POST'])
def deploy_shop_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    if not channel_id:
        return jsonify({"error": "Missing channel_id"}), 400

    cog = _bot_ref.get_cog("Echo")
    if cog is None:
        return jsonify({"error": "Cog not loaded"}), 500

    future = asyncio.run_coroutine_threadsafe(cog.deploy_shop_panel_from_web(int(channel_id)), _bot_ref.loop)
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

