from flask import Flask, request, redirect, session, render_template, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, emit
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime , timedelta
from werkzeug.utils import secure_filename
import bcrypt
import re
import os
import math
import stripe
import zipfile
import uuid
import shutil
import subprocess
import json
import socket
import random
import secrets

app = Flask(__name__, template_folder=".", static_folder=".", static_url_path="")

app.secret_key = "change-this-secret-key"

# Remember login sessions
app.permanent_session_lifetime = timedelta(days=30)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False  #True when HTTPS live
)
stripe.api_key = "sk_test_51TUgM1PEeETDzLUbe22GNXLu1JKiK3B4vjjJMDCe0z1dQa8jAeB9TyO68wPw40oVv9e4fMOeLfePdTk2kkaLmaTf00cYd47gfn"
STRIPE_WEBHOOK_SECRET = ""
BASE_URL = "http://172.104.189.194"
socketio = SocketIO(app, cors_allowed_origins="*")
GAME_SERVER_TOKEN = os.environ.get("GAME_SERVER_TOKEN", "dev-game-server-token")
client = MongoClient("mongodb://172.104.189.194:27017/")
db = client["bloxy"]

GAME_PORT_MIN = 30000
GAME_PORT_MAX = 40000

BLOCKED_PORTS = {
    22,
    80,
    443,
    422,
    5000,
    27017,
    3306,
    5432,
    6379,
    25565
}
users = db["users"]
messages = db["messages"]
parties = db["parties"] 
friend_requests = db["friend_requests"]
block_transactions = db["block_transactions"]
games_collection = db["games"]
join_tickets = db["join_tickets"]
game_comments = db["game_comments"]
game_likes = db["game_likes"]
game_favorites = db["game_favorites"]
game_comments.create_index([("game_id", 1), ("created_at", -1)])
game_likes.create_index([("game_id", 1), ("username", 1)], unique=True)
game_favorites.create_index([("game_id", 1), ("username", 1)], unique=True)
game_favorites.create_index([("username", 1), ("created_at", -1)])
join_tickets.create_index("ticket", unique=True)
join_tickets.create_index("expires_at")
games_collection.create_index("game_id", unique=True)
games_collection.create_index([("status", 1), ("created_at", -1)])
active_game_players = db["active_game_players"]
active_game_players.create_index([("game_id", 1), ("username", 1)], unique=True)
active_game_players.create_index([("last_seen", 1)])
active_game_players = db["active_game_players"]

active_game_players.create_index(
    [("game_id", 1), ("server_id", 1), ("peer_id", 1)],
    unique=True
)

active_game_players.create_index("last_seen")
GODOT_HEADLESS_PATH = "./godot-server"
GAME_UPLOAD_DIR = "game_uploads"
GAME_EXTRACT_DIR = "game_builds"

os.makedirs(GAME_UPLOAD_DIR, exist_ok=True)
os.makedirs(GAME_EXTRACT_DIR, exist_ok=True)

ALLOWED_GAME_EXTENSIONS = {"zip"}

block_transactions.create_index([("username", 1), ("created_at", -1)])

BLOCK_PACKS = {
    "100": {
        "name": "100 Blocks",
        "blocks": 100,
        "price_usd": 1.00
    },
    "500": {
        "name": "500 Blocks",
        "blocks": 500,
        "price_usd": 5.00
    },
    "1000": {
        "name": "1,000 Blocks",
        "blocks": 1000,
        "price_usd": 10.00
    },
    "2500": {
        "name": "2,500 Blocks",
        "blocks": 2500,
        "price_usd": 25.00
    }
}

users.create_index("username", unique=True)

UPLOAD_FOLDER = "pfp"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

login_attempts = {}
online_users = {}           
online_usernames = set()   
cooldowns = {}

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")

support_tickets = db["support_tickets"]

support_tickets.create_index([("username", 1), ("created_at", -1)])
support_tickets.create_index([("status", 1), ("created_at", -1)])

SUPPORT_EMAIL = "support@bloxy.com"
ADMINS = {"admin"}
support_tickets = db["support_tickets"]
bans = db["bans"]

support_tickets.create_index([("username", 1), ("created_at", -1)])
support_tickets.create_index([("status", 1), ("created_at", -1)])
bans.create_index("username", unique=True)
 
ADMIN_USERS = {"admin"}
GAME_ICON_DIR = "game_icons"

ALLOWED_GAME_ICON_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

os.makedirs(GAME_ICON_DIR, exist_ok=True)

def cleanup_inactive_game_players():
    cutoff = datetime.utcnow() - timedelta(minutes=2)

    stale = list(active_game_players.find(
        {"last_seen": {"$lt": cutoff}},
        {"_id": 0, "game_id": 1}
    ))

    affected_games = set()

    for p in stale:
        if p.get("game_id"):
            affected_games.add(p["game_id"])

    active_game_players.delete_many({
        "last_seen": {"$lt": cutoff}
    })

    for game_id in affected_games:
        update_game_player_count(game_id)

def now_iso():
    return datetime.utcnow().isoformat()

def allowed_game_icon(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_GAME_ICON_EXTENSIONS

def safe_str(value, max_len=250):
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def valid_username(username):
    return bool(USERNAME_RE.fullmatch(username))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
def record_recently_played(username, game_id):
    username = safe_str(username, 20)
    game_id = safe_str(game_id, 80)

    if not valid_username(username) or not game_id:
        return

    users.update_one(
        {"username": username},
        {
            "$pull": {
                "recently_played": {
                    "game_id": game_id
                }
            }
        }
    )

    users.update_one(
        {"username": username},
        {
            "$push": {
                "recently_played": {
                    "$each": [
                        {
                            "game_id": game_id,
                            "played_at": datetime.utcnow()
                        }
                    ],
                    "$position": 0,
                    "$slice": 30
                }
            }
        }
    )


def get_recently_played_games_for_user(username, limit=12):
    username = safe_str(username, 20)

    if not valid_username(username):
        return []

    user = users.find_one(
        {"username": username},
        {
            "_id": 0,
            "recently_played": 1
        }
    )

    if not user:
        return []

    recently_played = user.get("recently_played", [])

    if not isinstance(recently_played, list):
        recently_played = []

    game_ids = []

    for item in recently_played:
        if not isinstance(item, dict):
            continue

        game_id = safe_str(item.get("game_id"), 80)

        if game_id and game_id not in game_ids:
            game_ids.append(game_id)

    game_ids = game_ids[:limit]

    if not game_ids:
        return []

    game_docs = list(
        games_collection.find(
            {
                "game_id": {"$in": game_ids},
                "status": "approved"
            },
            {"_id": 0}
        )
    )

    game_map = {}

    for game in game_docs:
        game_map[game.get("game_id")] = game

    games = []

    for game_id in game_ids:
        game = game_map.get(game_id)

        if not game:
            continue

        games.append({
            "id": game.get("game_id"),
            "title": game.get("title", ""),
            "description": game.get("description", ""),
            "players": game.get("players", 0),
            "creator": game.get("creator", ""),
            "icon_url": game.get("icon_url") or "/logo.png"
        })

    return games

def get_current_user():
    username = session.get("user")
    if not isinstance(username, str) or not valid_username(username):
        return None
    return users.find_one({"username": username})


def are_friends(a, b):
    if not valid_username(a) or not valid_username(b):
        return False

    user = users.find_one({"username": a})
    return bool(user and b in user.get("friends", []))


def valid_room(room):
    room = safe_str(room, 100)

    if room == "global":
        return room

    if room.startswith("dm_"):
        parts = room.replace("dm_", "").split("_")
        if len(parts) == 2 and valid_username(parts[0]) and valid_username(parts[1]):
            return room

    if room.startswith("party_"):
        pid = room.replace("party_", "")
        if ObjectId.is_valid(pid):
            return room

    return "global"

def user_allows_online_status(user):
    if not user:
        return False

    privacy = user.get("privacy", {})
    return privacy.get("show_online", True) is True

def start_game_server(game):
    server_pck = game.get("server_pck_path")
    game_id = game.get("game_id", "unknown")

    if not server_pck or not os.path.exists(server_pck):
        return False, "server.pck not found", None, None

    try:
        server_port = allocate_game_port()
    except Exception as e:
        return False, str(e), None, None

    os.makedirs("game_logs", exist_ok=True)

    log_path = os.path.join("game_logs", f"{game_id}.log")
    log_file = open(log_path, "a")

    try:
        proc = subprocess.Popen(
            [
                GODOT_HEADLESS_PATH,
                "--headless",
                "--main-pack",
                server_pck,
                "--",
                "--bloxy-game-id",
                game_id,
                "--bloxy-port",
                str(server_port)
            ],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True
        )

        return True, "Server started", proc.pid, server_port

    except Exception as e:
        log_file.close()
        return False, str(e), None, None
    except Exception as e:
        log_file.close()
        return False, str(e), None, None
def update_game_player_count(game_id):
    count = active_game_players.count_documents({"game_id": game_id})

    games_collection.update_one(
        {"game_id": game_id},
        {"$set": {"players": count}}
    )

    return count
BANNED_PATTERNS = re.compile(
    r"(?i)("
    r"\bkys\b|kill yourself|kill urself|\bkms\b|hang yourself|unalive yourself|"
    r"n[i1!l]+[g9q]+[g9q]+[e3a@4]+r?|"
    r"n[i1!l]+[b8]+[b8]+[a@4]+|"
    r"f[a@4]+[g9q]+[g9q]+[o0]+t|"
    r"tr[a@4]+nny|tr[a@4]+nnies|troon|"
    r"k[i1!]+ke|ch[i1!]+nk|g[o0]+[o0]+k|sp[i1!]+c|beaner|wetback|"
    r"\brape\b|\brapist\b|pedo|pedophile|groomer|loli|shota|child porn|"
    r"free robux|free blocks|free money|discord\.gg|click this|visit my site|buy now|"
    r"cunt|whore|slut|motherfucker|fuck you|fuck off|"
    r"nazi|heil|gas chamber"
    r")"
)
def restart_game_servers_from_mongodb():
    print("BLOXY STARTUP: resetting old game server states...")

    games_collection.update_many(
        {
            "multiplayer": True
        },
        {
            "$set": {
                "server_running": False,
                "server_process_pid": None,
                "server_port": None
            }
        }
    )

    approved_multiplayer_games = list(games_collection.find({
        "status": "approved",
        "multiplayer": True
    }))

    print(f"BLOXY STARTUP: found {len(approved_multiplayer_games)} approved multiplayer games")

    for game in approved_multiplayer_games:
        game_id = game.get("game_id", "unknown")
        print(f"BLOXY STARTUP: starting game server for {game_id}")

        ok, msg, pid, port = start_game_server(game)

        if ok:
            games_collection.update_one(
                {"game_id": game_id},
                {
                    "$set": {
                        "server_running": True,
                        "server_process_pid": pid,
                        "server_port": port,
                        "last_started_at": now_iso()
                    }
                }
            )

            print(f"BLOXY STARTUP: {game_id} started on port {port}, pid {pid}")

        else:
            games_collection.update_one(
                {"game_id": game_id},
                {
                    "$set": {
                        "server_running": False,
                        "server_process_pid": None,
                        "server_port": None,
                        "startup_error": msg,
                        "last_start_failed_at": now_iso()
                    }
                }
            )

            print(f"BLOXY STARTUP ERROR: {game_id} failed: {msg}")

def normalize_for_filter(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9@!$]", "", text)
    return text


def is_bad_text(text):
    text = safe_str(text, 250)
    compact = normalize_for_filter(text)
    return bool(BANNED_PATTERNS.search(text) or BANNED_PATTERNS.search(compact))


def clean_message(text):
    text = safe_str(text, 250)

    if not text:
        return ""

    if is_bad_text(text):
        return "#####"

    return text


def is_bad_username(username):
    username = safe_str(username, 20)

    if not username:
        return True

    return is_bad_text(username)


def party_to_json(party):
    return {
        "id": str(party["_id"]),
        "leader": party["leader"],
        "members": party.get("members", []),
        "created_at": party.get("created_at", "")
    }
def is_admin_user(user):
    if not user:
        return False
    return user.get("username") in ADMIN_USERS


def require_admin():
    user = get_current_user()

    if not user:
        return None, redirect("/login")

    if not is_admin_user(user):
        return None, redirect("/home")

    return user, None

def is_banned(username):
    username = safe_str(username, 20)

    if not valid_username(username):
        return True

    return bans.find_one({"username": username}) is not None
def allowed_game_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_GAME_EXTENSIONS


def safe_extract_zip(zip_ref, target_dir):
    target_dir = os.path.abspath(target_dir)

    for member in zip_ref.namelist():
        member_path = os.path.abspath(os.path.join(target_dir, member))

        if not member_path.startswith(target_dir):
            raise Exception("Unsafe zip path detected")

    zip_ref.extractall(target_dir)

def update_game_player_count(game_id):
    count = active_game_players.count_documents({
        "game_id": game_id
    })

    games_collection.update_one(
        {"game_id": game_id},
        {"$set": {"players": count}}
    )

    return count

def validate_uploaded_game(folder):
    manifest_path = os.path.join(folder, "manifest.json")
    client_pck = os.path.join(folder, "client", "client.pck")
    server_pck = os.path.join(folder, "server", "server.pck")

    if not os.path.exists(manifest_path):
        return False, "Missing manifest.json", None

    if not os.path.exists(client_pck):
        return False, "Missing client/client.pck", None

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception:
        return False, "Invalid manifest.json", None

    required = ["title", "description", "creator", "godot_version", "max_players", "multiplayer"]

    for field in required:
        if field not in manifest:
            return False, f"Missing manifest field: {field}", None

    title = safe_str(manifest.get("title"), 60)
    description = safe_str(manifest.get("description"), 500)
    creator = safe_str(manifest.get("creator"), 20)

    if not title:
        return False, "Invalid game title", None

    if not description:
        return False, "Invalid game description", None

    if not valid_username(creator):
        return False, "Invalid creator username in manifest", None

    try:
        max_players = int(manifest.get("max_players"))
    except Exception:
        return False, "max_players must be a number", None

    multiplayer = bool(manifest.get("multiplayer"))

    if multiplayer:
        if max_players < 2 or max_players > 100:
            return False, "Multiplayer max_players must be between 2 and 100", None

        if not os.path.exists(server_pck):
            return False, "Multiplayer games must include server/server.pck", None
    else:
        max_players = 1

    manifest["title"] = title
    manifest["description"] = description
    manifest["creator"] = creator
    manifest["max_players"] = max_players
    manifest["multiplayer"] = multiplayer

    return True, "Valid game", manifest
def is_port_free(port, host="0.0.0.0"):
    if port in BLOCKED_PORTS:
        return False

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)

        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def get_used_game_ports():
    used_ports = set()

    running_games = games_collection.find(
        {
            "server_running": True,
            "server_port": {"$exists": True}
        },
        {
            "_id": 0,
            "server_port": 1
        }
    )

    for game in running_games:
        try:
            used_ports.add(int(game.get("server_port")))
        except Exception:
            pass

    return used_ports


