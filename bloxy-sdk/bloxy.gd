## bloxy_sdk.gd
## Bloxy SDK for Godot 4 — Full Edition
## Version 2.0.0
##
## Drop this as an autoload singleton named "Bloxy" in your project settings:
##   Project → Project Settings → Autoload → Add bloxy_sdk.gd as "Bloxy"
##
## Features:
##   identity, multiplayer, chat, reports, blocks, purchases, rewards,
##   events, sessions, avatars, debug, anticheat, rate-limiting,
##   leaderboards, player-data (save/load), notifications, moderation,
##   server analytics, input validation, encryption helpers

extends Node

# ============================================================
# CONSTANTS
# ============================================================

const SDK_VERSION       = "2.0.0"
const SDK_BUILD         = 2000
const MAX_USERNAME_LEN  = 32
const MAX_CHAT_LEN      = 160
const MAX_REASON_LEN    = 512
const MAX_EVENT_NAME_LEN = 64
const MAX_LEADERBOARD_ENTRIES = 100
const HEARTBEAT_INTERVAL = 30.0      # seconds
const SESSION_PING_INTERVAL = 60.0   # seconds
const AC_TICK_INTERVAL   = 0.25      # anticheat sample rate

# ============================================================
# SIGNALS — Connection
# ============================================================

signal bloxy_connected
signal bloxy_connection_failed
signal bloxy_disconnected

# ============================================================
# SIGNALS — Chat
# ============================================================

signal chat_message_received(username: String, user_id: String, message: String, peer_id: int)
signal chat_system_message_received(message: String)
signal chat_muted(peer_id: int, reason: String, duration: float)
signal chat_unmuted(peer_id: int)

# ============================================================
# SIGNALS — Anticheat
# ============================================================

signal ac_violation_detected(peer_id: int, violation_type: String, details: Dictionary)
signal ac_player_flagged(peer_id: int, flag_count: int)
signal ac_player_kicked(peer_id: int, reason: String)

# ============================================================
# SIGNALS — Economy / Rewards
# ============================================================

signal blocks_updated(new_amount: int)
signal purchase_completed(item_id: String, ok: bool)
signal reward_granted(reason: String, amount: int, ok: bool)

# ============================================================
# SIGNALS — Sessions / Events
# ============================================================

signal session_started(ok: bool)
signal session_ended(ok: bool)
signal event_logged(event_name: String, ok: bool)

# ============================================================
# SIGNALS — Leaderboard
# ============================================================

signal leaderboard_fetched(board_id: String, entries: Array, ok: bool)
signal leaderboard_score_submitted(board_id: String, ok: bool)

# ============================================================
# SIGNALS — Player Data
# ============================================================

signal player_data_loaded(key: String, value: Variant, ok: bool)
signal player_data_saved(key: String, ok: bool)

# ============================================================
# SIGNALS — Moderation
# ============================================================

signal player_reported(target_id: String, ok: bool)
signal player_banned(peer_id: int, reason: String)

# ============================================================
# SIGNALS — Notifications
# ============================================================

signal notification_received(title: String, body: String, data: Dictionary)

# ============================================================
# CORE IDENTITY
# ============================================================

var username    : String = ""
var user_id     : String = ""
var game_id     : String = ""
var server_ip   : String = ""
var server_port : int    = 0
var ticket      : String = ""
var api         : String = ""
var server_id   : String = ""
var server_token: String = ""
var avatar      : Dictionary = {}
var blocks      : int    = 0

# ============================================================
# CONNECTION STATE
# ============================================================

var peer : ENetMultiplayerPeer = ENetMultiplayerPeer.new()
var connection_status : String = "offline"
var _connected_peers  : Dictionary = {}   # peer_id → Dictionary of info

# ============================================================
# CHAT STATE
# ============================================================

var chat_users           : Dictionary = {}
var chat_ready           : bool       = false
var max_chat_length      : int        = MAX_CHAT_LEN
var chat_cooldown_seconds: float      = 0.5
var last_chat_time       : float      = 0.0
var _muted_peers         : Dictionary = {}   # peer_id → { until: float, reason: String }

# ============================================================
# HEARTBEAT / SESSION TIMERS
# ============================================================

var _heartbeat_timer     : float = 0.0
var _session_ping_timer  : float = 0.0
var _session_active      : bool  = false
var _session_start_time  : float = 0.0

# ============================================================
# RATE LIMITING
# ============================================================

# Tracks API call timestamps per endpoint to avoid hammering
var _rate_limit_buckets  : Dictionary = {}   # endpoint → Array[float]
var _rate_limit_max      : int        = 10   # max calls per window
var _rate_limit_window   : float      = 10.0 # window in seconds

# ============================================================
# ANTICHEAT STATE
# ============================================================

var _ac_enabled          : bool       = true
var _ac_tick_timer       : float      = 0.0
var _ac_peer_flags       : Dictionary = {}   # peer_id → int (flag count)
var _ac_peer_data        : Dictionary = {}   # peer_id → per-player AC state
var _ac_kick_threshold   : int        = 5    # flags before auto-kick
var _ac_log              : Array      = []   # ring buffer of recent violations

# Position / speed checks
var _ac_max_speed        : float      = 50.0   # units/second
var _ac_teleport_dist    : float      = 100.0  # instant jump threshold

# Action spam checks
var _ac_max_actions_per_sec : float   = 20.0

# Stat cap checks (game-defined, override as needed)
var _ac_stat_caps        : Dictionary = {}   # stat_name → max_value

# Trusted server-only RPCs whitelist
var _ac_trusted_rpcs     : Array      = [
	"_bloxy_server_register_chat_identity",
	"_bloxy_server_receive_chat",
	"_bloxy_client_receive_chat",
	"_bloxy_client_receive_system",
	"_bloxy_ac_server_report_position",
	"_bloxy_ac_server_report_action",
	"_bloxy_ac_server_report_stat",
	"_bloxy_ac_client_kick",
	"_bloxy_ac_client_warning",
]

# ============================================================
# PLAYER DATA CACHE
# ============================================================

var _player_data_cache   : Dictionary = {}   # key → value

# ============================================================
# LEADERBOARD CACHE
# ============================================================

var _leaderboard_cache   : Dictionary = {}   # board_id → { entries, timestamp }
var _leaderboard_ttl     : float      = 60.0

# ============================================================
# NOTIFICATION QUEUE
# ============================================================

var _notification_queue  : Array      = []
var _notification_poll_timer : float  = 0.0
var _notification_poll_interval : float = 30.0

# ============================================================
# MODERATION
# ============================================================

var _banned_peers        : Dictionary = {}   # peer_id → reason
var _banned_user_ids     : Dictionary = {}   # user_id → reason

# ============================================================
# INPUT VALIDATION / FILTER
# ============================================================

var _profanity_list      : Array      = []   # populated via load_profanity_list()
var _filter_enabled      : bool       = true

# ============================================================
# DEBUG / LOGGING
# ============================================================

var _debug_mode          : bool       = false
var _log_buffer          : Array      = []
var _max_log_lines       : int        = 500

# ============================================================
# READY
# ============================================================

func _ready() -> void:
	_load_args()

	multiplayer.connected_to_server.connect(_on_connected_to_server)
	multiplayer.connection_failed.connect(_on_connection_failed)
	multiplayer.server_disconnected.connect(_on_server_disconnected)

	_log_info("Bloxy SDK v%s initialised. game_id=%s ready=%s" % [SDK_VERSION, game_id, str(is_ready())])

# ============================================================
# PROCESS — Timers
# ============================================================

func _process(delta: float) -> void:
	_tick_heartbeat(delta)
	_tick_session_ping(delta)
	_tick_anticheat(delta)
	_tick_notifications(delta)
	_tick_mute_expiry(delta)

func _tick_heartbeat(delta: float) -> void:
	if not multiplayer.is_server():
		return
	if not _session_active:
		return

	_heartbeat_timer += delta
	if _heartbeat_timer >= HEARTBEAT_INTERVAL:
		_heartbeat_timer = 0.0
		send_heartbeat()

func _tick_session_ping(delta: float) -> void:
	if not _session_active:
		return

	_session_ping_timer += delta
	if _session_ping_timer >= SESSION_PING_INTERVAL:
		_session_ping_timer = 0.0
		_ping_session()

func _tick_anticheat(delta: float) -> void:
	if not _ac_enabled:
		return
	if not multiplayer.is_server():
		return

	_ac_tick_timer += delta
	if _ac_tick_timer >= AC_TICK_INTERVAL:
		_ac_tick_timer = 0.0
		_ac_run_server_tick()

func _tick_notifications(delta: float) -> void:
	if not is_ready():
		return

	_notification_poll_timer += delta
	if _notification_poll_timer >= _notification_poll_interval:
		_notification_poll_timer = 0.0
		_poll_notifications()

