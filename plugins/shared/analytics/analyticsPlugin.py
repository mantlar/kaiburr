import logging
import json
import kaiburrEvent
import pluginExports
import lib.shared.serverdata as serverdata
import lib.shared.teams as teams
import traceback

Log = logging.getLogger(__name__)

SERVER_DATA = None
DB = None

TEAM_NAMES = {
    teams.TEAM_GLOBAL: "Global",
    teams.TEAM_GOOD: "Rebels",
    teams.TEAM_EVIL: "Imperials",
    teams.TEAM_SPEC: "Spectator"
}

def OnInitialize(serverData: serverdata.ServerData, exports=None) -> bool:
    global SERVER_DATA
    SERVER_DATA = serverData
    return True

def OnStart() -> bool:
    global DB
    DB = SERVER_DATA.API.GetDatabase("Kaiburr")
    if DB is None:
        Log.error("Analytics: Could not get 'Kaiburr' database.")
        return False
    
    # Initialize the wide table
    query = """
    CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        -- Subject Player info
        player_id INTEGER,
        player_name TEXT,
        player_guid TEXT,
        ip_address TEXT,
        -- Common event specific fields
        message TEXT,
        team_id INTEGER,
        team_name TEXT,
        reason TEXT,
        victim_id INTEGER,
        victim_name TEXT,
        victim_guid TEXT,
        weapon TEXT,
        map_name TEXT,
        old_map_name TEXT,
        admin_name TEXT,
        smod_id TEXT,
        admin_ip TEXT,
        -- Catch-all for remaining data
        extra_data JSON
    )
    """
    try:
        DB.ExecuteQuery(query)
        Log.info("Analytics: Plugin started with wide-table schema.")
    except Exception as e:
        Log.error(f"Analytics: Failed to initialize table: {e}\n{traceback.format_exc()}")
        return False
    return True

def OnLoop():
    pass

def OnFinish():
    pass

def escape(val):
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    # Assume string or dict/list (which we'll json encode)
    if isinstance(val, (dict, list)):
        val = json.dumps(val)
    return "'" + str(val).replace("'", "''") + "'"

def LogEvent(event_type, player=None, **kwargs):
    if DB is None:
        return

    # Base fields
    fields = {
        "event_type": event_type,
        "player_id": getattr(player, "_id", None) if player else None,
        "player_name": getattr(player, "_name", None) if player else None,
        "player_guid": getattr(player, "_jaguid", None) if player else None,
        "ip_address": getattr(player, "_address", None) if player else None,
    }

    # Map kwargs to column names if they exist, otherwise put in extra_data
    known_columns = [
        "message", "team_id", "team_name", "reason", 
        "victim_id", "victim_name", "victim_guid", 
        "weapon", "map_name", "old_map_name", 
        "admin_name", "smod_id", "admin_ip"
    ]
    
    extra_data = {}
    for key, value in kwargs.items():
        if key in known_columns:
            fields[key] = value
        else:
            extra_data[key] = value

    if extra_data:
        fields["extra_data"] = extra_data

    # Build dynamically
    col_names = ", ".join(fields.keys())
    col_values = ", ".join(escape(v) for v in fields.values())

    query = f"INSERT INTO analytics_events ({col_names}) VALUES ({col_values})"
    try:
        DB.ExecuteQuery(query)
    except Exception as e:
        Log.error(f"Analytics: Failed to log event {event_type}: {e}")

def OnEvent(event) -> bool:
    # Removed event.isStartup check to allow recording historical log data

    if event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MESSAGE:
        LogEvent("CHAT", event.client, 
                 message=event.message, 
                 team_id=event.teamId, 
                 team_name=TEAM_NAMES.get(event.teamId, str(event.teamId)))
    
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTCONNECT:
        LogEvent("CONNECT", event.client)
    
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTDISCONNECT:
        LogEvent("DISCONNECT", event.client, reason=event.reason)
    
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTCHANGED:
        LogEvent("CLIENT_CHANGED", event.client, 
                 changes=event.data,
                 current_name=getattr(event.client, "_name", None),
                 current_team=getattr(event.client, "_teamId", None),
                 current_guid=getattr(event.client, "_jaguid", None))

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_KILL:
        LogEvent("KILL", event.client, 
                 victim_id=event.victim._id if event.victim else None,
                 victim_name=event.victim._name if event.victim else None,
                 victim_guid=event.victim._jaguid if event.victim else None,
                 weapon=event.weaponStr)
    
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MAPCHANGE:
        LogEvent("MAP_CHANGE", None, map_name=event.mapName, old_map_name=event.oldMapName)
    
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_PLAYER_SPAWN:
        LogEvent("SPAWN", event.client, spawn_vars=event.data)
    
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_OBJECTIVE:
        LogEvent("OBJECTIVE", event.client, objective_data=event.data)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMSAY:
        LogEvent("SMOD_CHAT", None, 
                 admin_name=event.playerName, 
                 smod_id=event.smodID, 
                 admin_ip=event.adminIP, 
                 message=event.message)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMOD_COMMAND:
        LogEvent("SMOD_COMMAND", None, smod_command_data=event.data)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMOD_LOGIN:
        LogEvent("SMOD_LOGIN", None, 
                 admin_name=event.playerName, 
                 smod_id=event.smodID, 
                 admin_ip=event.adminIP)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_EXIT:
        LogEvent("ROUND_EXIT", None, exit_data=event.data)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_INIT:
        LogEvent("SERVER_INIT", None, init_vars=event.data)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SHUTDOWN:
        LogEvent("SERVER_SHUTDOWN", None)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_PLAYER:
         LogEvent("PLAYER_GENERIC", event.client, text=event.data.get("text") if event.data else None)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_POST_INIT:
        LogEvent("POST_INIT", None)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_REAL_INIT:
        LogEvent("REAL_INIT", None)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENT_BEGIN:
        LogEvent("CLIENT_BEGIN", event.client)

    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SERVER_EMPTY:
        LogEvent("SERVER_EMPTY", None)

    # Watchdog events
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_WD_UNAVAILABLE:
        LogEvent("WD_UNAVAILABLE", None)
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_WD_EXISTING:
        LogEvent("WD_EXISTING", None)
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_WD_DIED:
        LogEvent("WD_DIED", None)
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_WD_STARTED:
        LogEvent("WD_STARTED", None)
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_WD_RESTARTED:
        LogEvent("WD_RESTARTED", None)

    return False

if __name__ == "__main__":
    print("Analytics plugin for Kaiburr (Full Event Coverage).")