def allocate_game_port():
    used_ports = get_used_game_ports()

    for _ in range(200):
        port = random.randint(GAME_PORT_MIN, GAME_PORT_MAX)

        if port in used_ports:
            continue

        if port in BLOCKED_PORTS:
            continue

        if is_port_free(port):
            return port

    raise Exception("No free game server ports available")
GAME_SERVER_TOKEN = os.environ.get("GAME_SERVER_TOKEN", "dev-game-server-token")

def require_game_server():
    token = request.headers.get("X-Bloxy-Server-Token", "")

    if token != GAME_SERVER_TOKEN:
        return False

    return True
def start_game_server(game):
    server_pck = game.get("server_pck_path")
    game_id = game.get("game_id", "unknown")

    if not server_pck or not os.path.exists(server_pck):
        return False, "server.pck not found", None, None

    try:
        server_port = allocate_game_port()
    except Exception as e:
        return False, str(e), None, None

    os.makedirs("game_logs", exist_ok=True)

    log_path = os.path.join("game_logs", f"{game_id}.log")
    log_file = open(log_path, "a")

    try:
        proc = subprocess.Popen(
            [
                "./godot-server",
                "--headless",
                "--main-pack",
                server_pck,
                "--",
                "--bloxy-game-id",
                game_id,
                "--bloxy-port",
                str(server_port)
            ],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True
        )

        return True, "Server started", proc.pid, server_port

    except Exception as e:
        log_file.close()
        return False, str(e), None, None
def cleanup_inactive_players():
    cutoff = datetime.utcnow() - timedelta(minutes=2)

    stale_players = list(active_game_players.find(
        {"last_seen": {"$lt": cutoff}},
        {"_id": 0, "game_id": 1}
    ))

    affected_game_ids = set()

    for p in stale_players:
        affected_game_ids.add(p.get("game_id"))

    active_game_players.delete_many({
        "last_seen": {"$lt": cutoff}
    })

    for game_id in affected_game_ids:
        if game_id:
            update_game_player_count(game_id)
def create_join_ticket(username, game_id):
    ticket = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=2)

    join_tickets.insert_one({
        "ticket": ticket,
        "username": username,
        "game_id": game_id,
        "created_at": datetime.utcnow(),
        "expires_at": expires_at,
        "used": False
    })

    return ticket
def format_joined_date(value):
    if not value:
        return "Unknown"

    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y")

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).strftime("%b %d, %Y")
        except Exception:
            return value[:10]

    return "Unknown"


def format_last_online(value):
    if not value:
        return "Unknown"

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except Exception:
            return value[:10]

    if not isinstance(value, datetime):
        return "Unknown"

    diff = datetime.utcnow() - value

    if diff.total_seconds() < 60:
        return "Just now"

    if diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds() // 60)} minutes ago"

    if diff.total_seconds() < 86400:
        return f"{int(diff.total_seconds() // 3600)} hours ago"

    return value.strftime("%b %d, %Y")


def game_to_card(game):
    return {
        "id": game.get("game_id"),
        "title": game.get("title", ""),
        "description": game.get("description", ""),
        "players": game.get("players", 0),
        "creator": game.get("creator", ""),
        "icon_url": game.get("icon_url") or "/logo.png",
        "likes": game_likes.count_documents({"game_id": game.get("game_id")}),
        "favorites": game_favorites.count_documents({"game_id": game.get("game_id")})
    }


def get_games_by_ids(game_ids, limit=12):
    clean_ids = []

    for game_id in game_ids:
        game_id = safe_str(game_id, 80)

        if game_id and game_id not in clean_ids:
            clean_ids.append(game_id)

    clean_ids = clean_ids[:limit]

    if not clean_ids:
        return []

    docs = list(
        games_collection.find(
            {
                "game_id": {"$in": clean_ids},
                "status": "approved"
            },
            {"_id": 0}
        )
    )

    game_map = {}

    for game in docs:
        game_map[game.get("game_id")] = game

    games = []

    for game_id in clean_ids:
        game = game_map.get(game_id)

        if game:
            games.append(game_to_card(game))

    return games


def get_favorite_games_for_user(username, limit=12):
    username = safe_str(username, 20)

    if not valid_username(username):
        return []

    fav_docs = list(
        game_favorites.find(
            {"username": username},
            {"_id": 0, "game_id": 1}
        )
        .sort("created_at", -1)
        .limit(limit)
    )

    game_ids = [doc.get("game_id") for doc in fav_docs]

    return get_games_by_ids(game_ids, limit=limit)


def update_user_last_online(username):
    username = safe_str(username, 20)

    if valid_username(username):
        users.update_one(
            {"username": username},
            {"$set": {"last_online": datetime.utcnow()}}
        )
@app.route("/")
def root():
    return render_template("index.html")
def record_recently_played(username, game_id):
    username = safe_str(username, 20)
    game_id = safe_str(game_id, 80)

    if not valid_username(username) or not game_id:
        return

    users.update_one(
        {"username": username},
        {
            "$pull": {
                "recently_played": {
                    "game_id": game_id
                }
            }
        }
    )

    users.update_one(
        {"username": username},
        {
            "$push": {
                "recently_played": {
                    "$each": [
                        {
                            "game_id": game_id,
                            "played_at": datetime.utcnow()
                        }
                    ],
                    "$position": 0,
                    "$slice": 30
                }
            }
        }
    )


def get_recently_played_games_for_user(username, limit=12):
    username = safe_str(username, 20)

    if not valid_username(username):
        return []

    user = users.find_one(
        {"username": username},
        {"_id": 0, "recently_played": 1}
    )

    if not user:
        return []

    recently_played = user.get("recently_played", [])

    if not isinstance(recently_played, list):
        recently_played = []

    game_ids = []

    for item in recently_played:
        if isinstance(item, dict):
            game_id = safe_str(item.get("game_id"), 80)
            if game_id and game_id not in game_ids:
                game_ids.append(game_id)

    game_ids = game_ids[:limit]

    if not game_ids:
        return []

    game_docs = list(
        games_collection.find(
            {
                "game_id": {"$in": game_ids},
                "status": "approved"
            },
            {"_id": 0}
        )
    )

    game_map = {}

    for g in game_docs:
        game_map[g.get("game_id")] = g

    games = []

    for game_id in game_ids:
        g = game_map.get(game_id)

        if not g:
            continue

        games.append({
            "id": g.get("game_id"),
            "title": g.get("title", ""),
            "description": g.get("description", ""),
            "players": g.get("players", 0),
            "creator": g.get("creator", ""),
            "icon_url": g.get("icon_url") or "/logo.png"
        })

    return games
@app.route("/download")
def download_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    return render_template(
        "download.html",
        username=user.get("username", session.get("user", "")),
        blocks=user.get("blocks", 0),
        is_admin=is_admin_user(user)
    )
@app.route("/login")
def login_page():
    if "user" in session:
        return redirect("/home")
    return render_template("signin.html")