func _tick_mute_expiry(delta: float) -> void:
	if not multiplayer.is_server():
		return

	var now := Time.get_ticks_msec() / 1000.0
	var to_remove : Array = []

	for pid in _muted_peers:
		if _muted_peers[pid]["until"] > 0.0 and now >= _muted_peers[pid]["until"]:
			to_remove.append(pid)

	for pid in to_remove:
		_muted_peers.erase(pid)
		chat_unmuted.emit(pid)
		_log_info("Peer %d unmuted (expired)." % pid)

# ============================================================
# ARGUMENT LOADING
# ============================================================

func _load_args() -> void:
	var args := OS.get_cmdline_user_args()
	if args.is_empty():
		args = OS.get_cmdline_args()

	var i := 0
	while i < args.size():
		var arg : String = args[i]
		var has_next := i + 1 < args.size()

		match arg:
			"--bloxy-ticket":
				if has_next: ticket = args[i + 1]; i += 1
			"--bloxy-game-id":
				if has_next: game_id = args[i + 1]; i += 1
			"--bloxy-api":
				if has_next: api = args[i + 1]; i += 1
			"--bloxy-username":
				if has_next: username = args[i + 1]; i += 1
			"--bloxy-user-id":
				if has_next: user_id = args[i + 1]; i += 1
			"--bloxy-server":
				if has_next: server_ip = args[i + 1]; i += 1
			"--bloxy-port":
				if has_next: server_port = int(args[i + 1]); i += 1
			"--bloxy-server-id":
				if has_next: server_id = args[i + 1]; i += 1
			"--bloxy-server-token":
				if has_next: server_token = args[i + 1]; i += 1
			"--bloxy-debug":
				_debug_mode = true
		i += 1

	if server_id == "":
		server_id = game_id + "_" + str(server_port)

# ============================================================
# SDK INFO
# ============================================================

func get_sdk_version() -> String:
	return SDK_VERSION

func get_sdk_build() -> int:
	return SDK_BUILD

func is_ready() -> bool:
	return game_id != "" and ticket != "" and api != ""

func supports(feature: String) -> bool:
	return feature in [
		"identity", "multiplayer", "chat", "reports", "blocks",
		"purchases", "rewards", "events", "sessions", "avatars",
		"debug", "anticheat", "leaderboards", "player_data",
		"notifications", "moderation", "rate_limiting",
		"input_validation", "encryption"
	]

func get_launch_status() -> Dictionary:
	return {
		"ready": is_ready(),
		"username": username,
		"user_id": user_id,
		"game_id": game_id,
		"api": api,
		"ticket": ticket != "",
		"multiplayer": is_multiplayer(),
		"server_ip": server_ip,
		"server_port": server_port,
		"connection_status": connection_status,
		"sdk_version": SDK_VERSION,
		"sdk_build": SDK_BUILD,
		"session_active": _session_active,
		"anticheat_enabled": _ac_enabled,
		"debug_mode": _debug_mode
	}

# ============================================================
# MULTIPLAYER / CONNECTION
# ============================================================

func connect_to_server() -> bool:
	if server_ip == "" or server_port <= 0:
		connection_status = "failed"
		bloxy_connection_failed.emit()
		_log_warn("connect_to_server: no server_ip or port.")
		return false

	connection_status = "connecting"
	_log_info("Connecting to %s:%d …" % [server_ip, server_port])

	var result := peer.create_client(server_ip, server_port)
	if result != OK:
		connection_status = "failed"
		bloxy_connection_failed.emit()
		_log_error("create_client failed: %d" % result)
		return false

	multiplayer.multiplayer_peer = peer
	return true

func disconnect_from_server() -> void:
	if peer and peer.get_connection_status() != ENetMultiplayerPeer.CONNECTION_DISCONNECTED:
		peer.close()

	connection_status = "disconnected"
	bloxy_disconnected.emit()
	_log_info("Disconnected from server.")

func start_server(port: int) -> bool:
	var srv_peer := ENetMultiplayerPeer.new()
	var result := srv_peer.create_server(port)
	if result != OK:
		_log_error("start_server: create_server failed on port %d (err %d)" % [port, result])
		return false

	multiplayer.multiplayer_peer = srv_peer
	server_port = port

	multiplayer.peer_connected.connect(_on_peer_connected)
	multiplayer.peer_disconnected.connect(_on_peer_disconnected)

	_log_info("Server started on port %d." % port)
	return true

func _on_connected_to_server() -> void:
	connection_status = "connected"
	bloxy_connected.emit()
	_log_info("Connected to server.")

func _on_connection_failed() -> void:
	connection_status = "failed"
	bloxy_connection_failed.emit()
	_log_error("Connection to server failed.")

func _on_server_disconnected() -> void:
	connection_status = "disconnected"
	bloxy_disconnected.emit()
	_log_warn("Server disconnected.")

func _on_peer_connected(peer_id: int) -> void:
	_connected_peers[peer_id] = {
		"peer_id": peer_id,
		"connected_at": Time.get_ticks_msec() / 1000.0,
		"verified": false
	}
	_ac_init_peer(peer_id)
	_log_info("Peer connected: %d" % peer_id)

func _on_peer_disconnected(peer_id: int) -> void:
	_connected_peers.erase(peer_id)
	_ac_cleanup_peer(peer_id)
	chat_users.erase(peer_id)
	_muted_peers.erase(peer_id)
	player_left(peer_id)
	_log_info("Peer disconnected: %d" % peer_id)

func get_connection_status() -> String:
	return connection_status

func is_multiplayer() -> bool:
	return server_ip != "" and server_port > 0

func get_connected_peers() -> Array:
	return _connected_peers.keys()

func get_peer_info(peer_id: int) -> Dictionary:
	return _connected_peers.get(peer_id, {})

func is_peer_verified(peer_id: int) -> bool:
	return _connected_peers.get(peer_id, {}).get("verified", false)

func get_peer_count() -> int:
	return _connected_peers.size()

# ============================================================
# IDENTITY GETTERS
# ============================================================

func get_username() -> String:
	return username

func get_user_id() -> String:
	return user_id

func get_game_id() -> String:
	return game_id

func get_ticket() -> String:
	return ticket

func get_api() -> String:
	return api

func get_server_ip() -> String:
	return server_ip

func get_server_port() -> int:
	return server_port

func get_server_id() -> String:
	return server_id

func get_avatar() -> Dictionary:
	return avatar

func get_blocks() -> int:
	return blocks

func has_ticket() -> bool:
	return ticket != ""

func has_api() -> bool:
	return api != ""

func has_server_token() -> bool:
	return server_token != ""

func get_auth_payload() -> Dictionary:
	return {
		"ticket":   ticket,
		"game_id":  game_id,
		"username": username,
		"user_id":  user_id
	}

# ============================================================
# HTTP HELPERS
# ============================================================

func api_url(path: String) -> String:
	if api == "":
		return ""

	var base := api
	while base.ends_with("/"):
		base = base.substr(0, base.length() - 1)

	if not path.begins_with("/"):
		path = "/" + path

	return base + path

## Rate-limited GET request.
## Returns false immediately if rate limit is exceeded.
func get_json(path: String, callback: Callable) -> void:
	if not _check_rate_limit(path):
		if callback.is_valid():
			callback.call(false, {"error": "Rate limited", "path": path}, 429)
		return

	var url := api_url(path)
	if url == "":
		if callback.is_valid():
			callback.call(false, {"error": "Missing API URL"}, 0)
		return

	var req := HTTPRequest.new()
	add_child(req)

	req.request_completed.connect(func(result, response_code, headers, body):
		var parsed := _parse_response_body(body)
		req.queue_free()
		if callback.is_valid():
			callback.call(response_code >= 200 and response_code < 300, parsed, response_code)
	)

	var err := req.request(url)
	if err != OK:
		req.queue_free()
		if callback.is_valid():
			callback.call(false, {"error": "Request failed", "code": err}, 0)

## Rate-limited POST request.
func post_json(path: String, data: Dictionary, callback: Callable, include_server_token := false) -> void:
	if not _check_rate_limit(path):
		if callback.is_valid():
			callback.call(false, {"error": "Rate limited", "path": path}, 429)
		return

	var url := api_url(path)
	if url == "":
		if callback.is_valid():
			callback.call(false, {"error": "Missing API URL"}, 0)
		return

	var req := HTTPRequest.new()
	add_child(req)

	var headers : PackedStringArray = ["Content-Type: application/json"]
	if include_server_token and server_token != "":
		headers.append("X-Bloxy-Server-Token: " + server_token)

	req.request_completed.connect(func(result, response_code, response_headers, body):
		var parsed := _parse_response_body(body)
		req.queue_free()
		if callback.is_valid():
			callback.call(response_code >= 200 and response_code < 300, parsed, response_code)
	)

	var body_str := JSON.stringify(data)
	var err := req.request(url, headers, HTTPClient.METHOD_POST, body_str)
	if err != OK:
		req.queue_free()
		if callback.is_valid():
			callback.call(false, {"error": "Request failed", "code": err}, 0)

