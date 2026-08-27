import os
import time
import logging
from threading import Thread
from flask import Flask, render_template_string, jsonify, request

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pymongo
except ImportError:
    pymongo = None

# Mute Werkzeug HTTP access logging completely to keep logs clean
log = logging.getLogger('werkzeug')
log.setLevel(logging.CRITICAL)
log.disabled = True

# Suppress Flask CLI server banner safely
try:
    import flask.cli
    flask.cli.show_server_banner = lambda *args, **kwargs: None
except Exception:
    pass

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


# Lightweight endpoint for cron-job.org and keep-alive pingers
@app.route('/ping')
@app.route('/health')
@app.route('/cron')
def cron_ping():
    return "OK", 200


@app.route('/')
def home():
    user_agent = request.headers.get('User-Agent', '').lower()
    if 'cron' in user_agent or 'uptime' in user_agent or 'bot' in user_agent:
        return "OK", 200

    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                return render_template_string(f.read())
        except Exception as e:
            return f"Error loading dashboard: {e}", 500
    return "OK", 200


@app.route('/api/stats', methods=['GET'])
def get_stats():
    ram_mb = 0.0
    cpu_pct = "0%"

    if psutil:
        try:
            process = psutil.Process()
            ram_mb = round(process.memory_info().rss / (1024 * 1024), 1)
            cpu_pct = f"{round(psutil.cpu_percent(interval=None), 1)}%"
        except Exception:
            pass

    cog = _bot_ref.get_cog("Orca") if _bot_ref else None
    uptime_sec = int(time.time() - cog.start_time) if cog else 0
    latency_ms = round(_bot_ref.latency * 1000) if (_bot_ref and _bot_ref.is_ready()) else 0

    return jsonify({
        "ping": latency_ms,
        "uptime_seconds": uptime_sec,
        "ram": f"{ram_mb} MB",
        "cpu": cpu_pct
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

            for cat in guild.categories:
                cat_channels = []
                for ch in cat.text_channels:
                    cat_channels.append({"id": str(ch.id), "name": ch.name})
                categories.append({"id": str(cat.id), "name": cat.name, "channels": cat_channels})

            sorted_roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
            for r in sorted_roles:
                if not r.is_default():
                    roles.append({"id": str(r.id), "name": r.name})

        except Exception:
            pass

    db = get_db()
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    config = db["guild_config"].find_one({"guild_id": guild_id}) if db is not None else {}

    return jsonify({
        "guild_name": guild_name,
        "categories": categories,
        "roles": roles,
        "bot_avatar": _bot_ref.user.display_avatar.url if (_bot_ref and _bot_ref.user) else "",
        "bot_name": _bot_ref.user.name if (_bot_ref and _bot_ref.user) else "ORCA",
        "category_configs": config.get("category_configs", {}),
        "verification_config": config.get("verification_config", {}),
        "staff_config": config.get("staff_config", {}),
        "estimator_config": config.get("estimator_config", {}),
        "monitor_config": config.get("monitor_config", {}),
        "automod_config": config.get("automod_config", {}),
        "welcomer_config": config.get("welcomer_config", {}),
        "rotator_config": config.get("rotator_config", {}),
        "review_config": config.get("review_config", {}),
        "outreach_config": config.get("outreach_config", {})
    })


@app.route('/api/staff-members', methods=['GET'])
def get_staff_members():
    staff_list = []
    if _bot_ref and _bot_ref.guilds:
        try:
            guild = _bot_ref.guilds[0]
            db = get_db()
            config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
            staff_config = config.get("staff_config", {})
            header_ids = staff_config.get("staff_header_role_ids", [])
            header_ids_int = {int(hid) for hid in header_ids if str(hid).isdigit()}

            for member in guild.members:
                if member.bot:
                    continue
                matched_header = next((r for r in member.roles if r.id in header_ids_int), None)
                if matched_header or member.guild_permissions.administrator:
                    top_role = member.top_role if member.top_role and member.top_role.name != "@everyone" else matched_header
                    role_name = matched_header.name if matched_header else (top_role.name if top_role else "Staff")
                    hex_color = f"#{top_role.color.value:06x}" if (top_role and top_role.color.value) else "#3b82f6"
                    avatar_url = member.display_avatar.url if member.display_avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
                    staff_list.append({
                        "id": str(member.id),
                        "name": str(member),
                        "role": role_name,
                        "color": hex_color,
                        "avatar": avatar_url
                    })
        except Exception:
            pass

    return jsonify({"staff": staff_list})


@app.route('/api/add-staff-user', methods=['POST'])
def add_staff_user():
    data = request.json or {}
    user_id = data.get("user_id")
    role_id = data.get("role_id")

    if not _bot_ref or not _bot_ref.guilds or not user_id or not role_id:
        return jsonify({"error": "Invalid request"}), 400

    guild = _bot_ref.guilds[0]
    db = get_db()
    config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
    staff_config = config.get("staff_config", {})
    header_ids = staff_config.get("staff_header_role_ids", [])

    async def _add():
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
        target_role = guild.get_role(int(role_id))
        roles_to_add = [target_role] if target_role else []
        for hid in header_ids:
            if str(hid).isdigit():
                hr = guild.get_role(int(hid))
                if hr:
                    roles_to_add.append(hr)
        if member and roles_to_add:
            await member.add_roles(*roles_to_add, reason="ORCA Web Dashboard Staff Promotion")

    future = asyncio.run_coroutine_threadsafe(_add(), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/remove-staff-user', methods=['POST'])
def remove_staff_user():
    data = request.json or {}
    user_id = data.get("user_id")

    if not _bot_ref or not _bot_ref.guilds or not user_id:
        return jsonify({"error": "Invalid request"}), 400

    guild = _bot_ref.guilds[0]
    db = get_db()
    config = db["guild_config"].find_one({"guild_id": guild.id}) if db is not None else {}
    staff_config = config.get("staff_config", {})
    header_ids = staff_config.get("staff_header_role_ids", [])

    async def _remove():
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
        if member:
            for hid in header_ids:
                if str(hid).isdigit():
                    hr = guild.get_role(int(hid))
                    if hr and hr in member.roles:
                        await member.remove_roles(hr, reason="ORCA Web Dashboard Staff Demotion")

    future = asyncio.run_coroutine_threadsafe(_remove(), _bot_ref.loop)
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/blacklist-users', methods=['GET'])
def get_blacklist_users():
    db = get_db()
    if db is None:
        return jsonify({"blacklist": []})

    bl_list = []
    for doc in db["blacklist"].find():
        user_id = doc.get("user_id")
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
        if _bot_ref and _bot_ref.guilds:
            try:
                guild = _bot_ref.guilds[0]
                member = guild.get_member(int(user_id))
                if member and member.display_avatar:
                    avatar_url = member.display_avatar.url
            except Exception:
                pass

        bl_list.append({
            "user_id": user_id,
            "username": doc.get("username", f"User {user_id}"),
            "reason": doc.get("reason", "No reason provided"),
            "avatar": avatar_url
        })

    return jsonify({"blacklist": bl_list})


@app.route('/api/add-blacklist-user', methods=['POST'])
def add_blacklist_user():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    user_id = data.get("user_id")
    reason = data.get("reason", "No reason provided")

    if not user_id:
        return jsonify({"error": "User ID required"}), 400

    username = f"User {user_id}"
    if _bot_ref and _bot_ref.guilds:
        try:
            guild = _bot_ref.guilds[0]
            member = guild.get_member(int(user_id))
            if member:
                username = str(member)
        except Exception:
            pass

    db["blacklist"].update_one(
        {"user_id": str(user_id)},
        {"$set": {"user_id": str(user_id), "username": username, "reason": reason}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/remove-blacklist-user', methods=['POST'])
def remove_blacklist_user():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    user_id = data.get("user_id")

    if not user_id:
        return jsonify({"error": "User ID required"}), 400

    db["blacklist"].delete_one({"user_id": str(user_id)})
    return jsonify({"success": True})


@app.route('/api/deploy-outreach-dm', methods=['POST'])
def deploy_outreach_dm():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    user_id = data.get("user_id")
    pitch = data.get("pitch", "")
    image_url = data.get("image_url", "")
    ad_copy = data.get("ad_copy", "")

    cog = _bot_ref.get_cog("Orca")
    if not cog or not user_id or not str(user_id).isdigit():
        return jsonify({"error": "Valid Discord User ID required"}), 400

    future = asyncio.run_coroutine_threadsafe(
        cog.deploy_outreach_dm_from_web(int(user_id), pitch, image_url, ad_copy),
        _bot_ref.loop
    )
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/save-outreach-config', methods=['POST'])
def save_outreach_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"outreach_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/save-rotator-config', methods=['POST'])
def save_rotator_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"rotator_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/save-welcomer-config', methods=['POST'])
def save_welcomer_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"welcomer_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/save-automod-config', methods=['POST'])
def save_automod_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"automod_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/save-estimator-config', methods=['POST'])
def save_estimator_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"estimator_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/deploy-estimator-panel', methods=['POST'])
def deploy_estimator_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    cog = _bot_ref.get_cog("Orca")

    if not cog or not channel_id:
        return jsonify({"error": "Invalid request"}), 400

    future = asyncio.run_coroutine_threadsafe(
        cog.deploy_estimator_panel_from_web(int(channel_id)),
        _bot_ref.loop
    )
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/save-monitor-config', methods=['POST'])
def save_monitor_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"monitor_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/publish-announcement', methods=['POST'])
def publish_announcement():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    cog = _bot_ref.get_cog("Orca")

    if not cog or not channel_id:
        return jsonify({"error": "Invalid request"}), 400

    future = asyncio.run_coroutine_threadsafe(
        cog.publish_announcement_from_web(data),
        _bot_ref.loop
    )
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/save-review-config', methods=['POST'])
def save_review_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"review_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/save-staff-config', methods=['POST'])
def save_staff_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"staff_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/save-verification-config', methods=['POST'])
def save_verification_config():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"verification_config": data}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/deploy-verification-panel', methods=['POST'])
def deploy_verification_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    cog = _bot_ref.get_cog("Orca")

    if not cog or not channel_id:
        return jsonify({"error": "Invalid request"}), 400

    future = asyncio.run_coroutine_threadsafe(
        cog.deploy_verification_panel_from_web(int(channel_id)),
        _bot_ref.loop
    )
    try:
        future.result(timeout=10)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/save-category', methods=['POST'])
def save_category():
    db = get_db()
    if db is None:
        return jsonify({"error": "Database unavailable"}), 500

    data = request.json or {}
    category_name = data.get("category_name")
    category_config = data.get("config", {})

    guild_id = _bot_ref.guilds[0].id if (_bot_ref and _bot_ref.guilds) else 0
    doc = db["guild_config"].find_one({"guild_id": guild_id}) or {}
    cat_configs = doc.get("category_configs", {})
    cat_configs[category_name] = category_config

    db["guild_config"].update_one(
        {"guild_id": guild_id},
        {"$set": {"category_configs": cat_configs}},
        upsert=True
    )
    return jsonify({"success": True})


@app.route('/api/deploy-form-panel', methods=['POST'])
def deploy_form_panel():
    if _bot_ref is None or not _bot_ref.is_ready():
        return jsonify({"error": "Bot not ready"}), 500

    data = request.json or {}
    channel_id = data.get("channel_id")
    category = data.get("category", "Custom Bot Commission")

    cog = _bot_ref.get_cog("Orca")
    future = asyncio.run_coroutine_threadsafe(
        cog.deploy_form_panel_from_web(int(channel_id), category),
        _bot_ref.loop
    )
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