@app.route("/signup")
def signup_page():
    if "user" in session:
        return redirect("/home")
    return render_template("signup.html")


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}

    username = safe_str(data.get("username"), 20)
    password = safe_str(data.get("password"), 100)

    if not valid_username(username):
        return jsonify({
            "success": False,
            "error": "Username must be 3-20 characters and only use letters, numbers, and underscores"
        })

    if is_bad_username(username):
        return jsonify({"success": False, "error": "That username is not allowed"})

    if not password:
        return jsonify({"success": False, "error": "Invalid password"})

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

    try:
        users.insert_one({
    "username": username,
    "password_hash": password_hash,
    "blocks": 0,
    "friends": [],
    "pfp": None,
    "bio": "",
    "status": "",
    "badges": ["Early User"],
    "created_at": datetime.utcnow(),
    "last_online": datetime.utcnow(),
    "recently_played": []
})
    except Exception:
        return jsonify({"success": False, "error": "User already exists"})

    return jsonify({"success": True})


@app.route("/api/login", methods=["POST"])
def login():
    ip = request.remote_addr or "unknown"
    login_attempts.setdefault(ip, 0)

    if login_attempts[ip] >= 20:
        return jsonify({
            "success": False,
            "error": "Too many login attempts. Try again later."
        }), 429

    data = request.get_json(silent=True) or {}

    username = safe_str(data.get("username"), 20)
    password = safe_str(data.get("password"), 100)

    if not valid_username(username):
        login_attempts[ip] += 1
        return jsonify({
            "success": False,
            "error": "Invalid login"
        }), 401

    user = users.find_one({"username": username})

    if not user:
        login_attempts[ip] += 1
        return jsonify({
            "success": False,
            "error": "Invalid login"
        }), 401

    if is_banned(username):
        return jsonify({
            "success": False,
            "error": "This account is banned"
        }), 403

    if not bcrypt.checkpw(password.encode(), user["password_hash"]):
        login_attempts[ip] += 1
        return jsonify({
            "success": False,
            "error": "Invalid login"
        }), 401

    login_attempts[ip] = 0
    session["user"] = username

    users.update_one(
        {"username": username},
        {
            "$set": {
                "last_online": datetime.utcnow()
            },
            "$setOnInsert": {
                "bio": "",
                "status": "",
                "badges": ["Early User"],
                "recently_played": []
            }
        }
    )

    return jsonify({
        "success": True
    })
@app.route("/develop")
def develop():
    user = get_current_user()

    if not user:
        return redirect("/login")

    return render_template(
        "develop.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp")
    )

@app.route("/home")
def home():
    user = get_current_user()

    if not user:
        return redirect("/login")

    cleanup_inactive_players()

    friend_usernames = user.get("friends", [])
    friends_data = []

    for friend_name in friend_usernames:
        friend_name = safe_str(friend_name, 20)

        if valid_username(friend_name):
            friend_user = users.find_one(
                {"username": friend_name},
                {
                    "_id": 0,
                    "username": 1,
                    "pfp": 1,
                    "settings": 1,
                    "privacy": 1
                }
            )

            if friend_user:
                friend_show_online = user_allows_online_status(friend_user)

                friends_data.append({
                    "username": friend_user["username"],
                    "pfp": friend_user.get("pfp"),
                    "online": friend_show_online and friend_user["username"] in online_usernames
                })

    games = get_recently_played_games_for_user(user["username"], limit=12)

    return render_template(
        "home.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp"),
        friends=friends_data,
        games=games,
        is_admin=is_admin_user(user)
    )

@app.route("/discover")
def discover_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    sort = safe_str(request.args.get("sort"), 30)

    valid_sorts = [
        "most_liked",
        "favorites",
        "most_comments",
        "most_players",
        "newest"
    ]

    if sort not in valid_sorts:
        sort = "most_liked"

    title_map = {
        "most_liked": "Most Liked Games",
        "favorites": "Your Favorites",
        "most_comments": "Most Commented Games",
        "most_players": "Most Concurrent Players",
        "newest": "Newest Games"
    }

    games = []

    if sort == "favorites":
        fav_docs = list(
            game_favorites.find(
                {"username": user["username"]},
                {"_id": 0, "game_id": 1}
            )
            .sort("created_at", -1)
            .limit(100)
        )

        game_ids = []

        for fav in fav_docs:
            game_id = safe_str(fav.get("game_id"), 80)

            if game_id and game_id not in game_ids:
                game_ids.append(game_id)

        games = get_games_by_ids(game_ids, limit=100)

    elif sort == "newest":
        game_docs = list(
            games_collection.find(
                {"status": "approved"},
                {"_id": 0}
            )
            .sort("created_at", -1)
            .limit(100)
        )

        for game in game_docs:
            games.append(game_to_card(game))

    elif sort == "most_players":
        game_docs = list(
            games_collection.find(
                {"status": "approved"},
                {"_id": 0}
            )
            .sort("players", -1)
            .limit(100)
        )

        for game in game_docs:
            games.append(game_to_card(game))

    elif sort == "most_liked":
        pipeline = [
            {
                "$group": {
                    "_id": "$game_id",
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {
                    "count": -1
                }
            },
            {
                "$limit": 100
            }
        ]

        ranked = list(game_likes.aggregate(pipeline))
        game_ids = [safe_str(row.get("_id"), 80) for row in ranked]
        games = get_games_by_ids(game_ids, limit=100)

    elif sort == "most_comments":
        pipeline = [
            {
                "$group": {
                    "_id": "$game_id",
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {
                    "count": -1
                }
            },
            {
                "$limit": 100
            }
        ]

        ranked = list(game_comments.aggregate(pipeline))
        game_ids = [safe_str(row.get("_id"), 80) for row in ranked]
        games = get_games_by_ids(game_ids, limit=100)

    return render_template(
        "discover.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp"),
        games=games,
        sort=sort,
        page_title=title_map.get(sort, "Discover Games"),
        is_admin=is_admin_user(user)
    )

@app.route("/profile/<profile_username>")
def profile_page(profile_username):
    user = get_current_user()

    if not user:
        return redirect("/login")

    update_user_last_online(user["username"])

    profile_username = safe_str(profile_username, 20)

    if not valid_username(profile_username):
        return "Invalid profile", 404

    profile_user = users.find_one(
        {"username": profile_username},
        {
            "_id": 0,
            "username": 1,
            "pfp": 1,
            "blocks": 1,
            "friends": 1,
            "created_at": 1,
            "privacy": 1,
            "recently_played": 1,
            "bio": 1,
            "status": 1,
            "badges": 1,
            "last_online": 1
        }
    )

    if not profile_user:
        return "Profile not found", 404

    created_games_docs = list(
        games_collection.find(
            {
                "creator": profile_username,
                "status": "approved"
            },
            {"_id": 0}
        )
        .sort("created_at", -1)
        .limit(12)
    )

    created_games = []

    for game in created_games_docs:
        created_games.append(game_to_card(game))

    friends = profile_user.get("friends", [])

    if not isinstance(friends, list):
        friends = []

    friends_preview = []

    for friend_name in friends[:12]:
        friend_name = safe_str(friend_name, 20)

        if not valid_username(friend_name):
            continue

        friend_user = users.find_one(
            {"username": friend_name},
            {
                "_id": 0,
                "username": 1,
                "pfp": 1
            }
        )

        if friend_user:
            friends_preview.append({
                "username": friend_user.get("username"),
                "pfp": friend_user.get("pfp")
            })

    profile_recent_games = get_recently_played_games_for_user(profile_username, limit=12)
    favorite_games = get_favorite_games_for_user(profile_username, limit=12)

    badges = profile_user.get("badges", [])

    if not isinstance(badges, list):
        badges = []

    is_own_profile = profile_username == user["username"]
    is_friend = profile_username in user.get("friends", [])

    pending_request = False

    if not is_own_profile:
        pending_request = friend_requests.find_one({
            "$or": [
                {
                    "from": user["username"],
                    "to": profile_username,
                    "status": "pending"
                },
                {
                    "from": profile_username,
                    "to": user["username"],
                    "status": "pending"
                }
            ]
        }) is not None

    return render_template(
        "profile.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        profile=profile_user,
        profile_blocks=profile_user.get("blocks", 0),
        friend_count=len(friends),
        friends_preview=friends_preview,
        created_games=created_games,
        profile_recent_games=profile_recent_games,
        favorite_games=favorite_games,
        badges=badges,
        joined_date=format_joined_date(profile_user.get("created_at")),
        last_online_text=format_last_online(profile_user.get("last_online")),
        is_own_profile=is_own_profile,
        is_friend=is_friend,
        pending_request=pending_request,
        is_admin=is_admin_user(user)
    )
@app.route("/search")
def search_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    q = safe_str(request.args.get("q"), 80)
    search_type = safe_str(request.args.get("type"), 20)

    if search_type not in ["games", "accounts"]:
        search_type = "games"

    games = []
    accounts = []

    if q:
        safe_q = re.escape(q)

        if search_type == "games":
            game_docs = list(
                games_collection.find(
                    {
                        "$and": [
                            {"status": "approved"},
                            {
                                "$or": [
                                    {"title": {"$regex": safe_q, "$options": "i"}},
                                    {"description": {"$regex": safe_q, "$options": "i"}},
                                    {"creator": {"$regex": safe_q, "$options": "i"}}
                                ]
                            }
                        ]
                    },
                    {"_id": 0}
                )
                .sort("created_at", -1)
                .limit(50)
            )

            for g in game_docs:
                games.append({
                    "id": g.get("game_id"),
                    "title": g.get("title", ""),
                    "description": g.get("description", ""),
                    "players": g.get("players", 0),
                    "creator": g.get("creator", ""),
                    "icon_url": g.get("icon_url") or "/logo.png"
                })

        if search_type == "accounts":
            account_docs = list(
                users.find(
                    {
                        "$and": [
                            {"username": {"$regex": safe_q, "$options": "i"}},
                            {"username": {"$ne": user["username"]}}
                        ]
                    },
                    {"_id": 0, "username": 1, "pfp": 1, "blocks": 1, "friends": 1}
                )
                .limit(50)
            )

            for account in account_docs:
                accounts.append({
                    "username": account.get("username", ""),
                    "pfp": account.get("pfp"),
                    "blocks": account.get("blocks", 0),
                    "friends_count": len(account.get("friends", []))
                })

    return render_template(
        "search.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp"),
        games=games,
        accounts=accounts,
        query=q,
        search_type=search_type,
        is_admin=is_admin_user(user)
    )
