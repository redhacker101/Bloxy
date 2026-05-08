const socket = io();

let currentRoom = "global";
let currentPartyId = null;

const messagesBox = document.getElementById("messages");
const input = document.getElementById("messageInput");
const roomName = document.getElementById("roomName");
const roomSubtext = document.getElementById("roomSubtext");

const emojiMap = {
    ":fire:": "🔥",
    ":skull:": "💀",
    ":gg:": "🎮 GG",
    ":blox:": "🧊"
};

function getPFP(username) {
    return `/pfp/user/${encodeURIComponent(username)}?t=${Date.now()}`;
}

function dmRoom(a, b) {
    return "dm_" + [a, b].sort().join("_");
}

function parseEmojis(text) {
    for (const key in emojiMap) {
        text = text.replaceAll(key, emojiMap[key]);
    }
    return text;
}

function escapeHTML(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function addMessage(username, message) {
    const isMe = username === window.BLOXY_USERNAME;

    const row = document.createElement("div");
    row.className = isMe ? "message-row mine" : "message-row";

    row.innerHTML = `
        <div class="avatar">
            <img src="${getPFP(username)}" onerror="this.src='/logo.png'">
        </div>
        <div class="message-bubble">
            <div class="message-name">${escapeHTML(username)}</div>
            <div class="message-text">${escapeHTML(parseEmojis(message))}</div>
        </div>
    `;

    messagesBox.appendChild(row);
    messagesBox.scrollTop = messagesBox.scrollHeight;
}

function addSystem(text) {
    const div = document.createElement("div");
    div.className = "system-message";
    div.textContent = text;
    messagesBox.appendChild(div);
    messagesBox.scrollTop = messagesBox.scrollHeight;
}

function loadHistory(room) {
    fetch(`/api/chat/history/${room}`)
        .then(res => res.json())
        .then(data => {
            messagesBox.innerHTML = "";

            if (data.error) {
                addSystem("⚠️ " + data.error);
                return;
            }

            data.forEach(msg => {
                addMessage(msg.username, msg.message);
            });
        });
}

function switchRoom(room, button) {
    currentRoom = room;
    currentPartyId = null;

    roomName.textContent = "# " + room;
    roomSubtext.textContent = "Real-time Bloxy chat";

    document.querySelectorAll(".room").forEach(btn => {
        btn.classList.remove("active");
    });

    if (button) button.classList.add("active");

    socket.emit("join_chat", { room });
    loadHistory(room);
}

function openDM(friend) {
    const room = dmRoom(window.BLOXY_USERNAME, friend);
    currentRoom = room;
    currentPartyId = null;

    roomName.textContent = "DM: " + friend;
    roomSubtext.textContent = "Private messages";

    socket.emit("join_chat", { room });
    loadHistory(room);
}

function openParty(id, members) {
    const room = "party_" + id;
    currentRoom = room;
    currentPartyId = id;

    roomName.textContent = "Party Chat";
    roomSubtext.textContent = members || "Private party chat";

    socket.emit("join_chat", { room });
    loadHistory(room);
}

function sendMessage() {
    const text = input.value.trim();
    if (!text) return;

    if (!currentRoom) {
        addSystem("⚠️ Select a chat first.");
        return;
    }

    if (window.BLOXY_MESSAGES_SETTING === "no_one") {
        addSystem("⚠️ Your messages are turned off. Enable messages in Settings to chat.");
        return;
    }

    socket.emit("send_message", {
        room: currentRoom,
        message: text
    });

    input.value = "";
}

function addEmoji(code) {
    input.value += " " + code + " ";
    input.focus();
}

function respondFriend(username, action) {
    fetch("/api/friends/respond", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ username, action })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) location.reload();
        else addSystem("⚠️ " + data.error);
    });
}

function removeFriend(username) {
    if (!confirm(`Remove ${username}?`)) return;

    fetch("/api/friends/remove", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ username })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) location.reload();
        else addSystem("⚠️ " + data.error);
    });
}

function createParty() {
    const checked = document.querySelectorAll("#partyFriendList input:checked");
    const members = Array.from(checked).map(x => x.value);

    if (!members.length) {
        addSystem("⚠️ Select at least one friend.");
        return;
    }

    fetch("/api/party/create", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ members })
    })
    .then(res => res.json())
    .then(data => {
        if (!data.success) {
            addSystem("⚠️ " + data.error);
            return;
        }

        addSystem("✅ Party created.");
        setTimeout(() => location.reload(), 500);
    });
}

function addFriendToParty(partyId) {
    const select = document.getElementById(`addPartyFriend_${partyId}`);
    if (!select) return;

    const username = select.value;

    if (!username) {
        addSystem("⚠️ Choose a friend to add.");
        return;
    }

    fetch("/api/party/add", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            party_id: partyId,
            username: username
        })
    })
    .then(res => res.json())
    .then(data => {
        if (!data.success) {
            addSystem("⚠️ " + data.error);
            return;
        }

        addSystem(`✅ Added ${username} to party.`);
        setTimeout(() => location.reload(), 500);
    });
}

function deleteParty(id) {
    if (!confirm("Delete this party?")) return;

    fetch("/api/party/delete", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ party_id: id })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) location.reload();
        else addSystem("⚠️ " + data.error);
    });
}

function showGlobalWarning() {
    if (localStorage.getItem("bloxy_global_warning_accepted") === "yes") {
        socket.emit("join_chat", { room: currentRoom });
        loadHistory(currentRoom);
        return;
    }

    const warning = document.getElementById("globalWarning");
    if (warning) {
        warning.style.display = "flex";
    }
}

function enterGlobalChat() {
    localStorage.setItem("bloxy_global_warning_accepted", "yes");

    const warning = document.getElementById("globalWarning");
    if (warning) {
        warning.style.display = "none";
    }

    switchRoom("global");
}

function cancelGlobalChat() {
    const warning = document.getElementById("globalWarning");
    if (warning) {
        warning.style.display = "none";
    }

    currentRoom = "";
    currentPartyId = null;
    messagesBox.innerHTML = "";
    roomName.textContent = "Chat";
    roomSubtext.textContent = "Select a DM or party chat.";
}
let selectedPartyFriend = {};

function selectPartyFriend(partyId, username) {
    selectedPartyFriend[partyId] = username;

    const dropdown = document.querySelector(`[data-party="${partyId}"]`);
    if (!dropdown) return;

    dropdown.querySelector(".dropdown-selected").textContent = username;
}

function addFriendToParty(partyId) {
    const username = selectedPartyFriend[partyId];

    if (!username) {
        addSystem("⚠️ Choose a friend first.");
        return;
    }

    fetch("/api/party/add", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            party_id: partyId,
            username
        })
    })
    .then(res => res.json())
    .then(data => {
        if (!data.success) {
            addSystem("⚠️ " + data.error);
            return;
        }

        addSystem(`✅ Added ${username} to party.`);
        setTimeout(() => location.reload(), 500);
    });
}
input.addEventListener("keydown", e => {
    if (e.key === "Enter") sendMessage();
});

socket.on("connect", () => {
    showGlobalWarning();
});

socket.on("new_message", data => {
    if (data.room === currentRoom) {
        addMessage(data.username, data.message);
    }
});

socket.on("error_message", data => {
    addSystem("⚠️ " + data.message);
});