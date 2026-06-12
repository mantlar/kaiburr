import json
import logging
import os
import random

# Import Godfinger Event system and shared libraries
import godfingerEvent
import lib.shared.client as client
import lib.shared.config as config
import lib.shared.player as player
import lib.shared.serverdata as serverdata
import lib.shared.teams as teams
import lib.shared.colors as colors

Log = logging.getLogger(__name__)

# Global server data instance
SERVER_DATA = None

# Configuration file paths and defaults
DEFAULT_CFG_JSON = os.path.join(os.path.dirname(__file__), "eightballConfig.json")
DEFAULT_CFG = None

CONFIG_FALLBACK = '''{
    "pluginThemeColor": "magenta",
    "MessagePrefix": "[8BALL] ^7: ",
    "CommandPrefix": "!",
    "Responses": [
        "It is certain.",
        "It is decidedly so.",
        "Without a doubt.",
        "Yes - definitely.",
        "You may rely on it.",
        "As I see it, yes.",
        "Most likely.",
        "Outlook good.",
        "Yes.",
        "Signs point to yes.",
        "Reply hazy, try again.",
        "Ask again later.",
        "Better not tell you now.",
        "Cannot predict now.",
        "Concentrate and ask again.",
        "Don't count on it.",
        "My reply is no.",
        "My sources say no.",
        "Outlook not so good.",
        "Very doubtful."
    ]
}'''

try:
    if os.path.exists(DEFAULT_CFG_JSON):
        DEFAULT_CFG = config.Config.from_file(DEFAULT_CFG_JSON, CONFIG_FALLBACK)
        Log.info(f"Loaded configuration from JSON file: {DEFAULT_CFG_JSON}")
    else:
        DEFAULT_CFG = config.Config()
        DEFAULT_CFG.cfg = json.loads(CONFIG_FALLBACK)
        with open(DEFAULT_CFG_JSON, "wt") as f:
            f.write(CONFIG_FALLBACK)
except Exception as e:
    Log.error(f"Error loading configuration: {str(e)}")
    DEFAULT_CFG = config.Config()
    DEFAULT_CFG.cfg = json.loads(CONFIG_FALLBACK)

class EightBallPlayer(player.Player):
    def __init__(self, cl: client.Client):
        super().__init__(cl)