@app.route("/api/games/<game_id>/join", methods=["POST"])
def game_player_join(game_id):
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Not logged in"
        }), 401

    data = request.get_json(silent=True) or {}
    ticket = safe_str(data.get("ticket"), 200)

    game_id = safe_str(game_id, 80)

    if not game_id or not ticket:
        return jsonify({
            "success": False,
            "error": "Missing game id or ticket"
        }), 400

    game = games_collection.find_one({
        "game_id": game_id,
        "status": "approved"
    })

    if not game:
        return jsonify({
            "success": False,
            "error": "Game not found or not approved"
        }), 404

    ticket_doc = join_tickets.find_one({
        "ticket": ticket,
        "game_id": game_id,
        "username": user["username"]
    })

    if not ticket_doc:
        return jsonify({
            "success": False,
            "error": "Invalid Bloxy ticket"
        }), 403

    expires_at = ticket_doc.get("expires_at")

    if not expires_at or expires_at < datetime.utcnow():
        return jsonify({
            "success": False,
            "error": "Ticket expired"
        }), 403

    username = user["username"]
    now_dt = datetime.utcnow()

    active_game_players.update_one(
        {
            "game_id": game_id,
            "username": username
        },
        {
            "$set": {
                "game_id": game_id,
                "username": username,
                "last_seen": now_dt
            },
            "$setOnInsert": {
                "joined_at": now_dt
            }
        },
        upsert=True
    )

    count = update_game_player_count(game_id)

    return jsonify({
        "success": True,
        "game_id": game_id,
        "username": username,
        "players": count
    })
@app.route("/api/games/<game_id>/play")
def game_play_info(game_id):
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Not logged in"
        }), 401

    game_id = safe_str(game_id, 80)

    if not game_id:
        return jsonify({
            "success": False,
            "error": "Invalid game id"
        }), 400

    game = games_collection.find_one({
        "game_id": game_id,
        "status": "approved"
    })

    if not game:
        return jsonify({
            "success": False,
            "error": "Game not found or not approved"
        }), 404

    if is_banned(user["username"]):
        return jsonify({
            "success": False,
            "error": "This account is banned"
        }), 403

    record_recently_played(user["username"], game["game_id"])

    join_ticket = create_join_ticket(user["username"], game["game_id"])

    if not game.get("multiplayer"):
        return jsonify({
            "success": True,
            "game_id": game["game_id"],
            "title": game.get("title", ""),
            "multiplayer": False,
            "client_pck_url": f"/api/games/{game_id}/client.pck",
            "join_ticket": join_ticket
        })

    server_running = game.get("server_running", False)
    server_port = game.get("server_port")
    server_pid = game.get("server_process_pid")

    server_dead = False

    if server_pid:
        try:
            os.kill(int(server_pid), 0)
        except Exception:
            server_dead = True
    else:
        server_dead = True

    if (
        not server_running
        or not server_port
        or not server_pid
        or server_dead
    ):
        ok, msg, pid, port = start_game_server(game)

        if not ok:
            games_collection.update_one(
                {"game_id": game_id},
                {
                    "$set": {
                        "server_running": False,
                        "server_process_pid": None,
                        "server_port": None,
                        "last_start_failed_at": now_iso(),
                        "startup_error": msg
                    }
                }
            )

            return jsonify({
                "success": False,
                "error": msg
            }), 500

        games_collection.update_one(
            {"game_id": game_id},
            {
                "$set": {
                    "server_running": True,
                    "server_process_pid": pid,
                    "server_port": port,
                    "last_started_at": now_iso(),
                    "startup_error": None
                }
            }
        )

        server_pid = pid
        server_port = port

    return jsonify({
        "success": True,
        "game_id": game["game_id"],
        "title": game.get("title", ""),
        "multiplayer": True,
        "client_pck_url": f"/api/games/{game_id}/client.pck",

        "server_ip": "127.0.0.1",

        "server_port": server_port,
        "server_pid": server_pid,
        "join_ticket": join_ticket
    })
@app.route("/api/game-server/verify-ticket", methods=["POST"])
def verify_join_ticket():
    if not require_game_server():
        return jsonify({
            "success": False,
            "error": "Invalid game server token"
        }), 403

    data = request.get_json(silent=True) or {}

    ticket = safe_str(data.get("ticket"), 200)
    game_id = safe_str(data.get("game_id"), 80)
    server_id = safe_str(data.get("server_id"), 120)
    peer_id = safe_str(str(data.get("peer_id")), 80)

    if not ticket or not game_id or not server_id or not peer_id:
        return jsonify({
            "success": False,
            "error": "Missing ticket, game_id, server_id, or peer_id"
        }), 400

    ticket_doc = join_tickets.find_one({
        "ticket": ticket,
        "game_id": game_id,
        "used": False
    })

    if not ticket_doc:
        return jsonify({
            "success": False,
            "error": "Invalid ticket"
        }), 403

    expires_at = ticket_doc.get("expires_at")

    if not expires_at or expires_at < datetime.utcnow():
        return jsonify({
            "success": False,
            "error": "Ticket expired"
        }), 403

    username = ticket_doc.get("username")

    user = users.find_one(
        {"username": username},
        {"_id": 0, "username": 1, "avatar": 1}
    )

    if not user or is_banned(username):
        return jsonify({
            "success": False,
            "error": "User banned or invalid"
        }), 403

    now_dt = datetime.utcnow()

    join_tickets.update_one(
        {"ticket": ticket},
        {
            "$set": {
                "used": True,
                "used_at": now_dt
            }
        }
    )

    active_game_players.update_one(
        {
            "game_id": game_id,
            "server_id": server_id,
            "peer_id": peer_id
        },
        {
            "$set": {
                "game_id": game_id,
                "server_id": server_id,
                "peer_id": peer_id,
                "username": username,
                "last_seen": now_dt
            },
            "$setOnInsert": {
                "joined_at": now_dt
            }
        },
        upsert=True
    )

    count = update_game_player_count(game_id)

    avatar = user.get("avatar") or {
        "skin_color": "#c68642",
        "shirt_color": "#1f6fff",
        "pants_color": "#111827",
        "face_id": "smile"
    }

    return jsonify({
        "success": True,
        "username": username,
        "game_id": game_id,
        "avatar": avatar,
        "players": count
    })
@app.route("/api/game-server/player-left", methods=["POST"])

def game_server_player_left():
    if not require_game_server():
        return jsonify({
            "success": False,
            "error": "Invalid game server token"
        }), 403

    data = request.get_json(silent=True) or {}

    game_id = safe_str(data.get("game_id"), 80)
    server_id = safe_str(data.get("server_id"), 120)
    peer_id = safe_str(str(data.get("peer_id")), 80)

    if not game_id or not server_id or not peer_id:
        return jsonify({
            "success": False,
            "error": "Missing game_id, server_id, or peer_id"
        }), 400

    active_game_players.delete_one({
        "game_id": game_id,
        "server_id": server_id,
        "peer_id": peer_id
    })

    count = update_game_player_count(game_id)

    return jsonify({
        "success": True,
        "players": count
    })
