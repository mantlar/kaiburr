"""
WebAdmin - A Kaiburr plugin that serves a web-based moderation panel.

Spins up an aiohttp HTTP server in a daemon thread, provides REST API
endpoints for moderation actions, and streams live server events via SSE.
"""

import os
import json
import logging
import threading
import asyncio
import secrets
from collections import deque
from datetime import datetime

import lib.shared.serverdata as serverdata
import lib.shared.kaiburrEvent as kaiburrEvent
import lib.shared.colors as colors
import lib.shared.teams as teams

from aiohttp import web

Log = logging.getLogger(__name__)

# --- Global State ---
SERVER_DATA = None
PluginInstance = None

# Server process health state (updated by watchdog events)
# Possible values: "unknown", "online", "offline", "restarting"
_server_process_status = "unknown"
_server_process_last_change = None

# Event ring buffer for SSE live feed (thread-safe via deque)
EVENT_BUFFER = deque(maxlen=500)

# Web server state
_web_thread = None
_web_loop = None
_web_runner = None
_shutdown_event = threading.Event()

# Config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "webadminConfig.yaml")
CONFIG_DEFAULTS = {
    "port": 8080,
    "host": "127.0.0.1",
    "sessionSecret": None,  # auto-generated on first run
    "sessionLifetimeHours": 24,
    "rateLimitPerMinute": 60,
    "smodLevelNames": {
        "1": "Helper",
        "2": "Moderator",
        "3": "Administrator"
    }
}

CONFIG = {}


def load_config():
    """Load or create the config file."""
    global CONFIG

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                CONFIG = json.load(f)
        except Exception as e:
            Log.error(f"Error loading webadmin config: {e}")
            CONFIG = dict(CONFIG_DEFAULTS)
    else:
        CONFIG = dict(CONFIG_DEFAULTS)

    # Merge any missing defaults
    for key, val in CONFIG_DEFAULTS.items():
        if key not in CONFIG:
            CONFIG[key] = val

    # Auto-generate session secret if not set
    if not CONFIG.get("sessionSecret"):
        CONFIG["sessionSecret"] = secrets.token_urlsafe(32)

    # Save back (ensures file exists and has all keys)
    save_config()


