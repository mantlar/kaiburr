"""
Route handlers for the WebAdmin REST API.
All routes assume auth middleware has already run and request['user'] is populated.
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from aiohttp import web
from aiohttp.web import Response

from .middleware import require_smod_level, get_client_ip, SESSION_COOKIE
from . import auth
from . import db

Log = logging.getLogger(__name__)

# These are set by webadmin.py at startup
SERVER_DATA = None
EVENT_BUFFER = None
SSE_CLIENTS = []  # list of asyncio.Queue objects for SSE subscribers


def set_server_data(sd):
    global SERVER_DATA
    SERVER_DATA = sd


def set_event_buffer(buf):
    global EVENT_BUFFER
    EVENT_BUFFER = buf


_get_process_status_callback = None

def set_process_status_callback(callback):
    global _get_process_status_callback
    _get_process_status_callback = callback


# --- Helper ---

def _get_player_list():
    """Build a list of connected players from the Kaiburr API."""
    if not SERVER_DATA or not SERVER_DATA.API or not SERVER_DATA.API.GetAllClients:
        return []

    players = []
    try:
        for cl in SERVER_DATA.API.GetAllClients():
            import lib.shared.colors as colors
            import lib.shared.teams as teams
            players.append({
                "id": cl.GetId(),
                "name": colors.StripColorCodes(cl.GetName()),
                "name_raw": cl.GetName(),
                "ip": cl.GetIp(),
                "team": teams.TranslateTeam(cl.GetTeamId()),
                "team_id": cl.GetTeamId(),
            })
    except Exception as e:
        Log.error(f"Error getting player list: {e}")

    return players


# ============================================================
# Auth Routes
# ============================================================

async def handle_login(request):
    """POST /api/auth/login — username/password login."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return web.json_response({"error": "Username and password required"}, status=400)

    ip = get_client_ip(request)
    token, result = auth.login(username, password, ip)

    if token is None:
        # result is an error string
        return web.json_response({"error": result}, status=401)

    # result is the user dict
    response = web.json_response({"ok": True, "user": result})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="Lax",
        max_age=86400,  # 24 hours
        path="/"
    )
    return response