## PUT request helper.
func put_json(path: String, data: Dictionary, callback: Callable, include_server_token := false) -> void:
	if not _check_rate_limit(path):
		if callback.is_valid():
			callback.call(false, {"error": "Rate limited", "path": path}, 429)
		return

	var url := api_url(path)
	if url == "":
		if callback.is_valid():
			callback.call(false, {"error": "Missing API URL"}, 0)
		return

	var req := HTTPRequest.new()
	add_child(req)

	var headers : PackedStringArray = ["Content-Type: application/json"]
	if include_server_token and server_token != "":
		headers.append("X-Bloxy-Server-Token: " + server_token)

	req.request_completed.connect(func(result, response_code, response_headers, body):
		var parsed := _parse_response_body(body)
		req.queue_free()
		if callback.is_valid():
			callback.call(response_code >= 200 and response_code < 300, parsed, response_code)
	)

	var body_str := JSON.stringify(data)
	var err := req.request(url, headers, HTTPClient.METHOD_PUT, body_str)
	if err != OK:
		req.queue_free()
		if callback.is_valid():
			callback.call(false, {"error": "Request failed", "code": err}, 0)

## DELETE request helper.
func delete_json(path: String, callback: Callable, include_server_token := false) -> void:
	if not _check_rate_limit(path):
		if callback.is_valid():
			callback.call(false, {"error": "Rate limited", "path": path}, 429)
		return

	var url := api_url(path)
	if url == "":
		if callback.is_valid():
			callback.call(false, {"error": "Missing API URL"}, 0)
		return

	var req := HTTPRequest.new()
	add_child(req)

	var headers : PackedStringArray = []
	if include_server_token and server_token != "":
		headers.append("X-Bloxy-Server-Token: " + server_token)

	req.request_completed.connect(func(result, response_code, response_headers, body):
		var parsed := _parse_response_body(body)
		req.queue_free()
		if callback.is_valid():
			callback.call(response_code >= 200 and response_code < 300, parsed, response_code)
	)

	var err := req.request(url, headers, HTTPClient.METHOD_DELETE)
	if err != OK:
		req.queue_free()
		if callback.is_valid():
			callback.call(false, {"error": "Request failed", "code": err}, 0)

func _parse_response_body(body: PackedByteArray) -> Dictionary:
	var text := body.get_string_from_utf8()
	if text == "":
		return {}

	var json := JSON.parse_string(text)
	if typeof(json) == TYPE_DICTIONARY:
		return json
	elif typeof(json) == TYPE_ARRAY:
		return {"data": json}
	else:
		return {"raw": text}

# ============================================================
# RATE LIMITING
# ============================================================

func _check_rate_limit(endpoint: String) -> bool:
	var now := Time.get_ticks_msec() / 1000.0

	if not _rate_limit_buckets.has(endpoint):
		_rate_limit_buckets[endpoint] = []

	var bucket : Array = _rate_limit_buckets[endpoint]

	# Remove old entries outside window
	bucket = bucket.filter(func(t): return now - t < _rate_limit_window)
	_rate_limit_buckets[endpoint] = bucket

	if bucket.size() >= _rate_limit_max:
		_log_warn("Rate limit hit for endpoint: %s" % endpoint)
		return false

	bucket.append(now)
	return true

## Adjust rate limiting parameters.
func set_rate_limit(max_calls: int, window_seconds: float) -> void:
	_rate_limit_max    = max_calls
	_rate_limit_window = window_seconds

func reset_rate_limit(endpoint: String = "") -> void:
	if endpoint == "":
		_rate_limit_buckets.clear()
	else:
		_rate_limit_buckets.erase(endpoint)

# ============================================================
# TICKET VERIFICATION
# ============================================================

func verify_ticket(peer_id: int, callback: Callable) -> void:
	var data := {
		"ticket":    ticket,
		"game_id":   game_id,
		"server_id": server_id,
		"peer_id":   str(peer_id)
	}

	post_json("/api/game-server/verify-ticket", data, func(ok, response, code):
		if ok and response.get("success", false):
			if response.has("avatar"):
				avatar = response.get("avatar", {})

			# Mark peer as verified
			if _connected_peers.has(peer_id):
				_connected_peers[peer_id]["verified"] = true
				_connected_peers[peer_id]["username"] = response.get("username", "")
				_connected_peers[peer_id]["user_id"]  = response.get("user_id", "")

		if callback.is_valid():
			callback.call(ok, response, code)
	, true)

## Verify a specific peer's ticket (server-side helper).
func verify_peer_ticket(peer_id: int, peer_ticket: String, peer_user_id: String, callback: Callable) -> void:
	var data := {
		"ticket":    peer_ticket,
		"game_id":   game_id,
		"server_id": server_id,
		"peer_id":   str(peer_id),
		"user_id":   peer_user_id
	}

	post_json("/api/game-server/verify-ticket", data, func(ok, response, code):
		if ok and response.get("success", false):
			if _connected_peers.has(peer_id):
				_connected_peers[peer_id]["verified"] = true

		if callback.is_valid():
			callback.call(ok, response, code)
	, true)

# ============================================================
# HEARTBEAT / SESSION PING
# ============================================================

func send_heartbeat(callback: Callable = Callable()) -> void:
	var data := {
		"game_id":   game_id,
		"server_id": server_id,
		"peer_count": get_peer_count()
	}

	post_json("/api/game-server/heartbeat", data, callback, true)

func _ping_session() -> void:
	var data := {
		"game_id":  game_id,
		"ticket":   ticket,
		"duration": int(Time.get_ticks_msec() / 1000.0 - _session_start_time)
	}

	post_json("/api/games/" + game_id + "/session/ping", data, func(_ok, _res, _code): pass)

func player_left(peer_id: int, callback: Callable = Callable()) -> void:
	var data := {
		"game_id":   game_id,
		"server_id": server_id,
		"peer_id":   str(peer_id)
	}

	post_json("/api/game-server/player-left", data, callback, true)

# ============================================================
# AVATAR
# ============================================================

func fetch_avatar(callback: Callable) -> void:
	verify_ticket(multiplayer.get_unique_id(), func(ok, response, code):
		if ok and response.has("avatar"):
			avatar = response.get("avatar", {})

		if callback.is_valid():
			callback.call(ok, avatar, code)
	)

func fetch_avatar_for_user(target_user_id: String, callback: Callable) -> void:
	get_json("/api/users/" + target_user_id + "/avatar", func(ok, response, code):
		var av : Dictionary = {}
		if ok and response.has("avatar"):
			av = response.get("avatar", {})

		if callback.is_valid():
			callback.call(ok, av, code)
	)

func get_avatar_color() -> Color:
	var hex : String = avatar.get("color", "ffffff")
	return Color(hex)

func get_avatar_display_name() -> String:
	return avatar.get("display_name", username)

func get_avatar_hat() -> String:
	return avatar.get("hat", "")

func get_avatar_skin() -> String:
	return avatar.get("skin", "default")

# ============================================================
# ECONOMY — Blocks
# ============================================================

func fetch_blocks(callback: Callable) -> void:
	var data := {
		"game_id": game_id,
		"ticket":  ticket
	}

	post_json("/api/users/me/blocks", data, func(ok, response, code):
		if ok and response.has("blocks"):
			blocks = int(response.get("blocks", 0))
			blocks_updated.emit(blocks)

		if callback.is_valid():
			callback.call(ok, response, code)
	)

func spend_blocks(amount: int, reason: String, callback: Callable) -> void:
	if amount <= 0:
		if callback.is_valid():
			callback.call(false, {"error": "Invalid amount"}, 0)
		return

	var data := {
		"game_id": game_id,
		"ticket":  ticket,
		"amount":  amount,
		"reason":  _sanitise(reason, MAX_REASON_LEN)
	}

	post_json("/api/users/me/spend-blocks", data, func(ok, response, code):
		if ok:
			blocks = max(0, blocks - amount)
			blocks_updated.emit(blocks)

		if callback.is_valid():
			callback.call(ok, response, code)
	)

# ============================================================
# ECONOMY — Purchases
# ============================================================

func purchase_item(item_id: Variant, price: int, callback: Callable) -> void:
	var data := {
		"game_id": game_id,
		"item_id": str(item_id),
		"price":   int(price),
		"ticket":  ticket
	}

	post_json("/api/games/" + game_id + "/purchase", data, func(ok, response, code):
		purchase_completed.emit(str(item_id), ok)
		if callback.is_valid():
			callback.call(ok, response, code)
	)

func purchase_product(product_id: Variant, callback: Callable) -> void:
	var data := {
		"game_id":    game_id,
		"product_id": str(product_id),
		"ticket":     ticket
	}

	post_json("/api/games/" + game_id + "/purchase-product", data, func(ok, response, code):
		purchase_completed.emit(str(product_id), ok)
		if callback.is_valid():
			callback.call(ok, response, code)
	)

