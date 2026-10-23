
import logging
import os
import subprocess
import re
import time
import threading
import zipfile
import shutil

import lib.shared.kaiburrEvent as kaiburrEvent
import lib.shared.serverdata as serverdata
import lib.shared.config as config
import lib.shared.threadcontrol as threadcontrol
import lib.shared.teams as teams

SERVER_DATA = None

UPDATER_DOWNLOAD_URL = "https://www.moviebattles.org/download/MBII_CLI_Updater.zip"
UPDATER_DLL_NAME = "MBII_CommandLine_Update_XPlatform.dll"

CONFIG_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "mbiiclientupdaterCfg.yaml")
CONFIG_FALLBACK = """{
    "checkIntervalMinutes": 30,
    "updaterDir": "/home/rolphi/mb2server/updater",
    "gamePath": "/home/rolphi/mb2server",
    "mbiiPath": "/home/rolphi/mb2server/MBII",
    "announcementGracePeriodSeconds": 60,
    "announcementRepeatIntervalSeconds": 15,
    "prefix": "^1[UPDATE] ^7",
    "updateDetectedMessage": "An MBII update has been detected! The server will shut down for updates shortly.",
    "countdownMessage": "Server shutting down for updates in {remaining} seconds...",
    "shutdownMessage": "Server is shutting down NOW for MBII updates. See you soon!",
    "forceKickAfterGracePeriod": true
}
"""
global UpdaterConfig
UpdaterConfig = config.Config.from_file(CONFIG_DEFAULT_PATH, CONFIG_FALLBACK)

Log = logging.getLogger(__name__)

PluginInstance = None