@app.route("/api/games/<game_id>/leave", methods=["POST"])
def game_player_leave(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    active_game_players.delete_one({
        "game_id": game_id,
        "username": user["username"]
    })

    count = update_game_player_count(game_id)

    return jsonify({
        "success": True,
        "players": count
    })
@app.route("/api/games/<game_id>/heartbeat", methods=["POST"])
def game_player_heartbeat(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    result = active_game_players.update_one(
        {
            "game_id": game_id,
            "username": user["username"]
        },
        {
            "$set": {
                "last_seen": datetime.utcnow()
            }
        }
    )

    if result.matched_count == 0:
        return jsonify({"success": False, "error": "Player not joined"}), 404

    count = update_game_player_count(game_id)

    return jsonify({
        "success": True,
        "players": count
    })
@app.route("/api/game-server/heartbeat", methods=["POST"])
def game_server_heartbeat():
    if not require_game_server():
        return jsonify({
            "success": False,
            "error": "Invalid game server token"
        }), 403

    data = request.get_json(silent=True) or {}

    game_id = safe_str(data.get("game_id"), 80)
    server_id = safe_str(data.get("server_id"), 120)

    if not game_id or not server_id:
        return jsonify({
            "success": False,
            "error": "Missing game_id or server_id"
        }), 400

    active_game_players.update_many(
        {
            "game_id": game_id,
            "server_id": server_id
        },
        {
            "$set": {
                "last_seen": datetime.utcnow()
            }
        }
    )

    count = update_game_player_count(game_id)

    return jsonify({
        "success": True,
        "players": count
    })
@app.route("/chat")
def chat_page():
    user = get_current_user()
    if not user:
        return redirect("/login")

    incoming = list(friend_requests.find(
        {"to": user["username"], "status": "pending"},
        {"_id": 0, "from": 1}
    ))

    outgoing = list(friend_requests.find(
        {"from": user["username"], "status": "pending"},
        {"_id": 0, "to": 1}
    ))

    saved_parties = list(parties.find(
        {"members": user["username"]}
    ).sort("created_at", -1))

    privacy = user.get("privacy", {
        "friend_requests": "everyone",
        "messages": "friends",
        "show_online": True
    })

    return render_template(
        "chat.html",
        username=user["username"],
        pfp=user.get("pfp"),
        friends=user.get("friends", []),
        incoming_requests=incoming,
        outgoing_requests=outgoing,
        parties=[party_to_json(p) for p in saved_parties],
        is_admin=is_admin_user(user),
        privacy=privacy
    )
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/upload-game")
def upload_game_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    return render_template(
        "upload-game.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        is_admin=is_admin_user(user)
    )
@app.route("/api/games/upload", methods=["POST"])
def upload_game():
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    if "game_zip" not in request.files:
        return jsonify({"success": False, "error": "No game zip uploaded"}), 400

    file = request.files["game_zip"]

    if file.filename == "":
        return jsonify({"success": False, "error": "No selected file"}), 400

    if not allowed_game_file(file.filename):
        return jsonify({"success": False, "error": "Only .zip game uploads are allowed"}), 400

    game_id = str(uuid.uuid4())
    original_filename = secure_filename(file.filename)

    zip_path = os.path.join(GAME_UPLOAD_DIR, f"{game_id}.zip")
    extract_path = os.path.join(GAME_EXTRACT_DIR, game_id)

    file.save(zip_path)

    try:
        os.makedirs(extract_path, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            safe_extract_zip(zip_ref, extract_path)

        valid, message, manifest = validate_uploaded_game(extract_path)

        if not valid:
            shutil.rmtree(extract_path, ignore_errors=True)
            os.remove(zip_path)
            return jsonify({"success": False, "error": message}), 400

        is_multiplayer = manifest["multiplayer"]

        game_doc = {
            "game_id": game_id,
            "title": manifest["title"],
            "description": manifest["description"],
            "creator": user["username"],
            "manifest_creator": manifest["creator"],
            "godot_version": manifest["godot_version"],
            "max_players": manifest["max_players"],
            "multiplayer": is_multiplayer,
            "status": "pending",
            "players": 0,
            "server_running": False,
            "server_process_pid": None,
            "server_port": None,
            "zip_path": zip_path,
            "extract_path": extract_path,
            "client_pck_path": os.path.join(extract_path, "client", "client.pck"),
            "server_pck_path": os.path.join(extract_path, "server", "server.pck") if is_multiplayer else None,
            "original_filename": original_filename,
            "created_at": now_iso(),
            "approved_at": None,
            "approved_by": None
        }
        games_collection.insert_one(game_doc)

        return jsonify({
            "success": True,
            "message": "Game uploaded and sent for admin approval",
            "game_id": game_id
        })

    except zipfile.BadZipFile:
        shutil.rmtree(extract_path, ignore_errors=True)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        return jsonify({"success": False, "error": "Invalid zip file"}), 400

    except Exception as e:
        shutil.rmtree(extract_path, ignore_errors=True)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        return jsonify({"success": False, "error": str(e)}), 500
    
@app.route("/game-icons/<filename>")
def serve_game_icon(filename):
    filename = secure_filename(filename)
    return send_from_directory(GAME_ICON_DIR, filename)
@app.route("/api/games/<game_id>/icon", methods=["POST"])
def upload_game_icon(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({"game_id": game_id})

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    if game.get("creator") != user["username"]:
        return jsonify({"success": False, "error": "You can only edit your own games"}), 403

    if "icon" not in request.files:
        return jsonify({"success": False, "error": "No icon uploaded"}), 400

    file = request.files["icon"]

    if file.filename == "":
        return jsonify({"success": False, "error": "No selected file"}), 400

    if not allowed_game_icon(file.filename):
        return jsonify({"success": False, "error": "Only png, jpg, jpeg, and webp icons are allowed"}), 400

    filename = secure_filename(file.filename)
    ext = filename.rsplit(".", 1)[1].lower()

    final_name = f"{game_id}_icon.{ext}"
    icon_path = os.path.join(GAME_ICON_DIR, final_name)

    old_icon = game.get("icon")

    if old_icon and old_icon != final_name:
        old_path = os.path.join(GAME_ICON_DIR, secure_filename(old_icon))

        if os.path.exists(old_path):
            os.remove(old_path)

    file.save(icon_path)

    games_collection.update_one(
        {"game_id": game_id},
        {
            "$set": {
                "icon": final_name,
                "icon_url": f"/game-icons/{final_name}",
                "updated_at": now_iso()
            }
        }
    )

    return jsonify({
        "success": True,
        "icon_url": f"/game-icons/{final_name}"
    })
@app.route("/api/games/<game_id>/edit-info", methods=["POST"])
def edit_game_info(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({"game_id": game_id})

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    if game.get("creator") != user["username"]:
        return jsonify({"success": False, "error": "You can only edit your own games"}), 403

    data = request.get_json(silent=True) or {}

    title = safe_str(data.get("title"), 60)
    description = safe_str(data.get("description"), 500)

    if len(title) < 3:
        return jsonify({"success": False, "error": "Title must be at least 3 characters"}), 400

    if len(description) < 10:
        return jsonify({"success": False, "error": "Description must be at least 10 characters"}), 400

    games_collection.update_one(
        {"game_id": game_id},
        {
            "$set": {
                "title": title,
                "description": description,
                "updated_at": now_iso()
            }
        }
    )

    return jsonify({
        "success": True,
        "message": "Game info updated"
    })

@app.route("/pfp/user/<username>")
def serve_user_pfp(username):
    username = safe_str(username, 20)

    if not valid_username(username):
        return send_from_directory(".", "logo.png")

    user = users.find_one({"username": username})

    if not user or not user.get("pfp"):
        return send_from_directory(".", "logo.png")

    response = send_from_directory(UPLOAD_FOLDER, user["pfp"])
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/pfp/<filename>")
def serve_pfp(filename):
    filename = secure_filename(filename)
    return send_from_directory(UPLOAD_FOLDER, filename)


@app.route("/api/pfp/upload", methods=["POST"])
def upload_pfp():
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"success": False, "error": "No filename"}), 400

    if not allowed_file(file.filename):
        return jsonify({"success": False, "error": "Invalid file type"}), 400

    filename = secure_filename(file.filename)
    ext = filename.rsplit(".", 1)[1].lower()
    final_name = f"{user['username']}_pfp.{ext}"

    path = os.path.join(UPLOAD_FOLDER, final_name)
    file.save(path)

    users.update_one(
        {"username": user["username"]},
        {"$set": {"pfp": final_name}}
    )

    return jsonify({"success": True, "pfp": final_name})


@app.route("/api/users/search")
def search_users():
    current = get_current_user()
    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    q = safe_str(request.args.get("q"), 20)

    if not q:
        return jsonify({"success": True, "users": []})

    safe_q = re.escape(q)

    found = list(users.find(
        {
            "$and": [
                {"username": {"$regex": safe_q, "$options": "i"}},
                {"username": {"$ne": current["username"]}}
            ]
        },
        {"_id": 0, "username": 1}
    ).limit(10))

    return jsonify({"success": True, "users": found})


@app.route("/api/friends/request", methods=["POST"])
def send_friend_request():
    current = get_current_user()
    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    receiver = safe_str(data.get("username"), 20)

    if not valid_username(receiver):
        return jsonify({"success": False, "error": "Invalid username"})

    if receiver == current["username"]:
        return jsonify({"success": False, "error": "You cannot add yourself"})

    receiver_user = users.find_one({"username": receiver})

    if not receiver_user:
        return jsonify({"success": False, "error": "User does not exist"})

    receiver_privacy = receiver_user.get("privacy", {})
    receiver_friend_setting = receiver_privacy.get("friend_requests", "everyone")

    if receiver_friend_setting == "no_one":
        return jsonify({
            "success": False,
            "error": "This user is not accepting friend requests"
        })

    if are_friends(current["username"], receiver):
        return jsonify({"success": False, "error": "Already friends"})

    existing = friend_requests.find_one({
        "$or": [
            {"from": current["username"], "to": receiver, "status": "pending"},
            {"from": receiver, "to": current["username"], "status": "pending"}
        ]
    })

    if existing:
        return jsonify({"success": False, "error": "Request already pending"})

    friend_requests.insert_one({
        "from": current["username"],
        "to": receiver,
        "status": "pending",
        "created_at": now_iso()
    })

    return jsonify({"success": True})

@app.route("/api/friends/respond", methods=["POST"])
def respond_friend_request():
    current = get_current_user()
    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    sender = safe_str(data.get("username"), 20)
    action = safe_str(data.get("action"), 10)

    if not valid_username(sender) or action not in ["accept", "reject"]:
        return jsonify({"success": False, "error": "Invalid request"})

    req = friend_requests.find_one({
        "from": sender,
        "to": current["username"],
        "status": "pending"
    })

    if not req:
        return jsonify({"success": False, "error": "Request not found"})

    if action == "accept":
        users.update_one({"username": current["username"]}, {"$addToSet": {"friends": sender}})
        users.update_one({"username": sender}, {"$addToSet": {"friends": current["username"]}})

    friend_requests.update_one(
        {"from": sender, "to": current["username"], "status": "pending"},
        {"$set": {"status": action, "responded_at": now_iso()}}
    )

    return jsonify({"success": True})


@app.route("/api/friends/remove", methods=["POST"])
def remove_friend():
    current = get_current_user()
    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    friend = safe_str(data.get("username"), 20)

    if not valid_username(friend):
        return jsonify({"success": False, "error": "Invalid username"})

    users.update_one({"username": current["username"]}, {"$pull": {"friends": friend}})
    users.update_one({"username": friend}, {"$pull": {"friends": current["username"]}})

    return jsonify({"success": True})

@app.route("/api/chat/history/<room>")
def chat_history(room):
    current = get_current_user()

    if not current:
        return jsonify({"error": "Not logged in"}), 401

    room = valid_room(room)

    current_privacy = current.get("privacy", {})
    current_messages_setting = current_privacy.get("messages", "friends")

    if current_messages_setting == "no_one":
        return jsonify({
            "error": "Your messages are turned off. Enable messages in Settings to view chat."
        }), 403

    if room.startswith("dm_"):
        parts = room.replace("dm_", "").split("_")

        if len(parts) != 2:
            return jsonify({"error": "Invalid DM room"}), 400

        if current["username"] not in parts:
            return jsonify({"error": "Forbidden"}), 403

        other = parts[0] if parts[1] == current["username"] else parts[1]

        if not valid_username(other):
            return jsonify({"error": "Invalid user"}), 400

        if not are_friends(current["username"], other):
            return jsonify({"error": "You can only DM friends"}), 403

        other_user = users.find_one({"username": other})

        if not other_user:
            return jsonify({"error": "User not found"}), 404

        other_privacy = other_user.get("privacy", {})
        other_messages_setting = other_privacy.get("messages", "friends")

        if other_messages_setting == "no_one":
            return jsonify({"error": "This user is not accepting messages"}), 403

    if room.startswith("party_"):
        pid = room.replace("party_", "")

        if not ObjectId.is_valid(pid):
            return jsonify({"error": "Invalid party"}), 400

        party = parties.find_one({
            "_id": ObjectId(pid),
            "members": current["username"]
        })

        if not party:
            return jsonify({"error": "Party not found or forbidden"}), 403

    history = list(messages.find(
        {"room": room},
        {"_id": 0}
    ).sort("created_at", -1).limit(50))

    history.reverse()

    return jsonify(history)

@app.route("/api/party/create", methods=["POST"])
def create_party():
    current = get_current_user()
    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    members = data.get("members", [])

    if not isinstance(members, list):
        members = []

    clean_members = []

    for member in members:
        member = safe_str(member, 20)
        if valid_username(member) and are_friends(current["username"], member):
            clean_members.append(member)

    party = {
        "leader": current["username"],
        "members": list(set([current["username"]] + clean_members)),
        "created_at": now_iso()
    }

    result = parties.insert_one(party.copy())

    return jsonify({
        "success": True,
        "party": {
            "id": str(result.inserted_id),
            "leader": party["leader"],
            "members": party["members"],
            "created_at": party["created_at"]
        }
    })
@app.route("/develop")
@app.route("/develop")

@app.route("/api/party/add", methods=["POST"])
def add_to_party():
    current = get_current_user()
    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    party_id = safe_str(data.get("party_id"), 30)
    new_member = safe_str(data.get("username"), 20)

    if not ObjectId.is_valid(party_id):
        return jsonify({"success": False, "error": "Invalid party id"})

    if not valid_username(new_member):
        return jsonify({"success": False, "error": "Invalid username"})

    party = parties.find_one({"_id": ObjectId(party_id)})

    if not party:
        return jsonify({"success": False, "error": "Party not found"})

    if current["username"] not in party.get("members", []):
        return jsonify({"success": False, "error": "You are not in this party"})

    if new_member in party.get("members", []):
        return jsonify({"success": False, "error": "User already in party"})

    if not are_friends(current["username"], new_member):
        return jsonify({"success": False, "error": "You can only add your friends"})

    users.update_one({"username": new_member}, {"$setOnInsert": {"username": new_member}})
    if not users.find_one({"username": new_member}):
        return jsonify({"success": False, "error": "User does not exist"})

    parties.update_one(
        {"_id": ObjectId(party_id)},
        {"$addToSet": {"members": new_member}}
    )

    room = "party_" + party_id
    system_msg = {
        "room": room,
        "username": "System",
        "message": f"{new_member} was added to the party.",
        "created_at": datetime.utcnow().isoformat()
    }

    messages.insert_one(system_msg.copy())
    emit("new_message", system_msg, room=room, namespace="/")

    return jsonify({"success": True})


@app.route("/api/party/delete", methods=["POST"])
def delete_party():
    current = get_current_user()
    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    party_id = safe_str(data.get("party_id"), 30)

    if not ObjectId.is_valid(party_id):
        return jsonify({"success": False, "error": "Invalid party id"})

    party = parties.find_one({"_id": ObjectId(party_id)})

    if not party:
        return jsonify({"success": False, "error": "Party not found"})

    if party.get("leader") != current["username"]:
        return jsonify({"success": False, "error": "Only party leader can delete this party"})

    room = "party_" + party_id

    parties.delete_one({"_id": ObjectId(party_id)})
    messages.delete_many({"room": room})

    return jsonify({"success": True})

@app.route("/game/<game_id>")
def game_page(game_id):
    user = get_current_user()

    if not user:
        return redirect("/login")

    update_user_last_online(user["username"])

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({
        "game_id": game_id,
        "status": "approved"
    })

    if not game:
        return "Game not found or not approved", 404

    game["icon_url"] = game.get("icon_url") or "/logo.png"

    like_count = game_likes.count_documents({"game_id": game_id})
    favorite_count = game_favorites.count_documents({"game_id": game_id})

    liked_by_me = game_likes.find_one({
        "game_id": game_id,
        "username": user["username"]
    }) is not None

    favorited_by_me = game_favorites.find_one({
        "game_id": game_id,
        "username": user["username"]
    }) is not None

    comment_docs = list(
        game_comments.find(
            {"game_id": game_id},
            {"_id": 0}
        )
        .sort("created_at", -1)
        .limit(50)
    )

    comments = []

    for comment in comment_docs:
        comments.append({
            "username": comment.get("username", ""),
            "text": comment.get("text", ""),
            "created_at": format_joined_date(comment.get("created_at"))
        })

    return render_template(
        "game.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        game=game,
        is_admin=is_admin_user(user),
        like_count=like_count,
        favorite_count=favorite_count,
        liked_by_me=liked_by_me,
        favorited_by_me=favorited_by_me,
        comments=comments
    )
@app.route("/api/games/<game_id>/client.pck")
def download_client_pck(game_id):
    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({
        "game_id": game_id,
        "status": "approved"
    })

    if not game:
        return "Game not found or not approved", 404

    client_pck_path = game.get("client_pck_path")

    if not client_pck_path or not os.path.exists(client_pck_path):
        return "client.pck not found", 404

    folder = os.path.dirname(client_pck_path)
    filename = os.path.basename(client_pck_path)

    return send_from_directory(folder, filename, as_attachment=True)
@app.route("/api/games/<game_id>/like", methods=["POST"])
def toggle_game_like(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({
        "game_id": game_id,
        "status": "approved"
    })

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    existing = game_likes.find_one({
        "game_id": game_id,
        "username": user["username"]
    })

    if existing:
        game_likes.delete_one({
            "game_id": game_id,
            "username": user["username"]
        })

        liked = False
    else:
        game_likes.insert_one({
            "game_id": game_id,
            "username": user["username"],
            "created_at": datetime.utcnow()
        })

        liked = True

    count = game_likes.count_documents({"game_id": game_id})

    return jsonify({
        "success": True,
        "liked": liked,
        "count": count
    })


@app.route("/api/games/<game_id>/favorite", methods=["POST"])
def toggle_game_favorite(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({
        "game_id": game_id,
        "status": "approved"
    })

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    existing = game_favorites.find_one({
        "game_id": game_id,
        "username": user["username"]
    })

    if existing:
        game_favorites.delete_one({
            "game_id": game_id,
            "username": user["username"]
        })

        favorited = False
    else:
        game_favorites.insert_one({
            "game_id": game_id,
            "username": user["username"],
            "created_at": datetime.utcnow()
        })

        favorited = True

    count = game_favorites.count_documents({"game_id": game_id})

    return jsonify({
        "success": True,
        "favorited": favorited,
        "count": count
    })


@app.route("/api/games/<game_id>/comments", methods=["POST"])
def add_game_comment(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({
        "game_id": game_id,
        "status": "approved"
    })

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    data = request.get_json(silent=True) or {}

    text = safe_str(data.get("text"), 500)

    if len(text) < 1:
        return jsonify({"success": False, "error": "Comment cannot be empty"}), 400

    if is_bad_text(text):
        return jsonify({"success": False, "error": "Comment contains blocked text"}), 400

    game_comments.insert_one({
        "game_id": game_id,
        "username": user["username"],
        "text": text,
        "created_at": datetime.utcnow()
    })

    return jsonify({"success": True})
@socketio.on("connect")
def on_connect():
    current = get_current_user()

    if not current:
        return False

    username = current["username"]
    show_online = user_allows_online_status(current)

    online_users[request.sid] = {
        "username": username,
        "show_online": show_online
    }

    if show_online:
        online_usernames.add(username)

    socketio.emit("online_status_update", {
        "username": username,
        "online": show_online
    })


@socketio.on("disconnect")
def on_disconnect():
    data = online_users.pop(request.sid, None)

    if not data:
        return

    username = data.get("username")

    still_visible_online = False

    for connection in online_users.values():
        if (
            connection.get("username") == username
            and connection.get("show_online") is True
        ):
            still_visible_online = True
            break

    if not still_visible_online:
        online_usernames.discard(username)

        socketio.emit("online_status_update", {
            "username": username,
            "online": False
        })

@socketio.on("join_chat")
def join_chat(data):
    current = get_current_user()
    if not current:
        return

    if not isinstance(data, dict):
        data = {}

    room = valid_room(data.get("room", "global"))
    username = current["username"]

    if room.startswith("dm_"):
        parts = room.replace("dm_", "").split("_")
        other = parts[0] if parts[1] == username else parts[1]

        if username not in parts or not are_friends(username, other):
            emit("error_message", {"message": "Forbidden DM room."})
            return

    if room.startswith("party_"):
        pid = room.replace("party_", "")

        if not parties.find_one({"_id": ObjectId(pid), "members": username}):
            emit("error_message", {"message": "Forbidden party."})
            return

    join_room(room)

@app.route("/blocks")
def blocks_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    return render_template(
        "blocks.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        packs=BLOCK_PACKS
    )
@app.route("/api/admin/tickets/reply", methods=["POST"])
def admin_reply_ticket():
    admin, response = require_admin()

    if response:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}

    ticket_id = safe_str(data.get("ticket_id"), 40)
    reply = safe_str(data.get("reply"), 2000)
    status = safe_str(data.get("status"), 20)

    if not ObjectId.is_valid(ticket_id):
        return jsonify({"success": False, "error": "Invalid ticket ID"})

    if len(reply) < 1:
        return jsonify({"success": False, "error": "Reply cannot be empty"})

    if status not in ["open", "answered", "closed"]:
        return jsonify({"success": False, "error": "Invalid status"})

    result = support_tickets.update_one(
        {"_id": ObjectId(ticket_id)},
        {
            "$set": {
                "admin_reply": reply,
                "admin_replied_at": now_iso(),
                "status": status,
                "answered_by": admin["username"]
            }
        }
    )

    if result.matched_count == 0:
        return jsonify({"success": False, "error": "Ticket not found"})

    return jsonify({"success": True})
@app.route("/api/blocks/buy", methods=["POST"])
def create_blocks_checkout():
    current = get_current_user()

    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}
    pack_id = safe_str(data.get("pack_id"), 20)

    if pack_id not in BLOCK_PACKS:
        return jsonify({"success": False, "error": "Invalid block pack"}), 400

    pack = BLOCK_PACKS[pack_id]

    if not stripe.api_key:
        return jsonify({"success": False, "error": "Stripe secret key missing"}), 500

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": pack["name"]
                        },
                        "unit_amount": int(pack["price_usd"] * 100)
                    },
                    "quantity": 1
                }
            ],
            metadata={
                "username": current["username"],
                "pack_id": pack_id,
                "blocks": str(pack["blocks"])
            },
            success_url=BASE_URL + "/blocks/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=BASE_URL + "/blocks/cancel"
        )

        block_transactions.insert_one({
            "username": current["username"],
            "pack_id": pack_id,
            "pack_name": pack["name"],
            "blocks_added": pack["blocks"],
            "price_usd": pack["price_usd"],
            "status": "checkout_created",
            "stripe_checkout_session_id": checkout_session.id,
            "created_at": now_iso()
        })

        return jsonify({
            "success": True,
            "checkout_url": checkout_session.url
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
@app.route("/wiki")
def wiki_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    return render_template(
        "wiki.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp")
    )
@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return "Invalid payload", 400
    except stripe.error.SignatureVerificationError:
        return "Invalid signature", 400

    if event["type"] == "checkout.session.completed":
        session_obj = event["data"]["object"]

        checkout_id = session_obj.get("id")
        metadata = session_obj.get("metadata", {})

        username = metadata.get("username")
        pack_id = metadata.get("pack_id")

        if not username or pack_id not in BLOCK_PACKS:
            return "Invalid metadata", 400

        pack = BLOCK_PACKS[pack_id]

        existing = block_transactions.find_one({
            "stripe_checkout_session_id": checkout_id,
            "status": "completed"
        })

        if existing:
            return "Already processed", 200

        user = users.find_one({"username": username})

        if not user:
            return "User not found", 400

        users.update_one(
            {"username": username},
            {"$inc": {"blocks": pack["blocks"]}}
        )

        block_transactions.update_one(
            {"stripe_checkout_session_id": checkout_id},
            {
                "$set": {
                    "status": "completed",
                    "completed_at": now_iso(),
                    "stripe_payment_status": session_obj.get("payment_status")
                }
            },
            upsert=False
        )

    return "OK", 200
@app.route("/blocks/success")
def blocks_success():
    user = get_current_user()

    if not user:
        return redirect("/login")

    session_id = request.args.get("session_id")

    if not session_id:
        return "Missing session_id", 400

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return "Invalid Stripe session: " + str(e), 400

    if checkout_session.payment_status != "paid":
        return "Payment not completed", 400

    metadata = checkout_session.metadata

    username = metadata.username
    pack_id = metadata.pack_id

    if username != user["username"]:
        return "Wrong user", 403

    if pack_id not in BLOCK_PACKS:
        return "Invalid pack", 400

    pack = BLOCK_PACKS[pack_id]

    existing_completed = block_transactions.find_one({
        "stripe_checkout_session_id": session_id,
        "status": "completed"
    })

    if not existing_completed:
        users.update_one(
            {"username": user["username"]},
            {"$inc": {"blocks": pack["blocks"]}}
        )

        block_transactions.update_one(
            {"stripe_checkout_session_id": session_id},
            {
                "$set": {
                    "username": user["username"],
                    "pack_id": pack_id,
                    "pack_name": pack["name"],
                    "blocks_added": pack["blocks"],
                    "price_usd": pack["price_usd"],
                    "status": "completed",
                    "completed_at": now_iso(),
                    "stripe_payment_status": checkout_session.payment_status
                }
            },
            upsert=True
        )

    updated_user = users.find_one({"username": user["username"]})

    return render_template(
        "blocks_success.html",
        username=user["username"],
        blocks=updated_user.get("blocks", 0)
    )

@app.route("/blocks/cancel")
def blocks_cancel():
    user = get_current_user()

    if not user:
        return redirect("/login")

    return render_template(
        "blocks_cancel.html",
        username=user["username"],
        blocks=user.get("blocks", 0)
    )

@app.route("/api/blocks/balance")
def blocks_balance():
    current = get_current_user()

    if not current:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    return jsonify({
        "success": True,
        "blocks": current.get("blocks", 0)
    })
@app.route("/support")
def support_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    return render_template(
        "support.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        support_email=SUPPORT_EMAIL
    )


@app.route("/api/support/ticket", methods=["POST"])
def create_support_ticket():
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}

    category = safe_str(data.get("category"), 50)
    subject = safe_str(data.get("subject"), 100)
    message = safe_str(data.get("message"), 1000)

    allowed_categories = [
        "Account problem",
        "Blocks/payment issue",
        "Report user",
        "Bug report",
        "Appeal ban",
        "Other"
    ]

    if category not in allowed_categories:
        return jsonify({"success": False, "error": "Invalid category"})

    if len(subject) < 3:
        return jsonify({"success": False, "error": "Subject is too short"})

    if len(message) < 10:
        return jsonify({"success": False, "error": "Message is too short"})

    ticket = {
        "username": user["username"],
        "category": category,
        "subject": subject,
        "message": message,
        "status": "open",
        "created_at": now_iso(),
        "admin_reply": None,
        "admin_replied_at": None
    }

    result = support_tickets.insert_one(ticket)

    return jsonify({
        "success": True,
        "ticket_id": str(result.inserted_id)
    })