async def handle_logout(request):
    """POST /api/auth/logout — destroy session."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth.logout(token)

    response = web.json_response({"ok": True})
    response.del_cookie(SESSION_COOKIE, path="/")
    return response


@require_smod_level(1)
async def handle_me(request):
    """GET /api/auth/me — get current user info."""
    user = request["user"]
    return web.json_response({"user": user})


# ============================================================
# Server Info Routes
# ============================================================

_AUTH_CACHE_VALUE = None
_AUTH_CACHE_TIME = 0.0
AUTH_CACHE_TTL = 30.0  # seconds

@require_smod_level(1)
async def handle_server_info(request):
    """GET /api/server/info — server name, map, mode, player count."""
    if not SERVER_DATA:
        return web.json_response({"error": "Server data not available"}, status=503)

    player_count = 0
    mode_value = SERVER_DATA.mode
    try:
        if SERVER_DATA.API:
            if SERVER_DATA.API.GetClientCount:
                player_count = SERVER_DATA.API.GetClientCount()
        
        global _AUTH_CACHE_VALUE, _AUTH_CACHE_TIME
        if hasattr(SERVER_DATA, "interface") and hasattr(SERVER_DATA.interface, "GetCvar"):
            now = time.time()
            if now - _AUTH_CACHE_TIME > AUTH_CACHE_TTL:
                auth_val = SERVER_DATA.interface.GetCvar("g_authenticity")
                if auth_val is not None:
                    try:
                        _AUTH_CACHE_VALUE = int(auth_val)
                        _AUTH_CACHE_TIME = now
                    except ValueError:
                        pass
            
            if _AUTH_CACHE_VALUE is not None:
                mode_value = _AUTH_CACHE_VALUE
    except Exception as e:
        Log.error(f"Error getting server stats: {e}")

    # Get process health status using the injected callback
    proc_status, proc_last_change = "unknown", None
    if _get_process_status_callback:
        try:
            proc_status, proc_last_change = _get_process_status_callback()
        except Exception:
            pass

    info = {
        "name": SERVER_DATA.name,
        "map": SERVER_DATA.mapName,
        "mode": mode_value,
        "max_players": SERVER_DATA.maxPlayers,
        "player_count": player_count,
        "version": SERVER_DATA.version,
        "game_type": SERVER_DATA.gameType,
        "is_extended": getattr(SERVER_DATA, "is_extended", False),
        "process_status": proc_status,
        "process_status_since": proc_last_change,
    }
    return web.json_response(info)


@require_smod_level(1)
async def handle_player_list(request):
    """GET /api/server/players — list of connected players."""
    players = _get_player_list()
    return web.json_response({"players": players})


# ============================================================
# Server-Sent Events (Live Feed)
# ============================================================

@require_smod_level(1)
async def handle_events_sse(request):
    """GET /api/events — SSE stream of live server events."""
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)

    # Create a queue for this client
    client_queue = asyncio.Queue()
    SSE_CLIENTS.append(client_queue)

    try:
        # Send existing buffer as initial catch-up
        if EVENT_BUFFER:
            for event in list(EVENT_BUFFER):
                event_data = json.dumps(event)
                await response.write(f"data: {event_data}\n\n".encode("utf-8"))

        # Stream new events
        while True:
            try:
                event = await asyncio.wait_for(client_queue.get(), timeout=30)
                event_data = json.dumps(event)
                await response.write(f"data: {event_data}\n\n".encode("utf-8"))
            except asyncio.TimeoutError:
                # Send keepalive comment to prevent timeout
                await response.write(b": keepalive\n\n")
            except (ConnectionResetError, ConnectionAbortedError):
                break
    except asyncio.CancelledError:
        pass
    finally:
        if client_queue in SSE_CLIENTS:
            SSE_CLIENTS.remove(client_queue)

    return response


def broadcast_event(event_dict):
    """Push an event to all connected SSE clients. Called from the game thread."""
    for q in list(SSE_CLIENTS):
        try:
            q.put_nowait(event_dict)
        except asyncio.QueueFull:
            pass  # Client is backed up, skip



def resolve_target_details(player_id):
    details = {}
    try:
        player_id_int = int(player_id)
        # Attempt to get player name
        client = SERVER_DATA.API.GetClientById(player_id_int)
        if client:
            details["player_name"] = client.GetName()
            # Attempt to get account ID
            account_plugin = SERVER_DATA.API.GetPlugin("plugins.shared.accountsystem.accountsystem")
            if account_plugin:
                get_account = account_plugin._exports.Get("GetAccountByPlayerID").pointer
                account = get_account(player_id_int)
                if account and not account.is_dummy_account():
                    details["account_id"] = account.user_id
        else:
            # Fallback to scanning player list if GetClientById fails or returns None
            for p in _get_player_list():
                if p["id"] == player_id_int:
                    details["player_name"] = p["name"]
                    break
    except Exception as e:
        Log.error(f"Error resolving target details for {player_id}: {e}")
    return details

# ============================================================
# Player Action Routes
# ============================================================

@require_smod_level(2)
async def handle_kick(request):
    """POST /api/player/{id}/kick — kick a player."""
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        data = {}
    reason = data.get("reason", "")

    # Find the player name for logging
    player_name = f"ID:{player_id}"
    for p in _get_player_list():
        if p["id"] == player_id:
            player_name = p["name"]
            break

    SERVER_DATA.interface.ClientKick(player_id)
    target_details = resolve_target_details(player_id)
    target_details.update({"player_id": player_id, "reason": reason})
    db.log_action(user["id"], "kick", target_details.get("player_name", str(player_id)), target_details, ip)
    Log.info(f"WebAdmin: {user['username']} kicked {player_name} (ID:{player_id})")

    return web.json_response({"ok": True, "action": "kick", "target": player_name})


@require_smod_level(2)
async def handle_mute(request):
    """POST /api/player/{id}/mute — mute a player."""
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        data = {}
    minutes = data.get("minutes", 10)

    player_name = f"ID:{player_id}"
    for p in _get_player_list():
        if p["id"] == player_id:
            player_name = p["name"]
            break

    SERVER_DATA.interface.ClientMute(player_id, minutes)
    target_details = resolve_target_details(player_id)
    target_details.update({"player_id": player_id, "minutes": minutes})
    db.log_action(user["id"], "mute", target_details.get("player_name", str(player_id)), target_details, ip)
    Log.info(f"WebAdmin: {user['username']} muted {player_name} for {minutes}m")

    return web.json_response({"ok": True, "action": "mute", "target": player_name, "minutes": minutes})


@require_smod_level(2)
async def handle_unmute(request):
    """POST /api/player/{id}/unmute — unmute a player."""
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)

    player_name = f"ID:{player_id}"
    for p in _get_player_list():
        if p["id"] == player_id:
            player_name = p["name"]
            break

    SERVER_DATA.interface.ClientUnmute(player_id)
    target_details = resolve_target_details(player_id)
    target_details.update({"player_id": player_id})
    db.log_action(user["id"], "unmute", target_details.get("player_name", str(player_id)), target_details, ip)
    Log.info(f"WebAdmin: {user['username']} unmuted {player_name}")

    return web.json_response({"ok": True, "action": "unmute", "target": player_name})


@require_smod_level(3)
async def handle_ban(request):
    """POST /api/player/{ip}/ban — ban a player by IP."""
    target_ip = request.match_info["ip"]
    user = request["user"]
    req_ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        data = {}
    reason = data.get("reason", "")

    SERVER_DATA.interface.ClientBan(target_ip)
    db.log_action(user["id"], "ban", target_ip, {"reason": reason}, req_ip)
    Log.info(f"WebAdmin: {user['username']} banned IP {target_ip}")

    return web.json_response({"ok": True, "action": "ban", "target": target_ip})


@require_smod_level(3)
async def handle_unban(request):
    """POST /api/player/{ip}/unban — unban a player by IP."""
    target_ip = request.match_info["ip"]
    user = request["user"]
    req_ip = get_client_ip(request)

    SERVER_DATA.interface.ClientUnban(target_ip)
    db.log_action(user["id"], "unban", target_ip, {}, req_ip)
    Log.info(f"WebAdmin: {user['username']} unbanned IP {target_ip}")

    return web.json_response({"ok": True, "action": "unban", "target": target_ip})


# ============================================================
# Server Command Routes
# ============================================================

@require_smod_level(1)
async def handle_say(request):
    """POST /api/server/say — send a server message."""
    user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    message = data.get("message", "").strip()
    if not message:
        return web.json_response({"error": "Message required"}, status=400)

    SERVER_DATA.interface.SvSay(message)
    db.log_action(user["id"], "say", None, {"message": message}, ip)

    return web.json_response({"ok": True, "action": "say"})


@require_smod_level(2)
async def handle_smsay(request):
    """POST /api/server/smsay — send admin chat."""
    user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    message = data.get("message", "").strip()
    if not message:
        return web.json_response({"error": "Message required"}, status=400)

    SERVER_DATA.interface.SmSay(message)
    db.log_action(user["id"], "smsay", None, {"message": message}, ip)

    return web.json_response({"ok": True, "action": "smsay"})


@require_smod_level(3)
async def handle_map_change(request):
    """POST /api/server/map — change or reload map."""
    user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    map_name = data.get("map", "").strip()
    if not map_name:
        return web.json_response({"error": "Map name required"}, status=400)

    SERVER_DATA.admin_action = True
    try:
        SERVER_DATA.interface.MapReload(map_name)
    finally:
        SERVER_DATA.admin_action = False

    db.log_action(user["id"], "map_change", map_name, {}, ip)
    Log.info(f"WebAdmin: {user['username']} changed map to {map_name}")

    return web.json_response({"ok": True, "action": "map_change", "map": map_name})


@require_smod_level(3)
async def handle_get_cvar(request):
    """GET /api/server/cvar?name=... — get a cvar value."""
    cvar_name = request.query.get("name", "").strip()
    if not cvar_name:
        return web.json_response({"error": "Cvar name required"}, status=400)

    value = SERVER_DATA.interface.GetCvar(cvar_name)
    return web.json_response({"name": cvar_name, "value": value})


@require_smod_level(3)
async def handle_set_cvar(request):
    """POST /api/server/cvar — set a cvar value."""
    user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    cvar_name = data.get("name", "").strip()
    cvar_value = data.get("value", "")
    if not cvar_name:
        return web.json_response({"error": "Cvar name required"}, status=400)

    SERVER_DATA.interface.SetCvar(cvar_name, str(cvar_value))
    db.log_action(user["id"], "set_cvar", cvar_name, {"value": cvar_value}, ip)
    Log.info(f"WebAdmin: {user['username']} set cvar {cvar_name} = {cvar_value}")

    return web.json_response({"ok": True, "name": cvar_name, "value": cvar_value})



@require_smod_level(2)
async def handle_tempban(request):
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    rounds = int(data.get("rounds", 1))
    
    player_name = f"ID:{player_id}"
    for p in _get_player_list():
        if p["id"] == player_id:
            player_name = p["name"]
            break

    SERVER_DATA.interface.Tempban(player_name, rounds)
    target_details = resolve_target_details(player_id)
    target_details.update({"player_id": player_id, "rounds": rounds})
    db.log_action(user["id"], "tempban", target_details.get("player_name", str(player_id)), target_details, ip)
    Log.info(f"WebAdmin: {user['username']} tempbanned {player_name} for {rounds} rounds")
    return web.json_response({"ok": True, "action": "tempban", "target": player_name, "rounds": rounds})

@require_smod_level(2)
async def handle_forceteam(request):
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    team = data.get("team", "s")
    SERVER_DATA.interface.ForceTeam(player_id, team)
    target_details = resolve_target_details(player_id)
    target_details.update({"team": team})
    db.log_action(user["id"], "forceteam", target_details.get("player_name", str(player_id)), target_details, ip)
    return web.json_response({"ok": True, "action": "forceteam", "target": player_id, "team": team})

@require_smod_level(2)
async def handle_settk(request):
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    points = int(data.get("points", 0))
    SERVER_DATA.interface.SetTK(player_id, points)
    target_details = resolve_target_details(player_id)
    target_details.update({"points": points})
    db.log_action(user["id"], "settk", target_details.get("player_name", str(player_id)), target_details, ip)
    return web.json_response({"ok": True, "action": "settk", "target": player_id, "points": points})

@require_smod_level(2)
async def handle_marktk(request):
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    minutes = int(data.get("minutes", 10))
    SERVER_DATA.interface.MarkTK(player_id, minutes)
    target_details = resolve_target_details(player_id)
    target_details.update({"minutes": minutes})
    db.log_action(user["id"], "marktk", target_details.get("player_name", str(player_id)), target_details, ip)
    return web.json_response({"ok": True, "action": "marktk", "target": player_id, "minutes": minutes})

@require_smod_level(2)
async def handle_unmarktk(request):
    player_id = int(request.match_info["id"])
    user = request["user"]
    ip = get_client_ip(request)
    SERVER_DATA.interface.UnmarkTK(player_id)
    target_details = resolve_target_details(player_id)
    db.log_action(user["id"], "unmarktk", target_details.get("player_name", str(player_id)), target_details, ip)
    return web.json_response({"ok": True, "action": "unmarktk", "target": player_id})

@require_smod_level(3)
async def handle_nextmap(request):
    user = request["user"]
    SERVER_DATA.interface.ExecVstr("nextmap")
    db.log_action(user["id"], "nextmap", None, {}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "nextmap"})

@require_smod_level(3)
async def handle_maprestart(request):
    user = request["user"]
    SERVER_DATA.interface._rcon.MapReload(SERVER_DATA.mapName)
    db.log_action(user["id"], "maprestart", None, {}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "maprestart"})

@require_smod_level(3)
async def handle_newround(request):
    user = request["user"]
    SERVER_DATA.interface.NewRound()
    db.log_action(user["id"], "newround", None, {}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "newround"})

@require_smod_level(3)
async def handle_gametype(request):
    user = request["user"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    map_name = data.get("map", SERVER_DATA.mapName)
    t1 = data.get("team1", "")
    t2 = data.get("team2", "")
    if t1:
        SERVER_DATA.interface.SetTeam1(t1)
    if t2:
        SERVER_DATA.interface.SetTeam2(t2)
        
    SERVER_DATA.admin_action = True
    try:
        SERVER_DATA.interface.MapReload(map_name)
    finally:
        SERVER_DATA.admin_action = False

    db.log_action(user["id"], "gametype", None, {"map": map_name, "team1": t1, "team2": t2}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "gametype"})

@require_smod_level(3)
async def handle_mbmode(request):
    user = request["user"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    mode = int(data.get("mode", 0))
    map_name = data.get("map", SERVER_DATA.mapName)
    SERVER_DATA.admin_action = True
    try:
        SERVER_DATA.interface.MbMode(mode, map_name)
    finally:
        SERVER_DATA.admin_action = False

    db.log_action(user["id"], "mbmode", None, {"mode": mode, "map": map_name}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "mbmode"})

@require_smod_level(3)
async def handle_vstr(request):
    user = request["user"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    vstr = data.get("vstr", "").strip()
    if vstr:
        SERVER_DATA.interface.ExecVstr(vstr)
    db.log_action(user["id"], "vstr", None, {"vstr": vstr}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "vstr", "vstr": vstr})

@require_smod_level(3)
async def handle_shuffle(request):
    user = request["user"]
    SERVER_DATA.interface.Shuffle()
    db.log_action(user["id"], "shuffle", None, {}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "shuffle"})

@require_smod_level(2)
async def handle_tempbanlist(request):
    import lib.shared.colors as colors
    r = SERVER_DATA.interface.TempbanList()
    text = r.decode("utf-8", errors="ignore") if r else ""
    bans = []
    for line in text.split("\n"):
        line = colors.StripColorCodes(line).strip()
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            bans.append({
                "id": parts[0],
                "rounds": parts[1],
                "ip": parts[2]
            })
    return web.json_response({"ok": True, "action": "tempbanlist", "bans": bans})

@require_smod_level(2)
async def handle_removetempban(request):
    user = request["user"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    target = data.get("target", "").strip()
    SERVER_DATA.interface.RemoveTempban(target)
    db.log_action(user["id"], "removetempban", target, {}, get_client_ip(request))
    return web.json_response({"ok": True, "action": "removetempban", "target": target})


# ============================================================
# Admin / Audit Routes
# ============================================================

@require_smod_level(1)
async def handle_audit_log(request):
    """GET /api/admin/log — query audit log."""
    limit = min(int(request.query.get("limit", 100)), 500)
    offset = int(request.query.get("offset", 0))
    user_id_filter = request.query.get("user_id")
    action_filter = request.query.get("action")

    entries = db.get_audit_log(
        limit=limit,
        offset=offset,
        user_id=int(user_id_filter) if user_id_filter else None,
        action=action_filter if action_filter else None
    )
    return web.json_response({"entries": entries})


@require_smod_level(3)
async def handle_list_users(request):
    """GET /api/admin/users — list admin users."""
    users = db.list_users()
    return web.json_response({"users": users})


@require_smod_level(3)
async def handle_create_user(request):
    """POST /api/admin/users — create or update an admin user."""
    admin_user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    username = data.get("username", "").strip()
    password = data.get("password", "")
    smod_level = data.get("smod_level", 1)

    if not username:
        return web.json_response({"error": "Username required"}, status=400)
    if smod_level not in (1, 2, 3):
        return web.json_response({"error": "smod_level must be 1, 2, or 3"}, status=400)

    # Check if updating existing user
    existing = db.get_user_by_username(username)
    if existing:
        updates = {"smod_level": smod_level}
        if password:
            updates["password_hash"] = auth.hash_password(password)
        db.update_user(existing["id"], **updates)
        db.log_action(admin_user["id"], "update_user", username, {"smod_level": smod_level}, ip)
        return web.json_response({"ok": True, "action": "updated", "user_id": existing["id"]})
    else:
        if not password:
            return web.json_response({"error": "Password required for new user"}, status=400)
        pw_hash = auth.hash_password(password)
        user_id = db.create_user(username, pw_hash, smod_level)
        if user_id:
            db.log_action(admin_user["id"], "create_user", username, {"smod_level": smod_level}, ip)
            return web.json_response({"ok": True, "action": "created", "user_id": user_id})
        else:
            return web.json_response({"error": "Failed to create user"}, status=500)


@require_smod_level(3)
async def handle_change_password(request):
    """POST /api/auth/change-password — change own or another user's password."""
    admin_user = request["user"]
    ip = get_client_ip(request)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    target_user_id = data.get("user_id", admin_user["id"])
    new_password = data.get("password", "")

    if not new_password or len(new_password) < 6:
        return web.json_response({"error": "Password must be at least 6 characters"}, status=400)

    target_user = db.get_user_by_id(target_user_id)
    if not target_user:
        return web.json_response({"error": "User not found"}, status=404)

    pw_hash = auth.hash_password(new_password)
    db.update_user(target_user_id, password_hash=pw_hash)

    # Invalidate all sessions for the target user
    db.destroy_user_sessions(target_user_id)

    db.log_action(admin_user["id"], "change_password", target_user["username"], {}, ip)
    Log.info(f"WebAdmin: {admin_user['username']} changed password for {target_user['username']}")

    return web.json_response({"ok": True})


