import os
import json
import logging
import time
import re

from lib.shared.timeout import Timeout
from lib.shared.player import Player
import lib.shared.teams as teams
import lib.shared.kaiburrEvent as kaiburrEvent
import lib.shared.colors as colors

Log = logging.getLogger(__name__)

# Load config
config_path = os.path.join(os.path.dirname(__file__), "roundBettingConfig.yaml")
try:
    with open(config_path, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    config = {
        "betting_window_seconds": 30,
        "min_bet_amount": 10,
        "house_cut_percent": 10,
        "early_bird_bonus_percent": 20
    }

class RoundBettingPlugin:
    def __init__(self, serverData):
        self.serverData = serverData
        self.igbc_exports = None
        self.active_bets = {} # Format: { player_id: {"team": "RED", "amount": 100, "effective": 120} }
        self.betting_state: str = "CLOSED" # Can be "CLOSED", "OPEN", "RESOLVING"
        
        self.betting_window_timer = Timeout()
        
        self.betting_window_seconds = config.get("betting_window_seconds", 30)
        self.min_bet_amount = config.get("min_bet_amount", 10)
        self.house_cut_percent = config.get("house_cut_percent", 10)
        self.early_bird_bonus_percent = config.get("early_bird_bonus_percent", 20)
        
        # We need a reference to the global command dictionary
        self.commands = {
            teams.TEAM_GLOBAL: {
                ("bet",): ("!bet <red|blue> <amount|all> - Bet credits on the next round winner", self.cmd_bet)
            }
        }
        
        self._smodCommandList = {
            ("betopen",): ("!betopen - Force-open the betting window (Admin)", self.cmd_betopen)
        }

    def say(self, message):
        if self.serverData.is_extended:
            self.serverData.interface.SvPrint(f"^5[BETTING] ^7{message}")
        else:
            self.serverData.interface.SvSay(f"^5[BETTING] ^7{message}")
    
    def console_say(self, message):
        """Send to all players via console print (doesn't clutter chat)"""
        if self.serverData.is_extended:
            self.serverData.interface.SvPrintCon(f"^5[BETTING] ^7{message}")
        else:
            self.serverData.interface.SvSay(f"^5[BETTING] ^7{message}")
            
    def tell(self, pid, message):
        if self.serverData.is_extended:
            self.serverData.interface.SvPrint(f"^5[BETTING] ^7{message}", target=str(pid))
        else:
            self.serverData.interface.SvTell(pid, f"^5[BETTING] ^7{message}")

    def cmd_bet(self, player: Player, teamId, args):
        pid = player.GetId()
        if self.betting_state != "OPEN":
            self.tell(pid, "Betting is currently closed.")
            return True

        if len(args) < 2:
            self.tell(pid, "Usage: !bet <red|blue> <amount|all>")
            return True
            
        target_team = args[0].upper()
        if target_team not in ["RED", "BLUE"]:
            self.tell(pid, "Invalid team. Use RED or BLUE.")
            return True
            
        if not self.igbc_exports:
            self.tell(pid, "Banking plugin not available.")
            return True
            
        # Check balance
        account = self.igbc_exports.Get("GetAccountByID").pointer(pid)
        if account is None or account.is_dummy_account():
            self.tell(pid, "You must be logged in with a registered account to bet.")
            return True
            
        current_credits = self.igbc_exports.Get("GetCreditsByID").pointer(pid)
        
        # Handle "all" keyword
        is_all_in = args[1].lower() == "all"
        if is_all_in:
            amount = current_credits
            if amount < self.min_bet_amount:
                self.tell(pid, f"You don't have enough credits to bet. Minimum bet is {self.min_bet_amount}.")
                return True
        else:
            try:
                amount = int(args[1])
                if amount < self.min_bet_amount:
                    raise ValueError
            except ValueError:
                self.tell(pid, f"Invalid amount. Minimum bet is {self.min_bet_amount}.")
                return True
            
        if current_credits < amount:
            self.tell(pid, "Insufficient credits.")
            return True
            
        # Check if they already bet on the OTHER team
        if pid in self.active_bets and self.active_bets[pid]["team"] != target_team:
            self.tell(pid, f"You have already bet on {self.active_bets[pid]['team']}. You cannot change teams.")
            return True
            
        # Deduct credits immediately
        success = self.igbc_exports.Get("AddCredits").pointer(pid, -amount)
        if not success:
            self.tell(pid, "Failed to deduct credits.")
            return True
        
        # Calculate early bird bonus based on remaining time in the betting window
        bonus = 0
        if self.early_bird_bonus_percent > 0 and self.betting_window_timer.IsSet():
            # Only apply early bird bonus to the FIRST bet, not incremental top-ups
            if pid not in self.active_bets:
                remaining = self.betting_window_timer.Left()
                bonus_multiplier = remaining / self.betting_window_seconds
                bonus = int(amount * bonus_multiplier * self.early_bird_bonus_percent / 100)
            
        effective_amount = amount + bonus
            
        if pid in self.active_bets:
            self.active_bets[pid]["amount"] += amount
            self.active_bets[pid]["effective"] += effective_amount
        else:
            self.active_bets[pid] = {"team": target_team, "amount": amount, "effective": effective_amount}
            
        total_bet = self.active_bets[pid]["amount"]
        total_effective = self.active_bets[pid]["effective"]
        team_color = "^1" if target_team == "RED" else "^4"
        
        # Announce all-in to everyone
        if is_all_in:
            player_name = colors.StripColorCodes(player.GetName())
            bonus_text = f" (^2+{bonus} early bonus^7)" if bonus > 0 else ""
            self.say(f"^3{player_name} ^7has gone ^5ALL IN ^7with ^3{amount} ^7credits{bonus_text} on {team_color}{target_team}^7!")
        else:
            bonus_text = f" (^2+{bonus} early bonus^7)" if bonus > 0 else ""
            self.tell(pid, f"Bet {amount} credits{bonus_text} on {team_color}{target_team}^7. Total stake: {total_effective}")
        return True

    def cmd_betopen(self, playerName, smodID, adminIP, args):
        self.say("^1[ADMIN] ^7Force-opening the betting window...")
        if self.active_bets:
            self.refund_bets()
            
        self.open_betting()
        return True

    def calculate_payouts(self, winner_team):
        winner_team = re.sub(r'\^[0-9a-zA-Z]', '', winner_team).strip().upper()
        if not self.active_bets:
            return

        # Pools are based on actual credits deposited (amount), not effective
        red_pool = sum(bet["amount"] for bet in self.active_bets.values() if bet["team"] == "RED")
        blue_pool = sum(bet["amount"] for bet in self.active_bets.values() if bet["team"] == "BLUE")
        
        total_pool = red_pool + blue_pool
        if total_pool == 0:
            return
            
        losing_pool = blue_pool if winner_team == "RED" else red_pool
        
        # Effective totals are only used for determining each winner's share proportion
        winning_effective = sum(bet["effective"] for bet in self.active_bets.values() if bet["team"] == winner_team)
        
        # Apply house cut to the losing pool before distributing
        house_cut = int(losing_pool * self.house_cut_percent / 100)
        distributable_pool = losing_pool - house_cut
        
        winner_color = "^1RED" if winner_team == "RED" else "^4BLUE"
        if house_cut > 0:
            self.console_say(f"Round winner: {winner_color}^7! Pool: {losing_pool} credits (^3house cut: {house_cut}^7, distributing: {distributable_pool})")
        else:
            self.console_say(f"Round winner: {winner_color}^7! Distributing {distributable_pool} credits...")
        
        if winning_effective == 0:
            # House wins the losing pool
            self.console_say("No bets on the winning team. The house takes the pool!")
            self.active_bets.clear()
            return

        for pid, bet in self.active_bets.items():
            if bet["team"] == winner_team:
                # Share proportion is based on effective amount (rewards early bettors)
                share_percentage = bet["effective"] / winning_effective
                winnings = int(distributable_pool * share_percentage)
                
                # The winner receives their full effective stake back (original + bonus) + their share of the winnings
                bonus = bet["effective"] - bet["amount"]
                total_return = bet["effective"] + winnings
                
                # Pay out
                if self.igbc_exports:
                    self.igbc_exports.Get("AddCredits").pointer(pid, total_return)
                    if bonus > 0:
                        self.tell(pid, f"^2You won {winnings} credits! ^7(Total return: {total_return} ^3+{bonus}^7 early bonus)")
                    else:
                        self.tell(pid, f"^2You won {winnings} credits! ^7(Total return: {total_return})")
        self.active_bets.clear()

    def refund_bets(self):
        if not self.active_bets:
            return
            
        self.console_say("Round was a draw or interrupted. Refunding all bets.")
        for pid, bet in self.active_bets.items():
            if self.igbc_exports:
                self.igbc_exports.Get("AddCredits").pointer(pid, bet["amount"])
                self.tell(pid, f"Refunded {bet['amount']} credits.")
                
        self.active_bets.clear()

    def open_betting(self):
        # Log.info(f"[RoundBetting] open_betting() called. Previous state: {self.betting_state}")
        self.betting_state = "OPEN"
        self.betting_window_timer.Set(self.betting_window_seconds)
        self.say(f"^3Betting is now OPEN for {self.betting_window_seconds} seconds! ^7Use !bet <red|blue> <amount>")

# Setup logic
RoundBettingPluginInstance: RoundBettingPlugin | None = None

def OnInitialize(serverData, exports=None):
    global RoundBettingPluginInstance
    RoundBettingPluginInstance = RoundBettingPlugin(serverData)
    
    # We must delay getting IGBC exports if it hasn't loaded yet, or get it during Event.
    # It's safer to get it on the first loop or event, but we can try here first.
    return True

def OnStart():
    if RoundBettingPluginInstance:
        PluginInstance = RoundBettingPluginInstance
        serverData = PluginInstance.serverData
        
        PluginInstance.say("RoundBetting plugin started.")
        PluginInstance.open_betting()
        
        # Register chat commands
        newCommands = []
        rCommands = serverData.GetServerVar("registeredCommands")
        if rCommands is not None:
            newCommands.extend(rCommands)

        # Note: PluginInstance.commands uses teams.TEAM_GLOBAL
        for cmd in PluginInstance.commands[teams.TEAM_GLOBAL]:
            newCommands.append((cmd[0], PluginInstance.commands[teams.TEAM_GLOBAL][cmd][0]))
            
        serverData.SetServerVar("registeredCommands", newCommands)
        
        # Register SMOD commands
        newSmod = []
        rSmod = serverData.GetServerVar("registeredSmodCommands")
        if rSmod is not None:
            newSmod.extend(rSmod)
        for cmd in PluginInstance._smodCommandList:
            newSmod.append((cmd[0], PluginInstance._smodCommandList[cmd][0]))
        serverData.SetServerVar("registeredSmodCommands", newSmod)
        return True
    return False

# Periodic state logger - only logs every N seconds to avoid spam
_last_state_log_time = 0

def OnLoop():
    global _last_state_log_time
    if not RoundBettingPluginInstance:
        return False
        
    inst = RoundBettingPluginInstance
    
    # Try fetching IGBC exports if we don't have them yet
    if not inst.igbc_exports:
        igbc_xprt = inst.serverData.API.GetPlugin("plugins.shared.accountsystem.igbc2")
        if igbc_xprt:
            inst.igbc_exports = igbc_xprt.GetExports()
            # Log.info("[RoundBetting] IGBC exports acquired.")
    
    # Log state every 15 seconds so we can see what's happening
    now = time.time()
    if now - _last_state_log_time >= 15:
        _last_state_log_time = now
        betting_left = inst.betting_window_timer.Left() if inst.betting_window_timer.IsSet() else "NOT SET"
        # Log.info(f"[RoundBetting] STATE={inst.betting_state} | betting_timer={betting_left} | active_bets={len(inst.active_bets)}")
            
    if inst.betting_state == "OPEN":
        if inst.betting_window_timer._endS > 0 and inst.betting_window_timer.Left() == 0:
            inst.betting_state = "CLOSED"
            # Log.info("[RoundBetting] Betting window closed. State -> CLOSED. Waiting for RoundWinner.")
            
            # Announce odds
            red_pool = sum(bet["amount"] for bet in inst.active_bets.values() if bet["team"] == "RED")
            blue_pool = sum(bet["amount"] for bet in inst.active_bets.values() if bet["team"] == "BLUE")
            
            if red_pool == 0 and blue_pool == 0:
                inst.console_say("^3Betting is now CLOSED. ^7No bets placed.")
            else:
                total_pool = red_pool + blue_pool
                red_ratio = f"{total_pool / red_pool:.1f}x" if red_pool > 0 else "N/A"
                blue_ratio = f"{total_pool / blue_pool:.1f}x" if blue_pool > 0 else "N/A"
                inst.console_say(f"^3Betting is now CLOSED. ^7Pools - ^1RED: {red_pool} ({red_ratio}) ^7| ^4BLUE: {blue_pool} ({blue_ratio})")
    
    return True

def OnEvent(event):
    if not RoundBettingPluginInstance:
        return False
        
    inst = RoundBettingPluginInstance
    
    # Log ALL events we receive (except high-frequency ones)
    event_type = event.type
    is_startup = getattr(event, 'isStartup', False)
    
    # Map event type numbers to readable names for logging
    EVENT_NAMES = {
        1: "MESSAGE", 2: "INIT", 3: "SHUTDOWN", 4: "CLIENTCONNECT",
        5: "CLIENTDISCONNECT", 6: "CLIENTCHANGED", 7: "KILL", 8: "PLAYER",
        9: "EXIT", 10: "MAPCHANGE", 11: "SMSAY", 12: "POST_INIT",
        13: "REAL_INIT", 14: "PLAYER_SPAWN", 15: "CLIENT_BEGIN",
        16: "SERVER_EMPTY", 17: "SMOD_COMMAND", 18: "SMOD_LOGIN",
        19: "OBJECTIVE", 20: "NAMECHANGE", 21: "BANNED_ENTRY",
        22: "SERVER_SAY", 23: "ROUND_WINNER"
    }
    event_name = EVENT_NAMES.get(event_type, f"UNKNOWN({event_type})")
    
    # # Log important events (not every message/clientchanged/player_spawn)
    # if event_type in (2, 3, 7, 9, 10, 23):
    #     Log.info(f"[RoundBetting] OnEvent: {event_name} (isStartup={is_startup}) | current_state={inst.betting_state}")
    
    # Ignore events generated from parsing old logs during server startup
    if is_startup:
        return False
        
    # Handle InitGame (start of map / restart / next round starts)
    # Enter RESOLVING state and wait for the engine's RoundWinner: line.
    # No timeout needed — the engine guarantees exactly one RoundWinner: per round end.
    if event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_INIT:
        # Log.info(f"[RoundBetting] >>> InitGame received. State: {inst.betting_state} -> RESOLVING")
        if inst.betting_state == "OPEN":
            inst.betting_window_timer.Finish()
        inst.betting_state = "RESOLVING"
        
    # Handle Round Winner (RED, BLUE, or DRAW)
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_ROUND_WINNER:
        winner = getattr(event, 'winner_team', 'UNKNOWN')
        winner_clean = re.sub(r'\^[0-9a-zA-Z]', '', winner).strip().upper()
        # Log.info(f"[RoundBetting] >>> RoundWinner received: '{winner_clean}'. State: {inst.betting_state}")
        
        if winner_clean == "DRAW":
            # Log.info("[RoundBetting] Round was a DRAW. Refunding all bets.")
            inst.betting_state = "CLOSED"
            inst.refund_bets()
            inst.open_betting()
        elif winner_clean in ("RED", "BLUE"):
            if inst.betting_state == "RESOLVING":
                inst.betting_state = "CLOSED"
                # Log.info(f"[RoundBetting] Processing payouts for winner: {winner_clean}")
                inst.calculate_payouts(winner_clean)
                inst.open_betting()
            else:
                # Log.info(f"[RoundBetting] Got RoundWinner while NOT in RESOLVING (state={inst.betting_state}). Processing anyway.")
                inst.calculate_payouts(winner_clean)
                inst.open_betting()
        else:
            Log.warning(f"[RoundBetting] Unknown RoundWinner value: '{winner_clean}'. Ignoring.")
            
    # Handle SMOD Commands
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_SMSAY:
        if event.message.startswith("!"):
            msg = event.message[1:]
            args = msg.split()
            if args:
                cmd = args[0].lower()
                for c in inst._smodCommandList:
                    if cmd in c:
                        return inst._smodCommandList[c][1](event.playerName, event.smodID, event.adminIP, args)
            
    # Handle Chat Messages for betting
    elif event.type == kaiburrEvent.KAIBURR_EVENT_TYPE_MESSAGE:
        if event.client:
            message = event.message
            if message.startswith("!"):
                cmdArgs = message[1:].split()
                if len(cmdArgs) > 0 and cmdArgs[0].lower() == "bet":
                    # Log.info(f"[RoundBetting] !bet command from client {event.client.GetId()}, state={inst.betting_state}")
                    inst.cmd_bet(event.client, event.teamId, cmdArgs[1:])
                    return True

    return False

def OnFinish():
    global RoundBettingPluginInstance
    if RoundBettingPluginInstance:
        # Refund bets on plugin unload just to be safe
        RoundBettingPluginInstance.refund_bets()
        RoundBettingPluginInstance = None