@app.route("/my-tickets")
def my_tickets_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    tickets = list(support_tickets.find(
        {"username": user["username"]}
    ).sort("created_at", -1))

    clean_tickets = []

    for t in tickets:
        clean_tickets.append({
            "id": str(t["_id"]),
            "category": t.get("category", "Other"),
            "subject": t.get("subject", ""),
            "message": t.get("message", ""),
            "status": t.get("status", "open"),
            "created_at": t.get("created_at", ""),
            "admin_reply": t.get("admin_reply"),
            "admin_replied_at": t.get("admin_replied_at")
        })

    return render_template(
        "my_tickets.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        tickets=clean_tickets,
        support_email=SUPPORT_EMAIL
    )


@app.route("/admin/tickets")
def admin_tickets_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    if user["username"] not in ADMINS:
        return redirect("/home")

    tickets = list(support_tickets.find().sort("created_at", -1))

    clean_tickets = []

    for t in tickets:
        clean_tickets.append({
            "id": str(t["_id"]),
            "username": t.get("username", ""),
            "category": t.get("category", "Other"),
            "subject": t.get("subject", ""),
            "message": t.get("message", ""),
            "status": t.get("status", "open"),
            "created_at": t.get("created_at", ""),
            "admin_reply": t.get("admin_reply"),
            "admin_replied_at": t.get("admin_replied_at")
        })

    return render_template(
        "admin_tickets.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        tickets=clean_tickets,
        support_email=SUPPORT_EMAIL
    )