# ============================================================
# Route Registration
# ============================================================

def setup_routes(app):
    """Register all API routes on the aiohttp app."""
    # Auth
    app.router.add_post("/api/auth/login", handle_login)
    app.router.add_post("/api/auth/logout", handle_logout)
    app.router.add_get("/api/auth/me", handle_me)
    app.router.add_post("/api/auth/change-password", handle_change_password)

    # Server info
    app.router.add_get("/api/server/info", handle_server_info)
    app.router.add_get("/api/server/players", handle_player_list)
    app.router.add_get("/api/events", handle_events_sse)

    # Player actions
    app.router.add_post("/api/player/{id}/kick", handle_kick)
    app.router.add_post("/api/player/{id}/mute", handle_mute)
    app.router.add_post("/api/player/{id}/unmute", handle_unmute)
    app.router.add_post("/api/player/{ip}/ban", handle_ban)
    app.router.add_post("/api/player/{ip}/unban", handle_unban)

    # Server commands
    app.router.add_post("/api/server/say", handle_say)
    app.router.add_post("/api/server/smsay", handle_smsay)
    app.router.add_post("/api/server/map", handle_map_change)
    app.router.add_get("/api/server/cvar", handle_get_cvar)
    app.router.add_post("/api/server/cvar", handle_set_cvar)

    app.router.add_post("/api/player/{id}/tempban", handle_tempban)
    app.router.add_post("/api/player/{id}/forceteam", handle_forceteam)
    app.router.add_post("/api/player/{id}/settk", handle_settk)
    app.router.add_post("/api/player/{id}/marktk", handle_marktk)
    app.router.add_post("/api/player/{id}/unmarktk", handle_unmarktk)

    app.router.add_post("/api/server/commands/nextmap", handle_nextmap)
    app.router.add_post("/api/server/commands/maprestart", handle_maprestart)
    app.router.add_post("/api/server/commands/newround", handle_newround)
    app.router.add_post("/api/server/commands/gametype", handle_gametype)
    app.router.add_post("/api/server/commands/mbmode", handle_mbmode)
    app.router.add_post("/api/server/commands/vstr", handle_vstr)
    app.router.add_post("/api/server/commands/shuffle", handle_shuffle)
    app.router.add_get("/api/server/tempbanlist", handle_tempbanlist)
    app.router.add_post("/api/server/commands/removetempban", handle_removetempban)


    # Admin
    app.router.add_get("/api/admin/log", handle_audit_log)
    app.router.add_get("/api/admin/users", handle_list_users)
    app.router.add_post("/api/admin/users", handle_create_user)

    # Account Viewer
    app.router.add_get("/api/accounts", handle_search_accounts)
    app.router.add_get("/api/accounts/{id}", handle_get_account)

    # Static files — serve index.html for root, and everything under /static/
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.router.add_static("/static", static_dir)

    # Root serves the SPA
    async def handle_root(request):
        return web.FileResponse(os.path.join(static_dir, "index.html"))

    app.router.add_get("/", handle_root)
    app.router.add_get("", handle_root)