## Check whether the local player owns an item.
func check_ownership(item_id: Variant, callback: Callable) -> void:
	get_json("/api/games/" + game_id + "/owns/" + str(item_id) + "?ticket=" + ticket, func(ok, response, code):
		var owns : bool = ok and response.get("owns", false)
		if callback.is_valid():
			callback.call(owns, response, code)
	)

## Fetch the player's full inventory.
func fetch_inventory(callback: Callable) -> void:
	var data := {
		"game_id": game_id,
		"ticket":  ticket
	}

	post_json("/api/users/me/inventory", data, func(ok, response, code):
		var inventory : Array = []
		if ok and response.has("items"):
			inventory = response.get("items", [])

		if callback.is_valid():
			callback.call(ok, inventory, code)
	)

# ============================================================
# ECONOMY — Rewards
# ============================================================

func grant_reward_request(reason: Variant, amount: int, callback: Callable) -> void:
	var data := {
		"game_id": game_id,
		"reason":  _sanitise(str(reason), MAX_REASON_LEN),
		"amount":  int(amount),
		"ticket":  ticket
	}

	post_json("/api/games/" + game_id + "/reward-request", data, func(ok, response, code):
		reward_granted.emit(str(reason), amount, ok)
		if callback.is_valid():
			callback.call(ok, response, code)
	)

## Server-side: grant blocks directly to a peer.
func server_grant_blocks(peer_user_id: String, amount: int, reason: String, callback: Callable = Callable()) -> void:
	if not multiplayer.is_server():
		_log_warn("server_grant_blocks: called from non-server!")
		return

	var data := {
		"game_id":        game_id,
		"server_id":      server_id,
		"target_user_id": peer_user_id,
		"amount":         int(amount),
		"reason":         _sanitise(reason, MAX_REASON_LEN)
	}

	post_json("/api/game-server/grant-blocks", data, func(ok, response, code):
		if callback.is_valid():
			callback.call(ok, response, code)
	, true)

# ============================================================
# REPORTS / ERRORS
# ============================================================

func report_player(target_user_id: Variant, reason: Variant, callback: Callable) -> void:
	var data := {
		"game_id":          game_id,
		"reporter_user_id": user_id,
		"target_user_id":   str(target_user_id),
		"reason":           _sanitise(str(reason), MAX_REASON_LEN),
		"ticket":           ticket
	}

	post_json("/api/reports/player", data, func(ok, response, code):
		player_reported.emit(str(target_user_id), ok)
		if callback.is_valid():
			callback.call(ok, response, code)
	)

func report_error(message: Variant, callback: Callable = Callable()) -> void:
	var data := {
		"game_id":  game_id,
		"user_id":  user_id,
		"username": username,
		"message":  _sanitise(str(message), MAX_REASON_LEN),
		"ticket":   ticket
	}

	post_json("/api/reports/error", data, callback)

func report_exception(context: Variant, error_message: Variant, callback: Callable = Callable()) -> void:
	report_error(str(context) + ": " + str(error_message), callback)

## Server-side: report a suspicious player for server-detected cheating.
func report_cheat(peer_user_id: String, violation: String, details: Dictionary, callback: Callable = Callable()) -> void:
	if not multiplayer.is_server():
		return

	var data := {
		"game_id":        game_id,
		"server_id":      server_id,
		"target_user_id": peer_user_id,
		"violation":      _sanitise(violation, 128),
		"details":        details
	}

	post_json("/api/reports/cheat", data, func(ok, response, code):
		if callback.is_valid():
			callback.call(ok, response, code)
	, true)

# ============================================================
# EVENTS / ANALYTICS
# ============================================================

func log_event(event_name: Variant, data := {}, callback: Callable = Callable()) -> void:
	var name_clean := _sanitise(str(event_name), MAX_EVENT_NAME_LEN)

	var payload := {
		"game_id": game_id,
		"ticket":  ticket,
		"event":   name_clean,
		"data":    data,
		"ts":      int(Time.get_unix_time_from_system())
	}

	post_json("/api/games/" + game_id + "/event", payload, func(ok, response, code):
		event_logged.emit(name_clean, ok)
		if callback.is_valid():
			callback.call(ok, response, code)
	)

## Batch-log multiple events in a single request.
func log_events_batch(events: Array, callback: Callable = Callable()) -> void:
	# events: Array of { "event": String, "data": Dictionary }
	var cleaned : Array = []
	for ev in events:
		if typeof(ev) == TYPE_DICTIONARY and ev.has("event"):
			cleaned.append({
				"event": _sanitise(str(ev.get("event", "")), MAX_EVENT_NAME_LEN),
				"data":  ev.get("data", {}),
				"ts":    int(Time.get_unix_time_from_system())
			})

	var payload := {
		"game_id": game_id,
		"ticket":  ticket,
		"events":  cleaned
	}

	post_json("/api/games/" + game_id + "/events-batch", payload, func(ok, response, code):
		if callback.is_valid():
			callback.call(ok, response, code)
	)

# ============================================================
# SESSIONS
# ============================================================

func session_start(callback: Callable = Callable()) -> void:
	var payload := {
		"game_id":     game_id,
		"ticket":      ticket,
		"sdk_version": SDK_VERSION,
		"ts":          int(Time.get_unix_time_from_system())
	}

	post_json("/api/games/" + game_id + "/session/start", payload, func(ok, response, code):
		if ok:
			_session_active     = true
			_session_start_time = Time.get_ticks_msec() / 1000.0

		session_started.emit(ok)

		if callback.is_valid():
			callback.call(ok, response, code)
	)

func session_end(callback: Callable = Callable()) -> void:
	var payload := {
		"game_id":  game_id,
		"ticket":   ticket,
		"duration": int(Time.get_ticks_msec() / 1000.0 - _session_start_time)
	}

	post_json("/api/games/" + game_id + "/session/end", payload, func(ok, response, code):
		_session_active = false
		session_ended.emit(ok)

		if callback.is_valid():
			callback.call(ok, response, code)
	)

func get_session_duration() -> float:
	if not _session_active:
		return 0.0
	return Time.get_ticks_msec() / 1000.0 - _session_start_time

func is_session_active() -> bool:
	return _session_active

# ============================================================
# LEADERBOARDS
# ============================================================

## Fetch entries for a leaderboard. Results are cached for _leaderboard_ttl seconds.
func fetch_leaderboard(board_id: String, limit: int = 25, callback: Callable = Callable()) -> void:
	limit = clampi(limit, 1, MAX_LEADERBOARD_ENTRIES)
	board_id = _sanitise(board_id, 64)

	# Check cache
	var now := Time.get_ticks_msec() / 1000.0
	if _leaderboard_cache.has(board_id):
		var cached : Dictionary = _leaderboard_cache[board_id]
		if now - cached.get("timestamp", 0.0) < _leaderboard_ttl:
			var entries : Array = cached.get("entries", [])
			leaderboard_fetched.emit(board_id, entries, true)
			if callback.is_valid():
				callback.call(true, entries, 200)
			return

	get_json("/api/games/" + game_id + "/leaderboard/" + board_id + "?limit=" + str(limit), func(ok, response, code):
		var entries : Array = []
		if ok:
			entries = response.get("entries", response.get("data", []))
			_leaderboard_cache[board_id] = {
				"entries":   entries,
				"timestamp": Time.get_ticks_msec() / 1000.0
			}

		leaderboard_fetched.emit(board_id, entries, ok)

		if callback.is_valid():
			callback.call(ok, entries, code)
	)

## Submit a score to a leaderboard.
func submit_leaderboard_score(board_id: String, score: float, metadata := {}, callback: Callable = Callable()) -> void:
	board_id = _sanitise(board_id, 64)

	var data := {
		"game_id":  game_id,
		"ticket":   ticket,
		"board_id": board_id,
		"score":    score,
		"metadata": metadata,
		"ts":       int(Time.get_unix_time_from_system())
	}

	post_json("/api/games/" + game_id + "/leaderboard/" + board_id + "/submit", data, func(ok, response, code):
		# Invalidate cache for this board
		_leaderboard_cache.erase(board_id)

		leaderboard_score_submitted.emit(board_id, ok)

		if callback.is_valid():
			callback.call(ok, response, code)
	)

## Fetch the local player's rank on a specific leaderboard.
func fetch_player_rank(board_id: String, callback: Callable) -> void:
	board_id = _sanitise(board_id, 64)

	get_json("/api/games/" + game_id + "/leaderboard/" + board_id + "/rank?ticket=" + ticket, func(ok, response, code):
		var rank : int = response.get("rank", -1)
		var score : float = response.get("score", 0.0)

		if callback.is_valid():
			callback.call(ok, rank, score, code)
	)

## Set the leaderboard cache TTL.
func set_leaderboard_ttl(seconds: float) -> void:
	_leaderboard_ttl = max(0.0, seconds)

## Invalidate all leaderboard caches.
func invalidate_leaderboard_cache(board_id: String = "") -> void:
	if board_id == "":
		_leaderboard_cache.clear()
	else:
		_leaderboard_cache.erase(board_id)