def save_config():
    """Write config to disk."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(CONFIG, f, indent=4)
    except Exception as e:
        Log.error(f"Error saving webadmin config: {e}")


# --- Event Buffering ---

def push_event(event_type, **kwargs):
    """Push an event into the ring buffer and broadcast to SSE clients."""
    event_dict = {
        "type": event_type,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **kwargs
    }
    EVENT_BUFFER.append(event_dict)

    # Broadcast to SSE clients (must be done via the web server's event loop)
    if _web_loop and _web_loop.is_running():
        try:
            from plugins.shared.webadmin.routes import broadcast_event
            _web_loop.call_soon_threadsafe(broadcast_event, event_dict)
        except Exception:
            pass


# --- Web Server Thread ---

def _create_app():
    """Create and configure the aiohttp application."""
    from plugins.shared.webadmin import auth as auth_module
    from plugins.shared.webadmin import routes
    from plugins.shared.webadmin.middleware import (
        create_auth_middleware,
        create_rate_limit_middleware,
        RateLimiter,
    )

    rate_limiter = RateLimiter(max_requests=CONFIG.get("rateLimitPerMinute", 60))

    app = web.Application(middlewares=[
        create_rate_limit_middleware(rate_limiter),
        create_auth_middleware(auth_module),
    ])

    # Give routes access to server data and event buffer
    routes.set_server_data(SERVER_DATA)
    routes.set_event_buffer(EVENT_BUFFER)
    routes.set_process_status_callback(get_process_status)

    # Register all routes
    routes.setup_routes(app)

    return app


def _run_web_server():
    """Target function for the web server daemon thread."""
    global _web_loop, _web_runner

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _web_loop = loop

    try:
        app = _create_app()
        _web_runner = web.AppRunner(app)
        loop.run_until_complete(_web_runner.setup())

        host = CONFIG.get("host", "127.0.0.1")
        port = CONFIG.get("port", 8080)
        site = web.TCPSite(_web_runner, host, port)
        loop.run_until_complete(site.start())

        Log.info(f"WebAdmin server started on http://{host}:{port}")
        print(f"WebAdmin: Server running at http://{host}:{port}")

        # Run until shutdown
        loop.run_forever()
    except Exception as e:
        Log.error(f"WebAdmin server error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if _web_runner:
            loop.run_until_complete(_web_runner.cleanup())
        loop.close()
        _web_loop = None


def _stop_web_server():
    """Stop the web server gracefully."""
    global _web_thread, _web_loop, _web_runner

    _shutdown_event.set()

    if _web_loop and _web_loop.is_running():
        _web_loop.call_soon_threadsafe(_web_loop.stop)

    if _web_thread and _web_thread.is_alive():
        _web_thread.join(timeout=5)

    _web_thread = None
    _web_runner = None


# --- Server Health Polling ---

def _health_monitor_loop():
    """Background thread to poll the server process status."""
    import psutil
    import time
    
    global _server_process_status
    
    while not _shutdown_event.is_set():
        try:
            target_name = (SERVER_DATA.args.mbiicmd if SERVER_DATA.args.mbiicmd else "mbiided").lower()
            is_running = False
            
            for proc in psutil.process_iter(['name']):
                try:
                    if proc.info['name']:
                        proc_name = proc.info['name'].lower()
                        # specifically check for the dedicated server name, not just any 'mbii' string
                        if target_name in proc_name or "mbiided" in proc_name:
                            is_running = True
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            new_status = "online" if is_running else "offline"
            
            if _server_process_status != new_status:
                _update_process_status(new_status)
                
                # Push event to clients
                if new_status == "offline":
                    push_event("server_health", status="offline", message="Server process has crashed!")
                else:
                    push_event("server_health", status="online", message="Server process has started")
                    
        except Exception as e:
            Log.error(f"WebAdmin: Health monitor error: {e}")
            
        _shutdown_event.wait(3.0) # Check every 3 seconds


# ============================================================
# Kaiburr Plugin Hooks
# ============================================================

def OnInitialize(serverData: serverdata.ServerData, exports=None) -> bool:
    global SERVER_DATA, _server_process_status, _health_thread
    SERVER_DATA = serverData

    # Load config
    load_config()

    # Initialize database and create default admin if needed
    from plugins.shared.webadmin import db
    from plugins.shared.webadmin import auth
    db.init_db()
    auth.ensure_default_admin()
    
    # Start health monitor
    _server_process_status = "unknown"
    _health_thread = threading.Thread(target=_health_monitor_loop, daemon=True)
    _health_thread.start()

    Log.info("WebAdmin plugin initialized")
    return True

def OnStart():
    global _web_thread, _shutdown_event
    _shutdown_event.clear()
    _web_thread = threading.Thread(target=_run_web_server, daemon=True, name="webadmin-http")
    _web_thread.start()
    return True


def OnLoop():
    pass


def OnFinish():
    _stop_web_server()
    Log.info("WebAdmin plugin finished")


def OnEvent(event) -> bool:
    """Buffer server events for the SSE live feed."""

    if event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MESSAGE:
        cl = event.client
        push_event(
            "chat",
            player=colors.StripColorCodes(cl.GetName()),
            player_id=cl.GetId(),
            message=colors.StripColorCodes(event.message)
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_KILL:
        text = colors.StripColorCodes(event.data.get("text", ""))
        is_tk = event.data.get("tk", False)
        push_event("kill", text=text, is_tk=is_tk)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTCONNECT:
        cl = event.client
        push_event(
            "connect",
            player=colors.StripColorCodes(cl.GetName()),
            player_id=cl.GetId(),
            ip=cl.GetIp()
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTDISCONNECT:
        cl = event.client
        push_event(
            "disconnect",
            player=colors.StripColorCodes(cl.GetName()),
            player_id=cl.GetId()
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_ONNAMECHANGE:
        push_event(
            "namechange",
            old_name=colors.StripColorCodes(event.oldName),
            new_name=colors.StripColorCodes(event.newName),
            player_id=event.client.GetId() if event.client else None
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMOD_COMMAND:
        data = event.data
        push_event(
            "admin",
            admin=data.get("smod_name", "Unknown"),
            command=data.get("command", ""),
            target=data.get("target_name", ""),
            args=data.get("args", "")
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMOD_LOGIN:
        push_event(
            "admin",
            admin=event.playerName,
            command="login",
            smod_id=str(event.smodID)
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MAPCHANGE:
        push_event(
            "mapchange",
            map=event.mapName,
            old_map=event.oldMapName
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SERVER_SAY:
        push_event("server_say", message=event.message)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMSAY:
        push_event(
            "smsay",
            admin=event.playerName,
            smod_id=str(event.smodID),
            message=colors.StripColorCodes(event.message)
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMOD_SAY:
        push_event(
            "smod_say",
            admin=event.playerName,
            smod_id=str(event.smodID),
            message=colors.StripColorCodes(event.message)
        )

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_TELL:
        push_event(
            "tell",
            sender=colors.StripColorCodes(event.sender),
            target=colors.StripColorCodes(event.target),
            message=colors.StripColorCodes(event.message)
        )

    # Don't consume events — let other plugins process them too
    return False


def _update_process_status(new_status):
    """Update the global process health status."""
    global _server_process_status, _server_process_last_change
    _server_process_status = new_status
    _server_process_last_change = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_process_status():
    """Return (status, last_change_time) for use by routes."""
    return _server_process_status, _server_process_last_change


if __name__ == "__main__":
    print("WebAdmin is a Kaiburr plugin. Run it through Kaiburr.")
    exit()