class MBIIClientUpdater():
    """
    Periodically checks for MBII game updates using the official CLI updater.
    When an update is found:
      1. Announces the pending update in-game.
      2. Waits for the grace period (re-announcing periodically).
      3. Either force-kicks all remaining players or waits for them to leave.
      4. Applies the update via the CLI updater.
      5. Sends 'quit' via RCON — restartOnCrash brings the server back up.
    """

    # States for the update lifecycle
    STATE_IDLE = 0
    STATE_ANNOUNCED = 1       # Update detected, grace period active
    STATE_WAITING_EMPTY = 2   # Grace period expired, waiting for server empty
    STATE_APPLYING = 3        # Applying the update

    def __init__(self, serverData: serverdata.ServerData):
        self._serverData = serverData
        self.config = UpdaterConfig
        self._threadLock = threading.Lock()
        self._threadControl = threadcontrol.ThreadControl()

        self._state = self.STATE_IDLE
        self._update_file_count = 0
        self._announcement_start_time = 0.0
        self._last_announcement_time = 0.0
        self._last_check_time = 0.0
        self._applying_lock = threading.Lock()

        # Background thread for periodic update checks
        self._check_thread = threading.Thread(
            target=self._check_loop,
            daemon=True,
            args=(self._threadControl,)
        )

        self._commandList = {
            teams.TEAM_GLOBAL: {
                tuple(["checkupdate", "mbiiupdate"]): (
                    "!checkupdate - Force an immediate MBII update check",
                    self.HandleCheckUpdateCommand
                )
            },
            teams.TEAM_EVIL: {},
            teams.TEAM_GOOD: {},
            teams.TEAM_SPEC: {}
        }

    def Start(self) -> bool:
        # Ensure the CLI updater is available on disk
        self._ensure_updater_downloaded()

        self._last_check_time = time.time()
        self._check_thread.start()
        Log.info("MBII Client Updater plugin started. "
                 f"Checking every {self.config.cfg['checkIntervalMinutes']} minutes.")
        return True

    def Finish(self):
        with self._threadLock:
            self._threadControl.stop = True

    # ──── Updater DLL management ───────────────────────────────────────────

    def _get_updater_dll_path(self) -> str:
        """Returns the full path to the updater DLL."""
        return os.path.join(self.config.cfg["updaterDir"], UPDATER_DLL_NAME)

    def _ensure_updater_downloaded(self):
        """Downloads the MBII CLI updater if it doesn't already exist."""
        updater_dir = self.config.cfg["updaterDir"]
        dll_path = self._get_updater_dll_path()

        if os.path.isfile(dll_path):
            Log.info(f"MBII CLI updater found at: {dll_path}")
            return

        Log.info(f"MBII CLI updater not found. Downloading from {UPDATER_DOWNLOAD_URL}...")
        os.makedirs(updater_dir, exist_ok=True)

        zip_path = os.path.join(updater_dir, "MBII_CLI_Updater.zip")
        try:
            # Use wget or urllib to download
            import urllib.request
            urllib.request.urlretrieve(UPDATER_DOWNLOAD_URL, zip_path)
            Log.info(f"Downloaded updater archive to: {zip_path}")

            # Extract the zip
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(updater_dir)
            Log.info(f"Extracted updater to: {updater_dir}")

            # Clean up the zip
            os.remove(zip_path)

            if os.path.isfile(dll_path):
                Log.info(f"MBII CLI updater ready at: {dll_path}")
            else:
                Log.error(f"Updater DLL not found after extraction. "
                          f"Contents: {os.listdir(updater_dir)}")

        except Exception as e:
            Log.error(f"Failed to download MBII CLI updater: {e}")

    # ──── Background check loop ────────────────────────────────────────────

    def _check_loop(self, control):
        """Periodically runs the update check on a background thread."""
        interval_s = self.config.cfg["checkIntervalMinutes"] * 60

        while True:
            stop = False
            with self._threadLock:
                stop = control.stop
            if stop:
                break

            # Only check when idle (not already processing an update)
            if self._state == self.STATE_IDLE:
                elapsed = time.time() - self._last_check_time
                if elapsed >= interval_s:
                    self._run_update_check()
                    self._last_check_time = time.time()

            time.sleep(10)  # Poll state every 10 seconds

    def _run_update_check(self):
        """Runs the dotnet CLI updater in check-only mode (-c)."""
        dll_path = self._get_updater_dll_path()
        game_path = self.config.cfg["gamePath"]

        if not os.path.isfile(dll_path):
            Log.warning(f"MBII updater DLL not found at: {dll_path}")
            return

        try:
            Log.info("Checking for MBII updates...")
            result = subprocess.run(
                ["dotnet", dll_path, "-c", "-path", game_path],
                capture_output=True,
                text=True,
                timeout=120
            )
            output = result.stdout.strip()
            Log.info(f"Update check output: {output}")

            # The updater outputs a number at the start indicating files to update
            match = re.match(r'^(\d+)', output)
            if match:
                file_count = int(match.group(1))
            else:
                file_count = 0

            if file_count > 1:
                Log.info(f"MBII update available! {file_count} files need updating.")
                self._on_update_detected(file_count)
            else:
                Log.info(f"No MBII update needed ({file_count} files).")

        except subprocess.TimeoutExpired:
            Log.error("MBII update check timed out after 120 seconds.")
        except FileNotFoundError:
            Log.error("'dotnet' command not found. Is .NET installed?")
        except Exception as e:
            Log.error(f"Error during MBII update check: {e}")

    # ──── Update lifecycle ─────────────────────────────────────────────────

    def _on_update_detected(self, file_count: int):
        """Called when the check reveals files need updating."""
        with self._threadLock:
            if self._state != self.STATE_IDLE:
                return  # Already handling an update
            self._update_file_count = file_count
            self._state = self.STATE_ANNOUNCED
            self._announcement_start_time = time.time()
            self._last_announcement_time = 0.0

        # Announce immediately
        self._announce_update()

        # If no one is on the server, go straight to applying
        clients = self._serverData.API.GetAllClients()
        if len(clients) == 0:
            Log.info("Server is empty, applying update immediately.")
            self._begin_apply_update()

    def _announce_update(self):
        """Broadcasts the update notice in-game."""
        prefix = self.config.cfg["prefix"]
        msg = self.config.cfg["updateDetectedMessage"]
        try:
            if self._serverData.is_extended:
                self._serverData.interface.SvPrint(prefix + msg, "all")
            else:
                self._serverData.interface.SvSay(prefix + msg)
        except Exception as e:
            Log.error(f"Error sending update announcement: {e}")
        self._last_announcement_time = time.time()

    def _announce_countdown(self, remaining: int):
        """Broadcasts a countdown message."""
        prefix = self.config.cfg["prefix"]
        template = self.config.cfg["countdownMessage"]
        msg = template.format(remaining=remaining)
        try:
            if self._serverData.is_extended:
                self._serverData.interface.SvPrint(prefix + msg, "all")
            else:
                self._serverData.interface.SvSay(prefix + msg)
        except Exception as e:
            Log.error(f"Error sending countdown: {e}")
        self._last_announcement_time = time.time()

    def _announce_shutdown(self):
        """Broadcasts the final shutdown message."""
        prefix = self.config.cfg["prefix"]
        msg = self.config.cfg["shutdownMessage"]
        try:
            if self._serverData.is_extended:
                self._serverData.interface.SvPrint(prefix + msg, "all")
            else:
                self._serverData.interface.SvSay(prefix + msg)
        except Exception as e:
            Log.error(f"Error sending shutdown message: {e}")

    def OnLoop(self):
        """Called each Kaiburr tick — drives the update state machine."""
        if self._state == self.STATE_IDLE or self._state == self.STATE_APPLYING:
            return

        now = time.time()
        grace = self.config.cfg["announcementGracePeriodSeconds"]
        repeat = self.config.cfg["announcementRepeatIntervalSeconds"]

        if self._state == self.STATE_ANNOUNCED:
            elapsed = now - self._announcement_start_time
            remaining = max(0, int(grace - elapsed))

            # Re-announce periodically
            if now - self._last_announcement_time >= repeat:
                if remaining > 0:
                    self._announce_countdown(remaining)
                else:
                    # Grace period is up
                    self._announce_shutdown()

                    if self.config.cfg["forceKickAfterGracePeriod"]:
                        self._kick_all_players()
                        time.sleep(2)  # Brief pause for kick packets to send
                        self._begin_apply_update()
                    else:
                        # Switch to waiting for the server to empty naturally
                        with self._threadLock:
                            self._state = self.STATE_WAITING_EMPTY
                        Log.info("Grace period expired. Waiting for server to empty...")

        elif self._state == self.STATE_WAITING_EMPTY:
            # Re-announce periodically to remind stragglers
            if now - self._last_announcement_time >= repeat * 2:
                prefix = self.config.cfg["prefix"]
                try:
                    if self._serverData.is_extended:
                        self._serverData.interface.SvPrint(
                            prefix + "Waiting for all players to leave for a pending MBII update...", "all")
                    else:
                        self._serverData.interface.SvSay(
                            prefix + "Waiting for all players to leave for a pending MBII update...")
                except Exception:
                    pass
                self._last_announcement_time = now

    def _kick_all_players(self):
        """Kick every connected player."""
        clients = self._serverData.API.GetAllClients()
        for cl in clients:
            try:
                self._serverData.interface.ClientKick(cl.GetId())
            except Exception as e:
                Log.error(f"Error kicking client {cl.GetId()}: {e}")

    def _begin_apply_update(self):
        """Start applying the update in a background thread."""
        with self._applying_lock:
            if self._state == self.STATE_APPLYING:
                return
            with self._threadLock:
                self._state = self.STATE_APPLYING

        update_thread = threading.Thread(target=self._apply_update, daemon=True)
        update_thread.start()

    def _apply_update(self):
        """Runs the actual update and then signals a server restart via quit."""
        Log.info("Applying MBII update...")

        try:
            dll_path = self._get_updater_dll_path()
            game_path = self.config.cfg["gamePath"]
            mbii_path = self.config.cfg["mbiiPath"]

            # Run the updater (without -c flag to actually apply)
            Log.info(f"Running: dotnet {dll_path} -path {game_path}")
            result = subprocess.run(
                ["dotnet", dll_path, "-path", game_path],
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout for the actual update
            )
            Log.info(f"Updater output: {result.stdout}")
            if result.returncode != 0:
                Log.error(f"Updater stderr: {result.stderr}")

            # Copy engine library if needed
            nopp_so = os.path.join(mbii_path, "jampgamei386.nopp.so")
            target_so = os.path.join(mbii_path, "jampgamei386.so")
            if os.path.isfile(nopp_so):
                shutil.copy2(nopp_so, target_so)
                Log.info("Copied jampgamei386.nopp.so -> jampgamei386.so")

            Log.info("MBII update applied successfully. Sending quit to restart server...")

            # Give the server a moment to finish any I/O
            time.sleep(2)

            # Send quit via RCON — restartOnCrash will bring the server back up
            try:
                self._serverData.interface.Request(
                    b"\xff\xff\xff\xffrcon %b quit" % self._serverData.interface._password,
                    tryAgain=False
                )
            except Exception:
                Log.warning("Could not send 'quit' via RCON. The server may need a manual restart.")

        except subprocess.TimeoutExpired:
            Log.error("MBII update timed out after 10 minutes.")
        except Exception as e:
            Log.error(f"Error applying MBII update: {e}")
        finally:
            with self._threadLock:
                self._state = self.STATE_IDLE
                self._update_file_count = 0

    # ──── Event: server empty ──────────────────────────────────────────────

    def OnServerEmpty(self):
        """Called when the KAIBURR_EVENT_TYPE_SERVER_EMPTY event fires."""
        if self._state in (self.STATE_ANNOUNCED, self.STATE_WAITING_EMPTY):
            Log.info("Server is now empty — proceeding with update.")
            self._begin_apply_update()

    # ──── Event: client disconnect ─────────────────────────────────────────

    def OnClientDisconnect(self, client):
        """Check if the server is now empty after a disconnect.
        The SERVER_EMPTY event is the authoritative signal, but this
        is here as a fallback hook if needed in the future."""
        pass

    # ──── Chat commands ────────────────────────────────────────────────────

    def HandleCheckUpdateCommand(self, eventClient, teamId, cmdArgs):
        """Force an immediate update check via chat command."""
        prefix = self.config.cfg["prefix"]
        try:
            if self._serverData.is_extended:
                self._serverData.interface.SvPrint(
                    prefix + "Checking for MBII updates...", "all")
            else:
                self._serverData.interface.SvSay(prefix + "Checking for MBII updates...")
        except Exception:
            pass

        # Run the check in a background thread so we don't block the main loop
        check_thread = threading.Thread(target=self._run_update_check, daemon=True)
        check_thread.start()
        return True

    def HandleChatCommand(self, eventClient, teamId, cmdArgs) -> bool:
        command = cmdArgs[0].lower()
        if command.startswith("!"):
            command = command[1:]

        if teamId in self._commandList:
            for c in self._commandList[teamId]:
                if command in c:
                    return self._commandList[teamId][c][1](eventClient, teamId, cmdArgs)
        return False

    def OnChatMessage(self, eventClient, message: str, teamId: int) -> bool:
        if eventClient is None:
            return False

        if message.startswith("!"):
            message = message[1:]
            if len(message) > 0:
                cmdArgs = message.split()
                return self.HandleChatCommand(eventClient, teamId, cmdArgs)
        return False


