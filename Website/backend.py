from flask import Flask, request, redirect, session, render_template, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, emit
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename
import bcrypt
import re
import os
import math
import stripe


app = Flask(__name__, template_folder=".", static_folder=".", static_url_path="")
app.secret_key = "change-this-secret-key"
stripe.api_key = ""
STRIPE_WEBHOOK_SECRET = ""
BASE_URL = "http://localhost:5000"
socketio = SocketIO(app, cors_allowed_origins="*")

client = MongoClient("mongodb://localhost:27017/")
db = client["bloxy"]

users = db["users"]
messages = db["messages"]
parties = db["parties"] 
friend_requests = db["friend_requests"]
block_transactions = db["block_transactions"]

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


def now_iso():
    return datetime.utcnow().isoformat()


def safe_str(value, max_len=250):
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def valid_username(username):
    return bool(USERNAME_RE.fullmatch(username))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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


@app.route("/")
def root():
    return render_template("index.html")


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
            "pfp": None
        })
    except Exception:
        return jsonify({"success": False, "error": "User already exists"})

    return jsonify({"success": True})


@app.route("/api/login", methods=["POST"])
def login():
    ip = request.remote_addr or "unknown"
    login_attempts.setdefault(ip, 0)

    if login_attempts[ip] >= 20:
        return jsonify({"success": False, "error": "Too many attempts"})

    data = request.get_json(silent=True) or {}

    username = safe_str(data.get("username"), 20)
    password = safe_str(data.get("password"), 100)

    if not valid_username(username):
        login_attempts[ip] += 1
        return jsonify({"success": False, "error": "Invalid login"})

    user = users.find_one({"username": username})
    if user and is_banned(username):
     return jsonify({"success": False, "error": "This account is banned"})

    if not user:
        login_attempts[ip] += 1
        return jsonify({"success": False, "error": "Invalid login"})

    if bcrypt.checkpw(password.encode(), user["password_hash"]):
        login_attempts[ip] = 0
        session["user"] = username
        return jsonify({"success": True})

    login_attempts[ip] += 1
    return jsonify({"success": False, "error": "Invalid login"})

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

    friend_usernames = user.get("friends", [])
    friends_data = []

    for friend_name in friend_usernames:
        friend_name = safe_str(friend_name, 20)

        if valid_username(friend_name):
            friend_user = users.find_one(
                {"username": friend_name},
                {"_id": 0, "username": 1, "pfp": 1}
            )

            if friend_user:
                friend_show_online = user_allows_online_status(friend_user)

                friends_data.append({
                "username": friend_user["username"],
                 "pfp": friend_user.get("pfp"),
                "online": friend_show_online and friend_user["username"] in online_usernames
})

    games = []

    return render_template(
        "home.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp"),
        friends=friends_data,
        games=games,
        is_admin=is_admin_user(user)
    )


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

    return f"<h1>{game_id}</h1><p>Game page coming soon.</p><a href='/home'>Back</a>"


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

    return render_template(
        "settings.html",
        username=user["username"],
        blocks=user.get("blocks", 0),
        pfp=user.get("pfp"),
        privacy=privacy
    )


@app.route("/api/settings/change-password", methods=["POST"])
def change_password():
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}

    current_password = safe_str(data.get("current_password"), 100)
    new_password = safe_str(data.get("new_password"), 100)
    confirm_password = safe_str(data.get("confirm_password"), 100)

    if not current_password or not new_password or not confirm_password:
        return jsonify({"success": False, "error": "All password fields are required"})

    if new_password != confirm_password:
        return jsonify({"success": False, "error": "New passwords do not match"})

    if len(new_password) < 6:
        return jsonify({"success": False, "error": "New password must be at least 6 characters"})

    if not bcrypt.checkpw(current_password.encode(), user["password_hash"]):
        return jsonify({"success": False, "error": "Current password is wrong"})

    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt())

    users.update_one(
        {"username": user["username"]},
        {
            "$set": {
                "password_hash": new_hash,
                "password_changed_at": now_iso()
            }
        }
    )

    return jsonify({"success": True})

@app.route("/api/settings/privacy", methods=["POST"])
def update_privacy_settings():
    user = get_current_user()

    if not user:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    data = request.get_json(silent=True) or {}

    friend_requests_setting = safe_str(data.get("friend_requests"), 20)
    messages_setting = safe_str(data.get("messages"), 20)
    show_online = data.get("show_online")

    if friend_requests_setting not in ["everyone", "no_one"]:
        return jsonify({"success": False, "error": "Invalid friend request setting"})

    if messages_setting not in ["friends", "no_one"]:
        return jsonify({"success": False, "error": "Invalid message setting"})

    if not isinstance(show_online, bool):
        return jsonify({"success": False, "error": "Invalid online status setting"})

    users.update_one(
        {"username": user["username"]},
        {
            "$set": {
                "privacy": {
                    "friend_requests": friend_requests_setting,
                    "messages": messages_setting,
                    "show_online": show_online
                }
            }
        }
    )

    username = user["username"]

    for sid, connection in list(online_users.items()):
        if connection.get("username") == username:
            connection["show_online"] = show_online

    if show_online:
        online_usernames.add(username)
    else:
        online_usernames.discard(username)

    socketio.emit("online_status_update", {
        "username": username,
        "online": show_online
    })

    return jsonify({"success": True})


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

    ticket_docs = list(support_tickets.find().sort("created_at", -1).limit(100))
    user_docs = list(users.find({}, {"_id": 0, "username": 1, "blocks": 1, "friends": 1}).sort("username", 1).limit(200))
    ban_docs = list(bans.find().sort("created_at", -1))

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

    return render_template(
        "admin.html",
        username=admin["username"],
        blocks=admin.get("blocks", 0),
        tickets=tickets,
        users=all_users,
        banned_users=banned_users
    )
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
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