@app.route("/settings")
def settings_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    privacy = user.get("privacy", {
        "friend_requests": "everyone",
        "messages": "friends",
        "show_online": True
    })

    if not isinstance(privacy, dict):
        privacy = {
            "friend_requests": "everyone",
            "messages": "friends",
            "show_online": True
        }

    return render_template(
        "settings.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp"),
        privacy=privacy,
        bio=user.get("bio", ""),
        status=user.get("status", ""),
        is_admin=is_admin_user(user)
    )

@app.route("/api/settings/change-password", methods=["POST"])
def change_password():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Not logged in"
        }), 401

    data = request.get_json(silent=True) or {}

    current_password = safe_str(data.get("current_password"), 100)
    new_password = safe_str(data.get("new_password"), 100)
    confirm_password = safe_str(data.get("confirm_password"), 100)

    if not current_password or not new_password or not confirm_password:
        return jsonify({
            "success": False,
            "error": "All password fields are required"
        }), 400

    if new_password != confirm_password:
        return jsonify({
            "success": False,
            "error": "New passwords do not match"
        }), 400

    if len(new_password) < 6:
        return jsonify({
            "success": False,
            "error": "Password must be at least 6 characters"
        }), 400

    if not bcrypt.checkpw(current_password.encode(), user["password_hash"]):
        return jsonify({
            "success": False,
            "error": "Current password is incorrect"
        }), 403

    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt())

    users.update_one(
        {"username": user["username"]},
        {
            "$set": {
                "password_hash": new_hash,
                "password_changed_at": datetime.utcnow(),
                "last_online": datetime.utcnow()
            }
        }
    )

    return jsonify({
        "success": True
    })
@app.route("/api/settings/profile", methods=["POST"])
def update_profile_settings():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Not logged in"
        }), 401

    data = request.get_json(silent=True) or {}

    bio = safe_str(data.get("bio"), 300)
    status = safe_str(data.get("status"), 80)

    if is_bad_text(bio):
        return jsonify({
            "success": False,
            "error": "Bio contains blocked text"
        }), 400

    if is_bad_text(status):
        return jsonify({
            "success": False,
            "error": "Status contains blocked text"
        }), 400

    users.update_one(
        {"username": user["username"]},
        {
            "$set": {
                "bio": bio,
                "status": status,
                "profile_updated_at": datetime.utcnow(),
                "last_online": datetime.utcnow()
            }
        }
    )

    return jsonify({
        "success": True
    })

@app.route("/api/settings/privacy", methods=["POST"])
def update_privacy_settings():
    user = get_current_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Not logged in"
        }), 401

    data = request.get_json(silent=True) or {}

    friend_requests_setting = safe_str(data.get("friend_requests"), 20)
    messages_setting = safe_str(data.get("messages"), 20)
    show_online = data.get("show_online")

    if friend_requests_setting not in ["everyone", "no_one"]:
        return jsonify({
            "success": False,
            "error": "Invalid friend request setting"
        }), 400

    if messages_setting not in ["friends", "no_one"]:
        return jsonify({
            "success": False,
            "error": "Invalid messages setting"
        }), 400

    show_online = bool(show_online)

    users.update_one(
        {"username": user["username"]},
        {
            "$set": {
                "privacy": {
                    "friend_requests": friend_requests_setting,
                    "messages": messages_setting,
                    "show_online": show_online
                },
                "last_online": datetime.utcnow()
            }
        }
    )

    return jsonify({
        "success": True
    })
@app.route("/forgot-password")
def forgot_password_page():
    if "user" in session:
        return redirect("/settings")

    return render_template("forgot_password.html")


@app.route("/api/forgot-password", methods=["POST"])
def forgot_password_request():
    data = request.get_json(silent=True) or {}

    username = safe_str(data.get("username"), 20)
    details = safe_str(data.get("details"), 1000)

    if not valid_username(username):
        return jsonify({"success": False, "error": "Invalid username"})

    user = users.find_one({"username": username})

    if not user:
        return jsonify({"success": False, "error": "Account not found"})

    if len(details) < 10:
        return jsonify({"success": False, "error": "Add more details so staff can verify you"})

    ticket = {
        "username": username,
        "category": "Account problem",
        "subject": "Password reset request",
        "message": details,
        "status": "open",
        "created_at": now_iso(),
        "admin_reply": None,
        "admin_replied_at": None,
        "type": "password_reset"
    }

    result = support_tickets.insert_one(ticket)

    return jsonify({
        "success": True,
        "ticket_id": str(result.inserted_id)
    })
@app.route("/admin")
def admin_panel():
    admin, response = require_admin()

    if response:
        return response

    ticket_docs = list(
        support_tickets.find()
        .sort("created_at", -1)
        .limit(100)
    )

    user_docs = list(
        users.find(
            {},
            {"_id": 0, "username": 1, "blocks": 1, "friends": 1}
        )
        .sort("username", 1)
        .limit(200)
    )

    ban_docs = list(
        bans.find()
        .sort("created_at", -1)
    )

    game_docs = list(
        games_collection.find()
        .sort("created_at", -1)
        .limit(100)
    )

    tickets = []

    for t in ticket_docs:
        tickets.append({
            "id": str(t["_id"]),
            "username": t.get("username", ""),
            "category": t.get("category", "Other"),
            "subject": t.get("subject", ""),
            "message": t.get("message", ""),
            "status": t.get("status", "open"),
            "created_at": t.get("created_at", ""),
            "admin_reply": t.get("admin_reply"),
            "admin_replied_at": t.get("admin_replied_at")
        })

    all_users = []

    for u in user_docs:
        username = u.get("username", "")

        all_users.append({
            "username": username,
            "blocks": u.get("blocks", 0),
            "friends_count": len(u.get("friends", [])),
            "banned": is_banned(username),
            "admin": username in ADMIN_USERS
        })

    banned_users = []

    for b in ban_docs:
        banned_users.append({
            "username": b.get("username", ""),
            "reason": b.get("reason", ""),
            "banned_by": b.get("banned_by", ""),
            "created_at": b.get("created_at", "")
        })

    uploaded_games = []

    for g in game_docs:
        uploaded_games.append({
            "game_id": g.get("game_id", ""),
            "title": g.get("title", ""),
            "description": g.get("description", ""),
            "creator": g.get("creator", ""),
            "godot_version": g.get("godot_version", ""),
            "max_players": g.get("max_players", 0),
            "multiplayer": g.get("multiplayer", False),
            "status": g.get("status", "pending"),
            "created_at": g.get("created_at", ""),
            "server_running": g.get("server_running", False)
        })

    return render_template(
        "admin.html",
        username=admin["username"],
        blocks=admin.get("blocks", 0),
        tickets=tickets,
        users=all_users,
        banned_users=banned_users,
        uploaded_games=uploaded_games
    )
@app.route("/api/admin/games/approve", methods=["POST"])
def admin_approve_game():
    admin, response = require_admin()

    if response:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    game_id = safe_str(data.get("game_id"), 80)

    if not game_id:
        return jsonify({"success": False, "error": "Missing game_id"}), 400

    game = games_collection.find_one({"game_id": game_id})

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    if game.get("status") == "approved":
        return jsonify({"success": False, "error": "Game is already approved"}), 400

    if game.get("multiplayer"):
        ok, msg, pid, server_port = start_game_server(game)

        if not ok:
            return jsonify({
                "success": False,
                "error": msg
            }), 500

        update_data = {
            "status": "approved",
            "approved_at": now_iso(),
            "approved_by": admin["username"],
            "server_running": True,
            "server_process_pid": pid,
            "server_port": server_port
        }

        response_data = {
            "success": True,
            "message": "Multiplayer game approved and server started",
            "pid": pid,
            "server_port": server_port
        }

    else:
        update_data = {
            "status": "approved",
            "approved_at": now_iso(),
            "approved_by": admin["username"],
            "server_running": False,
            "server_process_pid": None,
            "server_port": None
        }

        response_data = {
            "success": True,
            "message": "Singleplayer game approved"
        }

    games_collection.update_one(
        {"game_id": game_id},
        {"$set": update_data}
    )

    return jsonify(response_data)
