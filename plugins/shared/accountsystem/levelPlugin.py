import logging
import os
import math
import json
import lib.shared.kaiburrEvent as kaiburrEvent
import lib.shared.colors as colors
import lib.shared.config as config
import lib.shared.teams as teams
import traceback
from lib.shared.serverdata import ServerData
from lib.shared.player import Player

Log = logging.getLogger(__name__)

SERVER_DATA = None
ACCOUNTS_XPRTS = None
DB = None

# Configuration file paths and defaults
DEFAULT_CFG_PATH = os.path.join(os.path.dirname(__file__), "levelConfig.yaml")

# Fallback configuration
CONFIG_FALLBACK = """
scalar: 100
kill_xp: 10
suicide_xp: -5
teamkill_xp: -20
themecolor: yellow
use_centerprint: true
rank_names:
  - Youngling
  - Apprentice
  - Initiate
  - Padawan
  - Knight
  - Master
  - Grandmaster
max_rank_name: The Force
"""

CFG = None
COMMAND_HANDLERS = {}
SMOD_COMMAND_HANDLERS = {}

def OnInitialize(serverData: ServerData, exports=None) -> bool:
    global SERVER_DATA, CFG
    SERVER_DATA = serverData
    
    # Load config via Config.from_file so YAML is generated/migrated automatically
    _cfg_obj = config.Config.from_file(DEFAULT_CFG_PATH, CONFIG_FALLBACK)
    CFG = _cfg_obj.cfg if _cfg_obj else {}
    return True

def OnStart() -> bool:
    global ACCOUNTS_XPRTS, DB
    ACCOUNTS_XPRTS = SERVER_DATA.API.GetPlugin("plugins.shared.accountsystem.accountsystem").GetExports()
    if not ACCOUNTS_XPRTS:
        Log.error("Leveling: Could not find AccountSystem exports. Is it loaded?")
        return False
        
    DB = ACCOUNTS_XPRTS.Get("GetDatabaseConnection").pointer()
    if not DB:
        Log.error("Leveling: Could not get database connection from AccountSystem.")
        return False
    
    # Initialize table
    query = """
    CREATE TABLE IF NOT EXISTS experience (
        user_id INTEGER PRIMARY KEY,
        exp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0
    )
    """
    DB.ExecuteQuery(query)
    
    # Register commands
    _register_commands()
    
    Log.info("Leveling plugin started.")
    return True

def GetLevel(exp):
    scalar = CFG.get("scalar", 100)
    if exp < scalar:
        return 0
    # Add a small epsilon to handle floating point precision issues (e.g. 7.99999999 -> 8)
    return int(math.pow(exp / scalar, 1/3) + 0.0000000001)

def GetExpForLevel(level):
    scalar = CFG.get("scalar", 100)
    return int(scalar * math.pow(level, 3))

def GetRankName(level):
    rank_names = CFG.get("rank_names", [])
    if level < len(rank_names):
        return rank_names[level]
    return CFG.get("max_rank_name", "The Force")

def _register_commands():
    global COMMAND_HANDLERS, SMOD_COMMAND_HANDLERS
    COMMAND_HANDLERS = {
        ("level", "xp", "rank"): ("!level - Check your current level and XP", HandleLevel),
        ("leveltop", "xptop"): ("!leveltop - View top 10 players by level", HandleLevelTop)
    }
    
    SMOD_COMMAND_HANDLERS = {
        ("setxp",): ("!setxp <pid> <amount> - Set a player's total XP", HandleSetXP),
        ("addxp",): ("!addxp <pid> <amount> - Add XP to a player", HandleAddXP),
        ("setlevel",): ("!setlevel <pid> <amount> - Set a player's level (recalculates XP)", HandleSetLevel)
    }
    
    # Register regular commands
    newVal = []
    rCommands = SERVER_DATA.GetServerVar("registeredCommands")
    if rCommands != None:
        newVal.extend(rCommands)
    
    for cmd_tuple, (desc, handler) in COMMAND_HANDLERS.items():
        for alias in cmd_tuple:
            newVal.append((alias, desc))
    SERVER_DATA.SetServerVar("registeredCommands", newVal)
    
    # Register SMOD commands
    new_smod_commands = []
    r_smod_commands = SERVER_DATA.GetServerVar("registeredSmodCommands")
    if r_smod_commands:
        new_smod_commands.extend(r_smod_commands)
    
    for cmd_tuple, (desc, handler) in SMOD_COMMAND_HANDLERS.items():
        for alias in cmd_tuple:
            new_smod_commands.append((alias, desc))
    SERVER_DATA.SetServerVar("registeredSmodCommands", new_smod_commands)