import os


# ============================================================
# Account Viewer Routes
# ============================================================

async def handle_search_accounts(request):
    try:
        q = request.query.get("q", "").strip()
        
        account_plugin = SERVER_DATA.API.GetPlugin("plugins.shared.accountsystem.accountsystem")
        if not account_plugin:
            return web.json_response({"error": "Account system offline"}, status=503)
            
        export_inst = account_plugin._exports.Get("GetDatabaseConnection")
        if not export_inst:
            return web.json_response({"error": "GetDatabaseConnection export not found"}, status=500)
            
        get_db_conn_func = export_inst.pointer
        db_conn = get_db_conn_func()
        if not db_conn:
            return web.json_response({"error": "db_conn is None"}, status=500)
            
        if not q:
            query = "SELECT user_id, player_name, last_login FROM user_credentials ORDER BY last_login DESC LIMIT 50"
        else:
            q_clean = q.replace("'", "''")
            query = f"SELECT user_id, player_name, last_login FROM user_credentials WHERE player_name LIKE '%{q_clean}%' OR user_id = '{q_clean}' ORDER BY last_login DESC LIMIT 50"
            
        rows = db_conn.ExecuteQuery(query, withResponse=True) or []
        
        results = []
        for r in rows:
            results.append({
                "user_id": r[0],
                "player_name": r[1],
                "last_login": r[2]
            })
            
        return web.json_response({"accounts": results})
    except Exception as e:
        import traceback
        return web.json_response({"error": str(e), "trace": traceback.format_exc()}, status=500)