# ============================================================
# PLAYER DATA (Server-side persistent key-value store)
# ============================================================

## Load a value from the player's persistent data store.
func load_player_data(key: String, callback: Callable) -> void:
	key = _sanitise(key, 128)

	# Return cached value if available
	if _player_data_cache.has(key):
		var val = _player_data_cache[key]
		player_data_loaded.emit(key, val, true)
		if callback.is_valid():
			callback.call(true, val, 200)
		return

	var data := {
		"game_id": game_id,
		"ticket":  ticket,
		"key":     key
	}

	post_json("/api/games/" + game_id + "/player-data/get", data, func(ok, response, code):
		var val = response.get("value", null)
		if ok:
			_player_data_cache[key] = val

		player_data_loaded.emit(key, val, ok)

		if callback.is_valid():
			callback.call(ok, val, code)
	)

## Save a value to the player's persistent data store.
func save_player_data(key: String, value: Variant, callback: Callable = Callable()) -> void:
	key = _sanitise(key, 128)
	_player_data_cache[key] = value   # Optimistic cache update

	var data := {
		"game_id": game_id,
		"ticket":  ticket,
		"key":     key,
		"value":   value
	}

	post_json("/api/games/" + game_id + "/player-data/set", data, func(ok, response, code):
		if not ok:
			_player_data_cache.erase(key)   # Rollback cache on failure

		player_data_saved.emit(key, ok)

		if callback.is_valid():
			callback.call(ok, response, code)
	)

## Delete a key from the player's persistent data store.
func delete_player_data(key: String, callback: Callable = Callable()) -> void:
	key = _sanitise(key, 128)
	_player_data_cache.erase(key)

	var data := {
		"game_id": game_id,
		"ticket":  ticket,
		"key":     key
	}

	post_json("/api/games/" + game_id + "/player-data/delete", data, callback)

## Load all keys for the player.
func load_all_player_data(callback: Callable) -> void:
	var data := {
		"game_id": game_id,
		"ticket":  ticket
	}

	post_json("/api/games/" + game_id + "/player-data/all", data, func(ok, response, code):
		var all : Dictionary = {}
		if ok and response.has("data"):
			all = response.get("data", {})
			for k in all:
				_player_data_cache[k] = all[k]

		if callback.is_valid():
			callback.call(ok, all, code)
	)

## Clear the local player data cache.
func clear_player_data_cache() -> void:
	_player_data_cache.clear()

# ============================================================
# NOTIFICATIONS
# ============================================================

func _poll_notifications() -> void:
	if not is_ready():
		return

	get_json("/api/users/me/notifications?ticket=" + ticket + "&game_id=" + game_id, func(ok, response, _code):
		if ok and response.has("notifications"):
			var notifs : Array = response.get("notifications", [])
			for n in notifs:
				if typeof(n) == TYPE_DICTIONARY:
					var title : String = n.get("title", "")
					var body  : String = n.get("body", "")
					var data  : Dictionary = n.get("data", {})
					notification_received.emit(title, body, data)
					_notification_queue.append(n)

					# Auto-acknowledge
					var nid = n.get("id", "")
					if nid != "":
						_acknowledge_notification(str(nid))
	)

func _acknowledge_notification(notification_id: String) -> void:
	var data := {
		"game_id":         game_id,
		"ticket":          ticket,
		"notification_id": notification_id
	}

	post_json("/api/users/me/notifications/ack", data, func(_ok, _res, _code): pass)

func get_pending_notifications() -> Array:
	return _notification_queue.duplicate()

func clear_notification_queue() -> void:
	_notification_queue.clear()

func set_notification_poll_interval(seconds: float) -> void:
	_notification_poll_interval = max(5.0, seconds)

# ============================================================
# MODERATION
# ============================================================

## Ban a peer from this server session (does NOT persist to backend).
func local_ban_peer(peer_id: int, reason: String) -> void:
	if not multiplayer.is_server():
		return

	reason = _sanitise(reason, MAX_REASON_LEN)
	_banned_peers[peer_id] = reason
	player_banned.emit(peer_id, reason)

	# Kick the peer
	_ac_kick_peer(peer_id, "banned: " + reason)
	_log_warn("Peer %d locally banned: %s" % [peer_id, reason])

## Ban a user ID from this server session.
func local_ban_user_id(target_user_id: String, reason: String) -> void:
	if not multiplayer.is_server():
		return

	target_user_id = _sanitise(target_user_id, 64)
	reason         = _sanitise(reason, MAX_REASON_LEN)
	_banned_user_ids[target_user_id] = reason

	# Kick any currently connected peer with this user_id
	for pid in _connected_peers:
		if _connected_peers[pid].get("user_id", "") == target_user_id:
			_ac_kick_peer(pid, "banned: " + reason)

	_log_warn("User ID %s locally banned: %s" % [target_user_id, reason])

## Submit a permanent ban request to the backend (server-auth required).
func submit_ban(target_user_id: String, reason: String, duration_hours: float, callback: Callable = Callable()) -> void:
	if not multiplayer.is_server():
		_log_warn("submit_ban: called from non-server!")
		return

	var data := {
		"game_id":        game_id,
		"server_id":      server_id,
		"target_user_id": target_user_id,
		"reason":         _sanitise(reason, MAX_REASON_LEN),
		"duration_hours": duration_hours
	}

	post_json("/api/moderation/ban", data, func(ok, response, code):
		if callback.is_valid():
			callback.call(ok, response, code)
	, true)

func is_peer_banned(peer_id: int) -> bool:
	return _banned_peers.has(peer_id)

func is_user_id_banned(target_user_id: String) -> bool:
	return _banned_user_ids.has(target_user_id)

# ============================================================
# CHAT
# ============================================================

func setup_chat() -> void:
	if chat_ready:
		return

	chat_ready = true

	if not multiplayer.peer_connected.is_connected(_bloxy_chat_peer_connected):
		multiplayer.peer_connected.connect(_bloxy_chat_peer_connected)

	if not multiplayer.peer_disconnected.is_connected(_bloxy_chat_peer_disconnected):
		multiplayer.peer_disconnected.connect(_bloxy_chat_peer_disconnected)

	if multiplayer.is_server():
		chat_users[multiplayer.get_unique_id()] = {
			"username": "server",
			"user_id":  "server"
		}

	_log_info("Chat setup complete.")

func register_chat_identity() -> bool:
	if multiplayer.multiplayer_peer == null:
		return false

	if multiplayer.is_server():
		chat_users[multiplayer.get_unique_id()] = {
			"username": username,
			"user_id":  user_id
		}
		return true

	rpc_id(1, "_bloxy_server_register_chat_identity", username, user_id, ticket)
	return true

func send_chat_message(message: String) -> bool:
	var now := Time.get_ticks_msec() / 1000.0

	if now - last_chat_time < chat_cooldown_seconds:
		return false

	last_chat_time = now

	message = _bloxy_clean_chat_message(message)
	if message == "":
		return false

	# Client-side filter
	if _filter_enabled:
		message = filter_message(message)

	if multiplayer.multiplayer_peer == null:
		return false

	if multiplayer.is_server():
		_bloxy_broadcast_chat(username, user_id, message, multiplayer.get_unique_id())
		return true

	rpc_id(1, "_bloxy_server_receive_chat", message)
	return true

## Mute a peer for a given duration (0 = permanent until unmute_peer).
func mute_peer(peer_id: int, reason: String, duration_seconds: float = 0.0) -> void:
	if not multiplayer.is_server():
		return

	reason = _sanitise(reason, MAX_REASON_LEN)
	var until := 0.0
	if duration_seconds > 0.0:
		until = Time.get_ticks_msec() / 1000.0 + duration_seconds

	_muted_peers[peer_id] = { "reason": reason, "until": until }
	chat_muted.emit(peer_id, reason, duration_seconds)
	_bloxy_send_system_to_peer(peer_id, "You have been muted: " + reason)
	_log_info("Peer %d muted: %s (%.0fs)" % [peer_id, reason, duration_seconds])

func unmute_peer(peer_id: int) -> void:
	if not multiplayer.is_server():
		return

	if _muted_peers.has(peer_id):
		_muted_peers.erase(peer_id)
		chat_unmuted.emit(peer_id)

func is_peer_muted(peer_id: int) -> bool:
	return _muted_peers.has(peer_id)

func get_chat_users() -> Dictionary:
	return chat_users.duplicate()

func _bloxy_clean_chat_message(message: String) -> String:
	message = message.strip_edges()
	if message.length() > max_chat_length:
		message = message.substr(0, max_chat_length)
	message = message.replace("\n", " ").replace("\r", " ").replace("\t", " ")
	return message

func _bloxy_chat_peer_connected(peer_id: int) -> void:
	if multiplayer.is_server():
		chat_system_message_received.emit("peer " + str(peer_id) + " connected")