class EightBall(object):
    def __init__(self, serverData : serverdata.ServerData):
        self._config : config.Config = DEFAULT_CFG
        self._themeColor = self._config.cfg["pluginThemeColor"]
        self._players : dict[int, EightBallPlayer] = {}
        self._serverData : serverdata.ServerData = serverData
        self._messagePrefix : str = colors.COLOR_CODES.get(self._themeColor, "^5") + self._config.cfg["MessagePrefix"]
        
        # Command definitions
        self._commandList = \
            {
                teams.TEAM_GLOBAL: {
                    ("8ball", "eightball"): ("!8ball <query> - ask the magic 8-ball a question", self.Handle8Ball)
                },
                teams.TEAM_EVIL: {},
                teams.TEAM_GOOD: {},
                teams.TEAM_SPEC: {}
            }

    def Say(self, saystr : str, usePrefix : bool = True):
        prefix = self._messagePrefix if usePrefix else ""
        if self._serverData.is_extended:
            return self._serverData.interface.SvPrintCon(prefix + saystr)
        else:
            return self._serverData.interface.Say(prefix + saystr)

    def SvSay(self, saystr : str, usePrefix : bool = True):
        prefix = self._messagePrefix if usePrefix else ""
        if self._serverData.is_extended:
            return self._serverData.interface.SvPrint(prefix + saystr)
        else:
            return self._serverData.interface.SvSay(prefix + saystr)

    def Handle8Ball(self, eventPlayer: player.Player, teamId: int, cmdArgs: list[str]) -> bool:
        if not cmdArgs:
            self.SvSay(f"Usage: {self._config.cfg['CommandPrefix']}8ball <question>")
            return True
            
        responses = self._config.cfg["Responses"]
        answer = random.choice(responses)
        
        # Optionally echo the question back, or just print the answer
        self.SvSay(f"{colors.COLOR_CODES['default']}{answer}")
        return True

    def HandleChatCommand(self, player : player.Player, teamId : int, cmdArgs : list[str]) -> bool:
        command = cmdArgs[0].lower()
        if teamId in self._commandList:
            for c in self._commandList[teamId]:
                if command in c:
                    return self._commandList[teamId][c][1](player, teamId, cmdArgs[1:])
        
        for c in self._commandList[teams.TEAM_GLOBAL]:
            if command in c:
                return self._commandList[teams.TEAM_GLOBAL][c][1](player, teamId, cmdArgs[1:])
        return False

    def OnChatMessage(self, eventClient : client.Client, eventMessage : str, eventTeamID : int):
        if eventClient != None:
            commandPrefix = self._config.cfg["CommandPrefix"]
            capture = False
            if eventClient.GetId() in self._players:
                eventPlayer = self._players[eventClient.GetId()]
                if eventMessage.startswith(commandPrefix):
                    eventMessage = eventMessage[len(commandPrefix):]
                    if len(eventMessage) > 0:
                        messageParse = eventMessage.split()
                        return self.HandleChatCommand(eventPlayer, eventTeamID, messageParse)
        return False

    def OnClientConnect(self, eventClient : client.Client):
        newPlayer = EightBallPlayer(eventClient)
        self._players[newPlayer.GetId()] = newPlayer
        return False

    def OnClientDisconnect(self, eventClient : client.Client, reason : int):
        if eventClient.GetId() in self._players:
            del self._players[eventClient.GetId()]
        return False

    def Start(self) -> bool:
        allClients = self._serverData.API.GetAllClients()
        for cl in allClients:
            newPlayer = EightBallPlayer(cl)
            self._players[newPlayer.GetId()] = newPlayer
        return True

def OnStart():
    global PluginInstance
    if not PluginInstance.Start():
        return False
    PluginInstance.Say(f"EightBall plugin loaded.")
    return True

def OnLoop():
    pass

def OnFinish():
    global PluginInstance
    del PluginInstance

def OnInitialize(serverData : serverdata.ServerData, exports=None):
    global SERVER_DATA
    SERVER_DATA = serverData
    
    global PluginInstance
    PluginInstance = EightBall(serverData)
    
    # Register commands with server
    newVal = []
    rCommands = PluginInstance._serverData.GetServerVar("registeredCommands")
    if rCommands != None:
        newVal.extend(rCommands)
    for cmd in PluginInstance._commandList[teams.TEAM_GLOBAL]:
        for i in cmd:
            newVal.append((i, PluginInstance._commandList[teams.TEAM_GLOBAL][cmd][0]))
    SERVER_DATA.SetServerVar("registeredCommands", newVal)
    return True

def OnEvent(event) -> bool:
    global PluginInstance
    if event.type == godfingerEvent.GODFINGER_EVENT_TYPE_MESSAGE:
        return PluginInstance.OnChatMessage(event.client, event.message, event.teamId)
    elif event.type == godfingerEvent.GODFINGER_EVENT_TYPE_CLIENTCONNECT:
        return PluginInstance.OnClientConnect(event.client)
    elif event.type == godfingerEvent.GODFINGER_EVENT_TYPE_CLIENTDISCONNECT:
        return PluginInstance.OnClientDisconnect(event.client, event.reason)
    elif event.type == godfingerEvent.GODFINGER_EVENT_TYPE_INIT:
        return False # no round start needed
    elif event.type == godfingerEvent.GODFINGER_EVENT_TYPE_SHUTDOWN:
        return False
    return False

if __name__ == "__main__":
    print("This is a plugin for the Godfinger Movie Battles II plugin system.")
    input("Press Enter to close this message.")
    exit()