@app.route("/api/admin/games/reject", methods=["POST"])
def admin_reject_game():
    admin, response = require_admin()

    if response:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    game_id = safe_str(data.get("game_id"), 80)

    result = games_collection.update_one(
        {"game_id": game_id},
        {
            "$set": {
                "status": "rejected",
                "rejected_at": now_iso(),
                "rejected_by": admin["username"]
            }
        }
    )

    if result.matched_count == 0:
        return jsonify({"success": False, "error": "Game not found"}), 404

    return jsonify({"success": True})
@app.route("/my-games")
def my_games_page():
    user = get_current_user()

    if not user:
        return redirect("/login")

    game_docs = list(
        games_collection.find(
            {"creator": user["username"]},
            {"_id": 0}
        )
        .sort("created_at", -1)
    )

    games = []

    for g in game_docs:
        icon_url = g.get("icon_url") or "/logo.png"

        games.append({
            "game_id": g.get("game_id", ""),
            "title": g.get("title", ""),
            "description": g.get("description", ""),
            "status": g.get("status", "pending"),
            "godot_version": g.get("godot_version", ""),
            "max_players": g.get("max_players", 0),
            "multiplayer": g.get("multiplayer", False),
            "created_at": g.get("created_at", ""),
            "updated_at": g.get("updated_at", ""),
            "icon_url": icon_url
        })

    return render_template(
        "my_games.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp"),
        games=games,
        is_admin=is_admin_user(user)
    )
@app.route("/api/games/<game_id>/update", methods=["POST"])
def update_uploaded_game(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({"game_id": game_id})

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    if game.get("creator") != user["username"]:
        return jsonify({"success": False, "error": "You can only update your own games"}), 403

    if "game_zip" not in request.files:
        return jsonify({"success": False, "error": "No game zip uploaded"}), 400

    file = request.files["game_zip"]

    if file.filename == "":
        return jsonify({"success": False, "error": "No selected file"}), 400

    if not allowed_game_file(file.filename):
        return jsonify({"success": False, "error": "Only .zip files are allowed"}), 400

    old_zip_path = game.get("zip_path")
    old_extract_path = game.get("extract_path")

    temp_id = str(uuid.uuid4())
    temp_zip_path = os.path.join(GAME_UPLOAD_DIR, f"update_{temp_id}.zip")
    temp_extract_path = os.path.join(GAME_EXTRACT_DIR, f"update_{temp_id}")

    file.save(temp_zip_path)

    try:
        os.makedirs(temp_extract_path, exist_ok=True)

        with zipfile.ZipFile(temp_zip_path, "r") as zip_ref:
            safe_extract_zip(zip_ref, temp_extract_path)

        valid, message, manifest = validate_uploaded_game(temp_extract_path)

        if not valid:
            shutil.rmtree(temp_extract_path, ignore_errors=True)
            if os.path.exists(temp_zip_path):
                os.remove(temp_zip_path)

            return jsonify({"success": False, "error": message}), 400

        final_zip_path = os.path.join(GAME_UPLOAD_DIR, f"{game_id}.zip")
        final_extract_path = os.path.join(GAME_EXTRACT_DIR, game_id)

        if old_extract_path and os.path.exists(old_extract_path):
            shutil.rmtree(old_extract_path, ignore_errors=True)

        if old_zip_path and os.path.exists(old_zip_path):
            os.remove(old_zip_path)

        if os.path.exists(final_extract_path):
            shutil.rmtree(final_extract_path, ignore_errors=True)

        shutil.move(temp_extract_path, final_extract_path)
        shutil.move(temp_zip_path, final_zip_path)

        games_collection.update_one(
            {"game_id": game_id},
            {
                "$set": {
                    "title": manifest["title"],
                    "description": manifest["description"],
                    "manifest_creator": manifest["creator"],
                    "godot_version": manifest["godot_version"],
                    "max_players": manifest["max_players"],
                    "multiplayer": manifest["multiplayer"],
                    "status": "pending",
                    "server_running": False,
                    "server_process_pid": None,
                    "server_port": None,
                    "zip_path": final_zip_path,
                    "extract_path": final_extract_path,
                    "client_pck_path": os.path.join(final_extract_path, "client", "client.pck"),
                    "server_pck_path": os.path.join(final_extract_path, "server", "server.pck"),
                    "updated_at": now_iso(),
                    "approved_at": None,
                    "approved_by": None
                }
            }
        )

        return jsonify({
            "success": True,
            "message": "Game updated and sent for review"
        })

    except zipfile.BadZipFile:
        shutil.rmtree(temp_extract_path, ignore_errors=True)
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)

        return jsonify({"success": False, "error": "Invalid zip file"}), 400

    except Exception as e:
        shutil.rmtree(temp_extract_path, ignore_errors=True)
        if os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)

        return jsonify({"success": False, "error": str(e)}), 500
@app.route("/api/games/<game_id>/delete", methods=["POST"])
def delete_uploaded_game(game_id):
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({"game_id": game_id})

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    if game.get("creator") != user["username"]:
        return jsonify({"success": False, "error": "You can only delete your own games"}), 403

    zip_path = game.get("zip_path")
    extract_path = game.get("extract_path")

    if extract_path and os.path.exists(extract_path):
        shutil.rmtree(extract_path, ignore_errors=True)

    if zip_path and os.path.exists(zip_path):
        os.remove(zip_path)

    games_collection.delete_one({"game_id": game_id})

    return jsonify({
        "success": True,
        "message": "Game deleted"
    })
@app.route("/api/admin/games/<game_id>/manifest")
def admin_view_game_manifest(game_id):
    admin, response = require_admin()

    if response:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({"game_id": game_id})

    if not game:
        return jsonify({"success": False, "error": "Game not found"}), 404

    manifest_path = os.path.join(game["extract_path"], "manifest.json")

    if not os.path.exists(manifest_path):
        return jsonify({"success": False, "error": "Manifest not found"}), 404

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    return jsonify({
        "success": True,
        "manifest": manifest
    })


@app.route("/api/admin/games/<game_id>/download")
def admin_download_game_zip(game_id):
    admin, response = require_admin()

    if response:
        return redirect("/login")

    game_id = safe_str(game_id, 80)

    game = games_collection.find_one({"game_id": game_id})

    if not game:
        return "Game not found", 404

    zip_path = game.get("zip_path")

    if not zip_path or not os.path.exists(zip_path):
        return "Zip not found", 404

    folder = os.path.dirname(zip_path)
    filename = os.path.basename(zip_path)

    return send_from_directory(folder, filename, as_attachment=True)

@app.route("/api/admin/unban", methods=["POST"])
def admin_unban_user():
    admin, response = require_admin()

    if response:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    username = safe_str(data.get("username"), 20)

    print("UNBAN REQUEST:", username)

    if not valid_username(username):
        return jsonify({"success": False, "error": "Invalid username"})

    result = bans.delete_one({
        "username": username
    })

    print("UNBAN DELETED COUNT:", result.deleted_count)

    if result.deleted_count == 0:
        return jsonify({"success": False, "error": "User is not banned"})

    return jsonify({
        "success": True,
        "username": username
    })
@app.route("/api/admin/tickets/delete", methods=["POST"])
def admin_delete_ticket():
    admin, response = require_admin()

    if response:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    ticket_id = safe_str(data.get("ticket_id"), 40)

    if not ObjectId.is_valid(ticket_id):
        return jsonify({"success": False, "error": "Invalid ticket ID"})

    result = support_tickets.delete_one({
        "_id": ObjectId(ticket_id)
    })

    if result.deleted_count == 0:
        return jsonify({"success": False, "error": "Ticket not found"})

    return jsonify({"success": True})
@app.route("/api/admin/ban", methods=["POST"])
def admin_ban_user():
    admin, response = require_admin()

    if response:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}

    username = safe_str(data.get("username"), 20)
    reason = safe_str(data.get("reason"), 250)

    if not valid_username(username):
        return jsonify({"success": False, "error": "Invalid username"})

    if username in ADMIN_USERS:
        return jsonify({"success": False, "error": "You cannot ban an admin"})

    target = users.find_one({"username": username})

    if not target:
        return jsonify({"success": False, "error": "User not found"})

    if not reason:
        reason = "No reason provided"

    bans.update_one(
        {"username": username},
        {
            "$set": {
                "username": username,
                "reason": reason,
                "banned_by": admin["username"],
                "created_at": now_iso()
            }
        },
        upsert=True
    )

    deleted = messages.delete_many({
        "username": username
    })

    system_msg = {
        "room": "global",
        "username": "System",
        "message": f"{username} was banned. {deleted.deleted_count} messages were wiped.",
        "created_at": now_iso()
    }

    result = messages.insert_one(system_msg.copy())
    system_msg["id"] = str(result.inserted_id)

    socketio.emit("user_messages_wiped", {
        "username": username
    }, room="global")

    socketio.emit("new_message", system_msg, room="global")

    return jsonify({
        "success": True,
        "messages_deleted": deleted.deleted_count
    })
@socketio.on("send_message")
def send_message(data):
    current = get_current_user()

    if not current or not isinstance(data, dict):
        return

    username = current["username"]
    room = valid_room(data.get("room", "global"))
    text = clean_message(data.get("message", ""))

    if not text:
        return

    current_privacy = current.get("privacy", {})
    current_messages_setting = current_privacy.get("messages", "friends")

    print("CHAT DEBUG:", username, "room:", room, "messages:", current_messages_setting)

    if current_messages_setting == "no_one":
        emit("error_message", {
            "message": "Your messages are turned off. Enable messages in Settings to chat."
        })
        return

    if room.startswith("dm_"):
        parts = room.replace("dm_", "").split("_")

        if len(parts) != 2 or username not in parts:
            emit("error_message", {"message": "Invalid DM room."})
            return

        other = parts[0] if parts[1] == username else parts[1]

        if not are_friends(username, other):
            emit("error_message", {"message": "You can only DM friends."})
            return

        other_user = users.find_one({"username": other})

        if not other_user:
            emit("error_message", {"message": "User not found."})
            return

        other_privacy = other_user.get("privacy", {})
        other_messages_setting = other_privacy.get("messages", "friends")

        if other_messages_setting == "no_one":
            emit("error_message", {"message": "This user is not accepting messages."})
            return

    if room.startswith("party_"):
        pid = room.replace("party_", "")

        if not ObjectId.is_valid(pid):
            emit("error_message", {"message": "Invalid party."})
            return

        if not parties.find_one({"_id": ObjectId(pid), "members": username}):
            emit("error_message", {"message": "Forbidden party."})
            return

    join_room(room)

    now = datetime.utcnow()
    last = cooldowns.get(username)

    if last and (now - last).total_seconds() < 1:
        emit("error_message", {"message": "Slow down."})
        return

    cooldowns[username] = now

    db_msg = {
        "room": room,
        "username": username,
        "message": text,
        "created_at": now.isoformat()
    }

    messages.insert_one(db_msg.copy())

    emit("new_message", db_msg, room=room)
if __name__ == "__main__":
    restart_game_servers_from_mongodb()

    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False
    )