func _bloxy_chat_peer_disconnected(peer_id: int) -> void:
	if multiplayer.is_server():
		chat_users.erase(peer_id)
		_bloxy_broadcast_system("peer " + str(peer_id) + " left")

func _bloxy_send_system_to_peer(peer_id: int, message: String) -> void:
	if not multiplayer.is_server():
		return
	rpc_id(peer_id, "_bloxy_client_receive_system", message)

@rpc("any_peer", "reliable")
func _bloxy_server_register_chat_identity(player_username: String, player_user_id: String, player_ticket: String) -> void:
	if not multiplayer.is_server():
		return

	var pid := multiplayer.get_remote_sender_id()

	# Check if banned
	if is_peer_banned(pid) or is_user_id_banned(player_user_id):
		_ac_kick_peer(pid, "banned")
		return

	player_username = _bloxy_clean_chat_message(player_username)
	player_user_id  = _sanitise(str(player_user_id), 64)

	if player_username == "":
		player_username = "player_" + str(pid)

	chat_users[pid] = {
		"username": player_username,
		"user_id":  player_user_id,
		"ticket":   _sanitise(str(player_ticket), 512)
	}

	_bloxy_broadcast_system(player_username + " joined")

@rpc("any_peer", "reliable")
func _bloxy_server_receive_chat(message: String) -> void:
	if not multiplayer.is_server():
		return

	var pid := multiplayer.get_remote_sender_id()

	if is_peer_muted(pid):
		return

	if is_peer_banned(pid):
		_ac_kick_peer(pid, "banned")
		return

	# Anticheat: track chat rate
	_ac_record_action(pid, "chat")
	if _ac_is_action_spamming(pid, "chat", 5.0):
		mute_peer(pid, "Chat spam detected (anticheat)", 30.0)
		return

	message = _bloxy_clean_chat_message(message)
	if message == "":
		return

	if _filter_enabled:
		message = filter_message(message)

	var profile : Dictionary = chat_users.get(pid, {
		"username": "player_" + str(pid),
		"user_id":  ""
	})

	_bloxy_broadcast_chat(
		profile.get("username", "player_" + str(pid)),
		profile.get("user_id", ""),
		message,
		pid
	)

func _bloxy_broadcast_chat(player_username: String, player_user_id: String, message: String, pid: int) -> void:
	if not multiplayer.is_server():
		return

	rpc("_bloxy_client_receive_chat", player_username, player_user_id, message, pid)
	_bloxy_client_receive_chat(player_username, player_user_id, message, pid)

func _bloxy_broadcast_system(message: String) -> void:
	if not multiplayer.is_server():
		return

	rpc("_bloxy_client_receive_system", message)
	_bloxy_client_receive_system(message)

@rpc("authority", "reliable")
func _bloxy_client_receive_chat(player_username: String, player_user_id: String, message: String, pid: int) -> void:
	chat_message_received.emit(player_username, player_user_id, message, pid)

@rpc("authority", "reliable")
func _bloxy_client_receive_system(message: String) -> void:
	chat_system_message_received.emit(message)

# ============================================================
# INPUT VALIDATION / FILTERING
# ============================================================

## Load a newline-separated list of words to filter from chat.
func load_profanity_list(words: Array) -> void:
	_profanity_list = words.map(func(w): return str(w).to_lower())

func load_profanity_list_from_file(path: String) -> void:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		_log_warn("Could not open profanity list: %s" % path)
		return

	var content := f.get_as_text()
	f.close()

	_profanity_list = content.split("\n", false)
	_profanity_list = _profanity_list.map(func(w): return w.strip_edges().to_lower())
	_profanity_list = _profanity_list.filter(func(w): return w != "")

	_log_info("Loaded %d profanity words." % _profanity_list.size())

## Replace filtered words with asterisks.
func filter_message(message: String) -> String:
	if not _filter_enabled or _profanity_list.is_empty():
		return message

	var lower := message.to_lower()
	for word in _profanity_list:
		if word == "":
			continue
		var idx := lower.find(word)
		while idx != -1:
			var stars := "*".repeat(word.length())
			message = message.substr(0, idx) + stars + message.substr(idx + word.length())
			lower   = lower.substr(0, idx) + stars + lower.substr(idx + word.length())
			idx = lower.find(word, idx + word.length())

	return message

func set_filter_enabled(enabled: bool) -> void:
	_filter_enabled = enabled

func is_filter_enabled() -> bool:
	return _filter_enabled

## Validate a username string.
func validate_username(name: String) -> Dictionary:
	var result := { "valid": true, "reason": "" }

	if name.length() < 3:
		return { "valid": false, "reason": "too_short" }

	if name.length() > MAX_USERNAME_LEN:
		return { "valid": false, "reason": "too_long" }

	var allowed := RegEx.new()
	allowed.compile("^[a-zA-Z0-9_\\-]+$")
	if not allowed.search(name):
		return { "valid": false, "reason": "invalid_characters" }

	return result

## Sanitise a string to a maximum length, stripping dangerous characters.
func _sanitise(value: String, max_len: int) -> String:
	value = value.strip_edges()
	value = value.replace("\x00", "")   # null bytes
	if value.length() > max_len:
		value = value.substr(0, max_len)
	return value

## Validate that a value is within an expected numeric range.
func validate_number(value: float, min_val: float, max_val: float) -> bool:
	return value >= min_val and value <= max_val

## Validate a dictionary has required keys.
func validate_dict(data: Dictionary, required_keys: Array) -> bool:
	for k in required_keys:
		if not data.has(k):
			return false
	return true

# ============================================================
# ENCRYPTION HELPERS
# ============================================================
# Lightweight helpers for simple XOR obfuscation of local data.
# For proper encryption, use Godot's built-in crypto or a server-side solution.

## XOR-obfuscate a string with a key (not cryptographically secure).
func xor_obfuscate(data: String, key: String) -> PackedByteArray:
	var bytes := data.to_utf8_buffer()
	var key_bytes := key.to_utf8_buffer()
	var result := PackedByteArray()
	result.resize(bytes.size())

	for i in range(bytes.size()):
		result[i] = bytes[i] ^ key_bytes[i % key_bytes.size()]

	return result

## Decode an XOR-obfuscated buffer back to a string.
func xor_deobfuscate(data: PackedByteArray, key: String) -> String:
	var key_bytes := key.to_utf8_buffer()
	var result := PackedByteArray()
	result.resize(data.size())

	for i in range(data.size()):
		result[i] = data[i] ^ key_bytes[i % key_bytes.size()]

	return result.get_string_from_utf8()

## Generate a simple checksum of a string (djb2 hash).
func checksum_djb2(data: String) -> int:
	var hash_val : int = 5381
	for ch in data.to_utf8_buffer():
		hash_val = ((hash_val << 5) + hash_val) + ch
		hash_val &= 0xFFFFFFFF
	return hash_val

## Generate a checksum of a Dictionary (for tamper detection).
func checksum_dict(data: Dictionary) -> int:
	return checksum_djb2(JSON.stringify(data))

## Encode bytes to base64.
func to_base64(data: PackedByteArray) -> String:
	return Marshalls.raw_to_base64(data)

## Decode base64 to bytes.
func from_base64(encoded: String) -> PackedByteArray:
	return Marshalls.base64_to_raw(encoded)

# ============================================================
# ANTICHEAT — Configuration
# ============================================================

func set_anticheat_enabled(enabled: bool) -> void:
	_ac_enabled = enabled
	_log_info("Anticheat %s." % ("enabled" if enabled else "disabled"))

func is_anticheat_enabled() -> bool:
	return _ac_enabled

func set_ac_max_speed(speed: float) -> void:
	_ac_max_speed = speed

func set_ac_teleport_distance(dist: float) -> void:
	_ac_teleport_dist = dist

func set_ac_max_actions_per_second(rate: float) -> void:
	_ac_max_actions_per_sec = rate

func set_ac_kick_threshold(flags: int) -> void:
	_ac_kick_threshold = flags

## Register a server-enforced stat cap (e.g. health capped at 100).
func register_stat_cap(stat_name: String, max_value: float) -> void:
	_ac_stat_caps[stat_name] = max_value

func remove_stat_cap(stat_name: String) -> void:
	_ac_stat_caps.erase(stat_name)

func get_peer_flags(peer_id: int) -> int:
	return _ac_peer_flags.get(peer_id, 0)

func get_ac_log() -> Array:
	return _ac_log.duplicate()

func clear_ac_log() -> void:
	_ac_log.clear()

# ============================================================
# ANTICHEAT — Internal State Management
# ============================================================

func _ac_init_peer(peer_id: int) -> void:
	_ac_peer_flags[peer_id] = 0
	_ac_peer_data[peer_id] = {
		"last_pos":          Vector3.ZERO,
		"last_pos_time":     Time.get_ticks_msec() / 1000.0,
		"action_log":        {},   # action_name → Array[float] (timestamps)
		"reported_stats":    {},   # stat_name → value
		"ping_times":        [],
		"checksum_failures": 0,
		"speed_violations":  0,
		"stat_violations":   0,
		"join_time":         Time.get_ticks_msec() / 1000.0
	}