def UpdatePlayerXP(player: Player, amount: int, silent=False):
    # Get user_id
    account_func = ACCOUNTS_XPRTS.Get("GetAccountByPlayerID").pointer
    account = account_func(player.GetId())
    if not account or account.is_dummy_account():
        return
    
    user_id = account.user_id
    color = CFG.get("themecolor", "yellow")
    prefix = f"{colors.COLOR_CODES[color]}[Level]^7: "
    
    # Get current
    query = f"SELECT exp, level FROM experience WHERE user_id = {user_id}"
    res = DB.ExecuteQuery(query, withResponse=True)
    
    if not res:
        current_exp = 0
        current_level = 0
        insert_query = f"INSERT INTO experience (user_id, exp, level) VALUES ({user_id}, 0, 0)"
        DB.ExecuteQuery(insert_query)
    else:
        current_exp = res[0][0]
        current_level = res[0][1]
    
    new_exp = max(0, current_exp + amount)
    new_level = GetLevel(new_exp)
    
    # Update DB
    update_query = f"UPDATE experience SET exp = {new_exp}, level = {new_level} WHERE user_id = {user_id}"
    DB.ExecuteQuery(update_query)
    
    if silent:
        return
    
    # Notify player
    if new_level > current_level:
        # Level up!
        SERVER_DATA.interface.ClientSound("sound/jediwin.wav", player.GetId())
        if CFG.get("use_centerprint", True):
            # Use \\n as requested by user
            rank_name = GetRankName(new_level)
            msg = f"LEVEL UP!^7\\nYou are now level {colors.ColorizeText(new_level, color)}!\\nRank: {colors.ColorizeText(rank_name, color)}"
            if getattr(SERVER_DATA, "is_extended", False):
                SERVER_DATA.interface.ClientCenterPrint(player.GetId(), msg)
            else:
                SERVER_DATA.interface.SvTell(player.GetId(), msg.replace("\\n", " | "))
        
        SERVER_DATA.interface.SvSay(f"{prefix}{player.GetName()}^7 leveled up to {colors.ColorizeText(new_level, color)} ({GetRankName(new_level)})!")
    elif amount != 0:
        next_level = new_level + 1
        xp_next = GetExpForLevel(next_level)
        xp_this = GetExpForLevel(new_level)
        rank_name = GetRankName(new_level)
        level_progress = new_exp - xp_this
        level_total = xp_next - xp_this
        
        sign = "+" if amount > 0 else ""
        status_msg = f"XP: {colors.ColorizeText(sign + str(amount), color)}\\n[{rank_name}^7]: ({level_progress}/{level_total}) to Next Level"
        if getattr(SERVER_DATA, "is_extended", False):
            SERVER_DATA.interface.ClientCenterPrint(player.GetId(), status_msg)
        else:
            SERVER_DATA.interface.SvTell(player.GetId(), status_msg.replace("\\n", " | "))

def SetPlayerXP(player: Player, amount: int):
    account_func = ACCOUNTS_XPRTS.Get("GetAccountByPlayerID").pointer
    account = account_func(player.GetId())
    if not account or account.is_dummy_account():
        return
    
    user_id = account.user_id
    new_level = GetLevel(amount)
    
    query = f"INSERT OR REPLACE INTO experience (user_id, exp, level) VALUES ({user_id}, {amount}, {new_level})"
    DB.ExecuteQuery(query)
    
    color = CFG.get("themecolor", "yellow")
    rank_name = GetRankName(new_level)
    prefix = f"{colors.COLOR_CODES[color]}[Level]^7: "
    SERVER_DATA.interface.SvTell(player.GetId(), f"{prefix}Your XP has been set to {colors.ColorizeText(amount, color)} (Level {new_level} - {rank_name}).")

def HandleLevel(player: Player, teamId: int, args: list[str]):
    account_func = ACCOUNTS_XPRTS.Get("GetAccountByPlayerID").pointer
    account = account_func(player.GetId())
    if not account or account.is_dummy_account():
        SERVER_DATA.interface.SvTell(player.GetId(), "You are not logged in.")
        return
    
    user_id = account.user_id
    color = CFG.get("themecolor", "yellow")
    prefix = f"{colors.COLOR_CODES[color]}[Level]^7: "
    
    query = f"SELECT exp, level FROM experience WHERE user_id = {user_id}"
    res = DB.ExecuteQuery(query, withResponse=True)
    
    if not res:
        exp, level = 0, 0
    else:
        exp, level = res[0]
        
    next_level = level + 1
    xp_next = GetExpForLevel(next_level)
    xp_this = GetExpForLevel(level)
    
    progress = exp - xp_this
    needed = xp_next - xp_this
    rank_name = GetRankName(level)
    
    msg = f"Rank: {colors.ColorizeText(rank_name, color)} | Level: {colors.ColorizeText(level, color)} | XP: {colors.ColorizeText(exp, color)}/{xp_next} ({progress}/{needed} to next level)"
    SERVER_DATA.interface.SvTell(player.GetId(), f"{prefix}{msg}")

