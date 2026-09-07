import time
import logging
import os
import lib.shared.kaiburrEvent as kaiburrEvent
import lib.shared.config as config
import lib.shared.serverdata as serverdata

Log = logging.getLogger(__name__)

CONFIG_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "multikillCfg.yaml")
CONFIG_FALLBACK = """
enabled: true
timeWindow: 4.0
broadcast: false
streaks:
  2:
    msg: "^1DOUBLE KILL!"
    sound: "sound/multikill/doublekill.wav"
  3:
    msg: "^1TRIPLE KILL!"
    sound: "sound/multikill/triplekill.wav"
"""

class MultikillPlugin:
    def __init__(self):
        self._serverData = None
        self._config = config.Config.from_file(CONFIG_DEFAULT_PATH, CONFIG_FALLBACK)
        # Track active kill streaks: {client_id: {"count": int, "last_time": float}}
        self._streaks = {}

    def OnInitialize(self, serverData: serverdata.ServerData, exports=None) -> bool:
        self._serverData = serverData
        return True

    def OnStart(self):
        enabled = self._config.cfg.get("enabled", True)
        if enabled:
            Log.info("Multikill plugin enabled and tracking kills.")
        else:
            Log.info("Multikill plugin is disabled via config.")
        return True

    def OnLoop(self):
        pass

    def OnFinish(self):
        self._streaks.clear()

    def ResetStreak(self, client_id: int):
        if client_id in self._streaks:
            del self._streaks[client_id]

    def _HandleKill(self, killer_id: int, victim_id: int):
        # Ignore world kills or suicides
        if killer_id < 0 or killer_id == victim_id:
            return

        current_time = time.time()
        time_window = self._config.cfg.get("timeWindow", 4.0)

        # Reset victim's streak if they die
        self.ResetStreak(victim_id)

        # Update killer's streak
        if killer_id not in self._streaks:
            self._streaks[killer_id] = {"count": 1, "last_time": current_time}
            return

        streak = self._streaks[killer_id]
        if current_time - streak["last_time"] <= time_window:
            streak["count"] += 1
            streak["last_time"] = current_time
            self._TriggerMultikill(killer_id, streak["count"])
        else:
            # Time window expired, start a new streak
            streak["count"] = 1
            streak["last_time"] = current_time

    def _TriggerMultikill(self, client_id: int, count: int):
        streaks = self._config.cfg.get("streaks", {})
        
        # Determine the streak data to use
        streak_data = None
        if count in streaks:
            streak_data = streaks[count]
        else:
            # If the count exceeds the defined streaks, cap it at the highest defined
            if streaks:
                max_count = max(streaks.keys())
                if count > max_count:
                    streak_data = streaks[max_count]

        if not streak_data:
            return

        msg = streak_data.get("msg", "")
        sound = streak_data.get("sound", "")
        broadcast = self._config.cfg.get("broadcast", False)
        
        interface = self._serverData.interface
        if broadcast:
            if msg:
                interface.SvCenterPrint(msg, 2)
            if sound:
                interface.SvSound(sound)
        else:
            if msg:
                interface.ClientCenterPrint(client_id, msg, 2)
            if sound:
                interface.ClientSound(sound, client_id)


    def OnEvent(self, event) -> bool:
        if not self._config.cfg.get("enabled", True):
            return False

        if event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_KILL:
            killer = event.client
            victim = event.victim
            if killer and victim:
                self._HandleKill(killer.GetID(), victim.GetID())

        elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTDISCONNECT:
            cl = event.client
            if cl:
                self.ResetStreak(cl.GetID())

        elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_PLAYER_SPAWN:
            cl = event.client
            if cl:
                self.ResetStreak(cl.GetID())

        elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MAPCHANGE:
            self._streaks.clear()

        return False

PluginInstance = MultikillPlugin()

def OnInitialize(serverData: serverdata.ServerData, exports=None) -> bool:
    return PluginInstance.OnInitialize(serverData, exports)

def OnStart() -> bool:
    return PluginInstance.OnStart()

def OnLoop():
    PluginInstance.OnLoop()

def OnFinish():
    PluginInstance.OnFinish()

def OnEvent(event) -> bool:
    return PluginInstance.OnEvent(event)