func _ac_cleanup_peer(peer_id: int) -> void:
	_ac_peer_flags.erase(peer_id)
	_ac_peer_data.erase(peer_id)

# ============================================================
# ANTICHEAT — Server Tick
# ============================================================

func _ac_run_server_tick() -> void:
	var now := Time.get_ticks_msec() / 1000.0

	for peer_id in _ac_peer_data.keys():
		var state : Dictionary = _ac_peer_data[peer_id]

		# Clean old action timestamps to avoid memory bloat
		for action in state["action_log"]:
			var timestamps : Array = state["action_log"][action]
			state["action_log"][action] = timestamps.filter(func(t): return now - t < 10.0)

		# Grace period — skip checks for the first 3 seconds after join
		if now - state.get("join_time", 0.0) < 3.0:
			continue

		# Escalate repeated violations
		if state.get("speed_violations", 0) > 10:
			_ac_flag_peer(peer_id, "repeated_speed_violation", {
				"count": state["speed_violations"]
			})
			state["speed_violations"] = 0

		if state.get("stat_violations", 0) > 5:
			_ac_flag_peer(peer_id, "repeated_stat_violation", {
				"count": state["stat_violations"]
			})
			state["stat_violations"] = 0

# ============================================================
# ANTICHEAT — Position / Movement Checks
# ============================================================

## Call on the server when a client reports its position.
## Returns true if the position appears legitimate.
func ac_check_position(peer_id: int, new_pos: Vector3) -> bool:
	if not multiplayer.is_server() or not _ac_enabled:
		return true

	if not _ac_peer_data.has(peer_id):
		return true

	var state  : Dictionary = _ac_peer_data[peer_id]
	var now    := Time.get_ticks_msec() / 1000.0
	var dt     := now - state.get("last_pos_time", now)

	if dt <= 0.001:
		state["last_pos"]      = new_pos
		state["last_pos_time"] = now
		return true

	var last_pos : Vector3 = state.get("last_pos", new_pos)
	var dist     := last_pos.distance_to(new_pos)
	var speed    := dist / dt

	state["last_pos"]      = new_pos
	state["last_pos_time"] = now

	# Teleportation check
	if dist > _ac_teleport_dist:
		_ac_flag_peer(peer_id, "teleport", {
			"from": str(last_pos),
			"to":   str(new_pos),
			"dist": snappedf(dist, 0.01)
		})
		state["speed_violations"] = state.get("speed_violations", 0) + 1
		return false

	# Speed check
	if speed > _ac_max_speed:
		state["speed_violations"] = state.get("speed_violations", 0) + 1

		# Only flag after 3 consecutive violations to avoid false positives from lag
		if state["speed_violations"] >= 3:
			_ac_flag_peer(peer_id, "speed_hack", {
				"speed": snappedf(speed, 0.01),
				"max":   _ac_max_speed,
				"dt":    snappedf(dt, 0.001)
			})
		return false

	# Reset consecutive violations on clean tick
	state["speed_violations"] = max(0, state.get("speed_violations", 0) - 1)
	return true

## RPC: client reports its position to the server.
@rpc("any_peer", "unreliable_ordered")
func _bloxy_ac_server_report_position(pos_x: float, pos_y: float, pos_z: float) -> void:
	if not multiplayer.is_server():
		return

	var pid := multiplayer.get_remote_sender_id()
	var pos  := Vector3(pos_x, pos_y, pos_z)
	ac_check_position(pid, pos)

## Client: send your position to the server for validation.
func ac_report_position(pos: Vector3) -> void:
	if multiplayer.multiplayer_peer == null or multiplayer.is_server():
		return

	rpc_id(1, "_bloxy_ac_server_report_position", pos.x, pos.y, pos.z)

# ============================================================
# ANTICHEAT — Action Rate Checks
# ============================================================

func _ac_record_action(peer_id: int, action: String) -> void:
	if not _ac_peer_data.has(peer_id):
		return

	var state  : Dictionary = _ac_peer_data[peer_id]
	var log    : Dictionary = state["action_log"]
	var now    := Time.get_ticks_msec() / 1000.0

	if not log.has(action):
		log[action] = []

	log[action].append(now)

func _ac_is_action_spamming(peer_id: int, action: String, rate_per_second: float) -> bool:
	if not _ac_peer_data.has(peer_id):
		return false

	var state     : Dictionary = _ac_peer_data[peer_id]
	var log       : Dictionary = state["action_log"]
	var now       := Time.get_ticks_msec() / 1000.0
	var window    := 1.0
	var timestamps : Array = log.get(action, [])

	var recent := timestamps.filter(func(t): return now - t <= window)
	return recent.size() > rate_per_second

## Server-side: record and validate a player action.
func ac_check_action(peer_id: int, action: String) -> bool:
	if not multiplayer.is_server() or not _ac_enabled:
		return true

	_ac_record_action(peer_id, action)

	if _ac_is_action_spamming(peer_id, action, _ac_max_actions_per_sec):
		_ac_flag_peer(peer_id, "action_spam", {
			"action": action,
			"rate":   _ac_max_actions_per_sec
		})
		return false

	return true

## RPC: client reports an action to the server.
@rpc("any_peer", "reliable")
func _bloxy_ac_server_report_action(action: String) -> void:
	if not multiplayer.is_server():
		return

	var pid := multiplayer.get_remote_sender_id()
	action  = _sanitise(action, 64)
	ac_check_action(pid, action)

## Client: report an action to the server.
func ac_report_action(action: String) -> void:
	if multiplayer.multiplayer_peer == null or multiplayer.is_server():
		return

	rpc_id(1, "_bloxy_ac_server_report_action", _sanitise(action, 64))

# ============================================================
# ANTICHEAT — Stat Validation
# ============================================================

## Server-side: validate a reported stat value against registered caps.
func ac_check_stat(peer_id: int, stat_name: String, value: float) -> bool:
	if not multiplayer.is_server() or not _ac_enabled:
		return true

	if not _ac_peer_data.has(peer_id):
		return true

	var state : Dictionary = _ac_peer_data[peer_id]
	state["reported_stats"][stat_name] = value

	if not _ac_stat_caps.has(stat_name):
		return true   # No cap registered for this stat

	var cap : float = _ac_stat_caps[stat_name]
	if value > cap:
		state["stat_violations"] = state.get("stat_violations", 0) + 1
		_ac_flag_peer(peer_id, "stat_overflow", {
			"stat":  stat_name,
			"value": value,
			"cap":   cap
		})
		return false

	return true

## RPC: client reports a stat value to the server.
@rpc("any_peer", "reliable")
func _bloxy_ac_server_report_stat(stat_name: String, value: float) -> void:
	if not multiplayer.is_server():
		return

	var pid  := multiplayer.get_remote_sender_id()
	stat_name = _sanitise(stat_name, 64)
	ac_check_stat(pid, stat_name, value)

## Client: report a stat to the server for validation.
func ac_report_stat(stat_name: String, value: float) -> void:
	if multiplayer.multiplayer_peer == null or multiplayer.is_server():
		return

	rpc_id(1, "_bloxy_ac_server_report_stat", _sanitise(stat_name, 64), value)

# ============================================================
# ANTICHEAT — Checksum Integrity
# ============================================================

## Server-side: verify that a client-submitted checksum matches the expected value.
## Use to detect tampering with authoritative data (e.g. inventory, score).
func ac_verify_checksum(peer_id: int, data: Dictionary, submitted_checksum: int) -> bool:
	if not multiplayer.is_server() or not _ac_enabled:
		return true

	var expected := checksum_dict(data)
	if submitted_checksum != expected:
		if _ac_peer_data.has(peer_id):
			_ac_peer_data[peer_id]["checksum_failures"] = _ac_peer_data[peer_id].get("checksum_failures", 0) + 1

		_ac_flag_peer(peer_id, "checksum_mismatch", {
			"submitted": submitted_checksum,
			"expected":  expected
		})
		return false

	return true

## RPC: client sends a checksum of critical data.
@rpc("any_peer", "reliable")
func _bloxy_ac_server_report_checksum(data_json: String, submitted_checksum: int) -> void:
	if not multiplayer.is_server():
		return

	var pid := multiplayer.get_remote_sender_id()
	var parsed := JSON.parse_string(data_json)

	if typeof(parsed) != TYPE_DICTIONARY:
		_ac_flag_peer(pid, "invalid_checksum_payload", {})
		return

	ac_verify_checksum(pid, parsed, submitted_checksum)

## Client: send a checksum of local critical data to the server.
func ac_report_checksum(data: Dictionary) -> void:
	if multiplayer.multiplayer_peer == null or multiplayer.is_server():
		return

	var cs := checksum_dict(data)
	rpc_id(1, "_bloxy_ac_server_report_checksum", JSON.stringify(data), cs)

