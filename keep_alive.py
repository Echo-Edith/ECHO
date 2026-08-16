import os
from threading import Thread
from flask import Flask, render_template_string, jsonify, request

try:
    import pymongo
except ImportError:
    pymongo = None

app = Flask(__name__)
_bot_ref = None


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
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return render_template_string(f.read())
    except FileNotFoundError:
        return "ECHO Web Dashboard Active!", 200


@app.route('/ping')
def ping():
    return "pong", 200


# API Endpoint: Fetch Shop Items
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


@app.route('/api/save-ticket-config', methods=['POST'])
def save_ticket_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "No database"}), 500

    data = request.json or {}
    db["ticket_dashboard_config"].update_one(
        {"id": 1},
        {"$set": data},
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