async def handle_get_account(request):
    """GET /api/accounts/{id}"""
    try:
        try:
            user_id = int(request.match_info["id"])
        except ValueError:
            return web.json_response({"error": "Invalid user ID"}, status=400)
            
        account_plugin = SERVER_DATA.API.GetPlugin("plugins.shared.accountsystem.accountsystem")
        if not account_plugin:
            return web.json_response({"error": "Account system offline"}, status=503)
            
        export_inst = account_plugin._exports.Get("GetDatabaseConnection")
        if not export_inst:
            return web.json_response({"error": "GetDatabaseConnection export not found"}, status=500)
            
        get_db_conn_func = export_inst.pointer
        db_conn = get_db_conn_func()
        if not db_conn:
            return web.json_response({"error": "db_conn is None"}, status=500)
        
        # user_credentials: 0:id, 1:name, 2:ip, 3:last_ip, 4:totp, 5:created_at, 6:last_login, 7:discord_id
        cred_rows = db_conn.ExecuteQuery(f"SELECT user_id, player_name, created_at, last_login, discord_id FROM user_credentials WHERE user_id = {user_id}", withResponse=True)
        if not cred_rows or len(cred_rows) == 0:
            return web.json_response({"error": "Account not found"}, status=404)
            
        c = cred_rows[0]
        account_data = {
            "user_id": c[0],
            "player_name": c[1],
            "created_at": c[2],
            "last_login": c[3],
            "discord_id": c[4]
        }
        
        # banking: 0:id, 1:credits
        bank_rows = db_conn.ExecuteQuery(f"SELECT credits FROM banking WHERE user_id = {user_id}", withResponse=True)
        account_data["credits"] = bank_rows[0][0] if bank_rows else 0
        
        # experience: 0:id, 1:exp, 2:level
        exp_rows = db_conn.ExecuteQuery(f"SELECT exp, level FROM experience WHERE user_id = {user_id}", withResponse=True)
        if exp_rows:
            account_data["exp"] = exp_rows[0][0]
            account_data["level"] = exp_rows[0][1]
        else:
            account_data["exp"] = 0
            account_data["level"] = 0
            
        # elo_ratings: 0:id, 1:rating, 2:games, 3:kills, 4:deaths, 5:high, 6:low, 7:changes, 8:last
        elo_rows = db_conn.ExecuteQuery(f"SELECT rating, games_played, kills, deaths, highest_rating FROM elo_ratings WHERE user_id = {user_id}", withResponse=True)
        if elo_rows:
            e = elo_rows[0]
            account_data["elo_rating"] = e[0]
            account_data["games_played"] = e[1]
            account_data["kills"] = e[2]
            account_data["deaths"] = e[3]
            account_data["highest_rating"] = e[4]
            if e[3] > 0:
                account_data["kd_ratio"] = round(e[2] / e[3], 2)
            else:
                account_data["kd_ratio"] = e[2]
        else:
            account_data["elo_rating"] = 1200
            account_data["games_played"] = 0
            account_data["kills"] = 0
            account_data["deaths"] = 0
            account_data["highest_rating"] = 1200
            account_data["kd_ratio"] = 0
            
        return web.json_response(account_data)
    except Exception as e:
        import traceback
        return web.json_response({"error": str(e), "trace": traceback.format_exc()}, status=500)