# ============================================================
# ANTICHEAT — Ping / Latency Tracking
# ============================================================

## Record a peer's ping time (call from your own ping system).
func ac_record_ping(peer_id: int, ping_ms: float) -> void:
	if not _ac_peer_data.has(peer_id):
		return

	var state  : Dictionary = _ac_peer_data[peer_id]
	var pings  : Array      = state["ping_times"]
	pings.append(ping_ms)

	# Keep only last 10 samples
	if pings.size() > 10:
		pings.pop_front()

	# Check for suspiciously negative or extreme ping (potential clock manipulation)
	if ping_ms < 0.0 or ping_ms > 10000.0:
		_ac_flag_peer(peer_id, "suspicious_ping", {
			"ping_ms": ping_ms
		})

func get_peer_average_ping(peer_id: int) -> float:
	if not _ac_peer_data.has(peer_id):
		return -1.0

	var pings : Array = _ac_peer_data[peer_id].get("ping_times", [])
	if pings.is_empty():
		return -1.0

	var total : float = 0.0
	for p in pings:
		total += p

	return total / pings.size()

# ============================================================
# ANTICHEAT — Flagging / Kicking
# ============================================================

func _ac_flag_peer(peer_id: int, violation_type: String, details: Dictionary) -> void:
	if not _ac_peer_flags.has(peer_id):
		_ac_peer_flags[peer_id] = 0

	_ac_peer_flags[peer_id] += 1
	var flag_count : int = _ac_peer_flags[peer_id]

	# Log violation
	var entry := {
		"peer_id":        peer_id,
		"violation":      violation_type,
		"details":        details,
		"flags":          flag_count,
		"timestamp":      Time.get_ticks_msec() / 1000.0
	}

	_ac_log.append(entry)
	if _ac_log.size() > 200:
		_ac_log.pop_front()

	ac_violation_detected.emit(peer_id, violation_type, details)
	ac_player_flagged.emit(peer_id, flag_count)

	_log_warn("[ANTICHEAT] Peer %d flagged (%s) — flags: %d — %s" % [
		peer_id, violation_type, flag_count, str(details)
	])

	# Warn the client
	if flag_count >= ceili(_ac_kick_threshold / 2.0):
		_ac_warn_peer(peer_id)

	# Kick the client
	if flag_count >= _ac_kick_threshold:
		_ac_kick_peer(peer_id, violation_type)

		# Report to backend
		var peer_info : Dictionary = _connected_peers.get(peer_id, {})
		var uid : String = peer_info.get("user_id", chat_users.get(peer_id, {}).get("user_id", ""))
		if uid != "":
			report_cheat(uid, violation_type, details)

func _ac_warn_peer(peer_id: int) -> void:
	if not multiplayer.is_server():
		return

	rpc_id(peer_id, "_bloxy_ac_client_warning", "Suspicious activity detected.")

func _ac_kick_peer(peer_id: int, reason: String) -> void:
	if not multiplayer.is_server():
		return

	rpc_id(peer_id, "_bloxy_ac_client_kick", reason)

	# Allow message to arrive before disconnecting
	await get_tree().create_timer(0.3).timeout

	if multiplayer.multiplayer_peer != null:
		multiplayer.multiplayer_peer.disconnect_peer(peer_id)

	ac_player_kicked.emit(peer_id, reason)
	_log_warn("[ANTICHEAT] Peer %d kicked: %s" % [peer_id, reason])

@rpc("authority", "reliable")
func _bloxy_ac_client_warning(message: String) -> void:
	_log_warn("[ANTICHEAT] Server warning: %s" % message)

@rpc("authority", "reliable")
func _bloxy_ac_client_kick(reason: String) -> void:
	_log_warn("[ANTICHEAT] Kicked by server: %s" % reason)
	disconnect_from_server()

# ============================================================
# ANTICHEAT — Convenience: Register a game object to be monitored
# ============================================================

## Attach automatic position reporting to a Node3D.
## Call once after the node is ready on the client.
## The node's global_position will be sent to the server every `interval` seconds.
func ac_monitor_node_position(node: Node3D, interval: float = 0.1) -> void:
	if multiplayer.is_server():
		return

	var timer := Timer.new()
	timer.wait_time = max(0.05, interval)
	timer.autostart = true
	node.add_child(timer)

	timer.timeout.connect(func():
		if is_instance_valid(node):
			ac_report_position(node.global_position)
		else:
			timer.queue_free()
	)

# ============================================================
# SERVER ANALYTICS
# ============================================================

## Push server-side analytics (CPU, memory, player count, etc.) to the backend.
func push_server_analytics(extra: Dictionary = {}, callback: Callable = Callable()) -> void:
	if not multiplayer.is_server():
		return

	var data : Dictionary = {
		"game_id":     game_id,
		"server_id":   server_id,
		"peer_count":  get_peer_count(),
		"uptime":      int(Time.get_ticks_msec() / 1000.0),
		"static_mem":  OS.get_static_memory_usage(),
		"sdk_version": SDK_VERSION
	}

	for k in extra:
		data[k] = extra[k]

	post_json("/api/game-server/analytics", data, func(ok, response, code):
		if callback.is_valid():
			callback.call(ok, response, code)
	, true)

## Get a snapshot of current server stats.
func get_server_stats() -> Dictionary:
	return {
		"peer_count":   get_peer_count(),
		"uptime_s":     int(Time.get_ticks_msec() / 1000.0),
		"static_mem":   OS.get_static_memory_usage(),
		"ac_flags":     _ac_peer_flags.duplicate(),
		"muted_peers":  _muted_peers.keys(),
		"banned_peers": _banned_peers.keys()
	}

# ============================================================
# DEBUG / LOGGING
# ============================================================

func set_debug_mode(enabled: bool) -> void:
	_debug_mode = enabled

func is_debug_mode() -> bool:
	return _debug_mode

func _log_info(message: String) -> void:
	var entry := "[INFO] [Bloxy] " + message
	_log_buffer.append(entry)
	_trim_log()

	if _debug_mode:
		print(entry)

func _log_warn(message: String) -> void:
	var entry := "[WARN] [Bloxy] " + message
	_log_buffer.append(entry)
	_trim_log()

	if _debug_mode:
		push_warning(entry)
	else:
		print(entry)

func _log_error(message: String) -> void:
	var entry := "[ERROR] [Bloxy] " + message
	_log_buffer.append(entry)
	_trim_log()
	push_error(entry)

func _trim_log() -> void:
	while _log_buffer.size() > _max_log_lines:
		_log_buffer.pop_front()

func get_log() -> Array:
	return _log_buffer.duplicate()

func clear_log() -> void:
	_log_buffer.clear()

func export_log_to_file(path: String) -> bool:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		_log_warn("export_log_to_file: cannot open %s" % path)
		return false

	for line in _log_buffer:
		f.store_line(line)

	f.close()
	return true

func debug_dump() -> void:
	print("──────────────────────────────────────────")
	print("  Bloxy SDK Debug Dump  v%s" % SDK_VERSION)
	print("──────────────────────────────────────────")
	print("  username        : ", username)
	print("  user_id         : ", user_id)
	print("  game_id         : ", game_id)
	print("  ticket          : ", ticket != "")
	print("  api             : ", api)
	print("  server_ip       : ", server_ip)
	print("  server_port     : ", server_port)
	print("  server_id       : ", server_id)
	print("  server_token    : ", server_token != "")
	print("  multiplayer     : ", is_multiplayer())
	print("  ready           : ", is_ready())
	print("  connection      : ", connection_status)
	print("  session_active  : ", _session_active)
	print("  peer_count      : ", get_peer_count())
	print("  ac_enabled      : ", _ac_enabled)
	print("  ac_flags        : ", _ac_peer_flags)
	print("  muted_peers     : ", _muted_peers.keys())
	print("  banned_peers    : ", _banned_peers.keys())
	print("  filter_enabled  : ", _filter_enabled)
	print("  blocks          : ", blocks)
	print("  debug_mode      : ", _debug_mode)
	print("──────────────────────────────────────────")

func debug_dump_as_dict() -> Dictionary:
	return {
		"sdk_version":      SDK_VERSION,
		"sdk_build":        SDK_BUILD,
		"username":         username,
		"user_id":          user_id,
		"game_id":          game_id,
		"has_ticket":       ticket != "",
		"api":              api,
		"server_ip":        server_ip,
		"server_port":      server_port,
		"server_id":        server_id,
		"has_server_token": server_token != "",
		"multiplayer":      is_multiplayer(),
		"ready":            is_ready(),
		"connection":       connection_status,
		"session_active":   _session_active,
		"peer_count":       get_peer_count(),
		"ac_enabled":       _ac_enabled,
		"ac_flags":         _ac_peer_flags.duplicate(),
		"muted_peers":      _muted_peers.keys(),
		"banned_peers":     _banned_peers.keys(),
		"filter_enabled":   _filter_enabled,
		"blocks":           blocks,
		"debug_mode":       _debug_mode
	}