def HandleLevelTop(player: Player, teamId: int, args: list[str]):
    color = CFG.get("themecolor", "yellow")
    prefix = f"{colors.COLOR_CODES[color]}[Level]^7: "
    
    query = """
        SELECT uc.player_name, e.level, e.exp
        FROM experience e
        JOIN user_credentials uc ON e.user_id = uc.user_id
        ORDER BY e.exp DESC
        LIMIT 10
    """
    res = DB.ExecuteQuery(query, withResponse=True)
    
    if not res:
        SERVER_DATA.interface.SvTell(player.GetId(), f"{prefix}No ranking data available.")
        return
    
    entries = []
    for i, row in enumerate(res):
        name, level, exp = row
        rank_name = GetRankName(level)
        entries.append(f"{i+1}. {name}^7 - Lvl {colors.ColorizeText(level, color)} ({rank_name}^7) - {exp} XP")
    
    output = f"{prefix}Top 10 Players: " + " | ".join(entries)
    
    if getattr(SERVER_DATA, "is_extended", False):
        SERVER_DATA.interface.SvPrintCon(output, str(player.GetId()))
    else:
        SERVER_DATA.interface.SvTell(player.GetId(), output)

# SMOD Handlers
def HandleSetXP(playerName, smodId, adminIP, cmdArgs):
    if len(cmdArgs) < 3:
        SERVER_DATA.interface.SmSay(f"Usage: !setxp <pid> <amount>")
        return True
    
    try:
        pid = int(cmdArgs[1])
        amount = int(cmdArgs[2])
        client = SERVER_DATA.API.GetClientById(pid)
        if not client:
            SERVER_DATA.interface.SmSay(f"Player {pid} not found.")
            return True
        
        SetPlayerXP(client, amount)
        SERVER_DATA.interface.SmSay(f"Set XP of {client.GetName()}^7 to {amount}.")
    except Exception as e:
        SERVER_DATA.interface.SmSay(f"Error: {e}")
    return True

def HandleAddXP(playerName, smodId, adminIP, cmdArgs):
    if len(cmdArgs) < 3:
        SERVER_DATA.interface.SmSay(f"Usage: !addxp <pid> <amount>")
        return True
    
    try:
        pid = int(cmdArgs[1])
        amount = int(cmdArgs[2])
        client = SERVER_DATA.API.GetClientById(pid)
        if not client:
            SERVER_DATA.interface.SmSay(f"Player {pid} not found.")
            return True
        
        UpdatePlayerXP(client, amount)
        SERVER_DATA.interface.SmSay(f"Added {amount} XP to {client.GetName()}^7.")
    except Exception as e:
        SERVER_DATA.interface.SmSay(f"Error: {e}")
    return True

def HandleSetLevel(playerName, smodId, adminIP, cmdArgs):
    if len(cmdArgs) < 3:
        SERVER_DATA.interface.SmSay(f"Usage: !setlevel <pid> <level>")
        return True
    
    try:
        pid = int(cmdArgs[1])
        level = int(cmdArgs[2])
        client = SERVER_DATA.API.GetClientById(pid)
        if not client:
            SERVER_DATA.interface.SmSay(f"Player {pid} not found.")
            return True
        
        xp_needed = GetExpForLevel(level)
        SetPlayerXP(client, xp_needed)
        SERVER_DATA.interface.SmSay(f"Set Level of {client.GetName()}^7 to {level} (XP: {xp_needed}).")
    except Exception as e:
        SERVER_DATA.interface.SmSay(f"Error: {e}")
    return True

def _on_smsay(event):
    message = event.message
    cmdArgs = message.split()
    if not cmdArgs: return False
    
    command = cmdArgs[0]
    if command.startswith("!"):
        command = command[1:]
    
    for aliases, (desc, handler) in SMOD_COMMAND_HANDLERS.items():
        if command in aliases:
            return handler(event.playerName, event.smodID, event.adminIP, cmdArgs)
    return False

def OnEvent(event) -> bool:
    if event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_KILL:
        if event.client: # killer
            if event.client == event.victim:
                UpdatePlayerXP(event.client, CFG.get("suicide_xp", -5))
            elif event.client.GetTeamId() == event.victim.GetTeamId():
                UpdatePlayerXP(event.client, CFG.get("teamkill_xp", -20))
            else:
                UpdatePlayerXP(event.client, CFG.get("kill_xp", 10))
                
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MESSAGE:
        if event.message.startswith("!"):
            args = event.message[1:].split()
            if args:
                cmd = args[0].lower()
                for aliases, (desc, handler) in COMMAND_HANDLERS.items():
                    if cmd in aliases:
                        handler(event.client, event.teamId, args)
                        break
                        
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMSAY:
        return _on_smsay(event)
    
    return False

def OnLoop():
    pass

def OnFinish():
    pass

if __name__ == "__main__":
    print("Leveling Plugin for Kaiburr.")