# ──── Plugin entry points ──────────────────────────────────────────────────

def OnInitialize(serverData: serverdata.ServerData, exports=None) -> bool:
    logMode = logging.INFO
    if serverData.args.debug:
        logMode = logging.DEBUG
    if serverData.args.logfile != "":
        logging.basicConfig(
            filename=serverData.args.logfile,
            level=logMode,
            format='%(asctime)s %(levelname)08s %(name)s %(message)s')
    else:
        logging.basicConfig(
            level=logMode,
            format='%(asctime)s %(levelname)08s %(name)s %(message)s')

    global SERVER_DATA
    SERVER_DATA = serverData
    global PluginInstance
    PluginInstance = MBIIClientUpdater(serverData)

    # Register chat commands
    newCommands = []
    rChatCommands = SERVER_DATA.GetServerVar("registeredCommands")
    if rChatCommands is not None:
        newCommands.extend(rChatCommands)
    for cmd in PluginInstance._commandList[teams.TEAM_GLOBAL]:
        for alias in cmd:
            if not alias.isdecimal():
                newCommands.append((alias, PluginInstance._commandList[teams.TEAM_GLOBAL][cmd][0]))
    SERVER_DATA.SetServerVar("registeredCommands", newCommands)

    return True


def OnStart():
    global PluginInstance
    return PluginInstance.Start()


def OnLoop():
    global PluginInstance
    PluginInstance.OnLoop()


def OnFinish():
    global PluginInstance
    PluginInstance.Finish()


def OnEvent(event) -> bool:
    global PluginInstance
    if event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MESSAGE:
        return PluginInstance.OnChatMessage(event.client, event.message, event.teamId)
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTDISCONNECT:
        PluginInstance.OnClientDisconnect(event.client)
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SERVER_EMPTY:
        PluginInstance.OnServerEmpty()
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTCONNECT:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENT_BEGIN:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_CLIENTCHANGED:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_INIT:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SHUTDOWN:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_KILL:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_PLAYER:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_EXIT:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MAPCHANGE:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMSAY:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_POST_INIT:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_REAL_INIT:
        return False
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_PLAYER_SPAWN:
        return False
    return False


if __name__ == "__main__":
    print("This is a plugin for the Kaiburr Movie Battles II plugin system. "
          "Please run one of the start scripts in the start directory to use it. "
          "Make sure that this python module's path is included in kaiburrCfg!")
    input("Press Enter to close this message.")
    exit()
