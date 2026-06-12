import logging
import json
import os
import threading
import urllib.request
import urllib.error
import random
from time import time
import godfingerEvent
import lib.shared.serverdata as serverdata
import lib.shared.colors as colors

Log = logging.getLogger(__name__)
SERVER_DATA = None
PluginInstance = None

class OllamabotPlugin:
    def __init__(self, serverData: serverdata.ServerData):
        self._serverData = serverData
        self.config_path = os.path.join(os.path.dirname(__file__), "ollamabotCfg.json")
        self.chat_pool_path = os.path.join(os.path.dirname(__file__), "player_chat_pool.txt")
        self.config = {
            "model": "llama3",
            "system_prompt": "You are a player on a 'Movie Battles II' (a Star Wars Jedi Academy multiplayer mod) game server. You are chatting with other players in the server. Your goal is to blend in as a typical average MB2 player. Use local slang, keep your messages very short, informal, and casual. Do not explain anything, do not use double quotes, and do not apologize for limitations. Respond in 1 short sentence.",
            "periodic_interval": 300,
            "context_length": 10,
            "api_url": "http://127.0.0.1:11434/api/generate",
            "bot_name": "ollamabot"
        }
        self.chat_history = []
        self.response_queue = []
        self.player_messages_pool = []
        self.queue_lock = threading.Lock()
        self.last_periodic_time = time()
        self.is_generating = False
        self.LoadConfig()
        self.LoadChatPool()

    def LoadConfig(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.config.update(data)
            except Exception as e:
                Log.error(f"Failed to load ollamabotCfg.json: {e}")
        else:
            self.SaveConfig()

    def SaveConfig(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            Log.error(f"Failed to save ollamabotCfg.json: {e}")

    def LoadChatPool(self):
        default_pool = [
            "cinder is bad",
            "who is maining sbd",
            "why did you decap",
            "nice main",
            "fa is so broken",
            "this map is terrible",
            "go dotf",
            "recharge clone",
            "lmao nice shot",
            "stop double teaming",
            "wtf was that lag",
            "no block points left",
            "gg",
            "he's 1 hp",
            "dash clone is annoying",
            "who has the detonator",
            "defend the objective",
            "you got repped",
            "lol",
            "open mode is better",
            "need a rep clone",
            "why legends mode",
            "snipers are so annoying"
        ]
        if os.path.exists(self.chat_pool_path):
            try:
                with open(self.chat_pool_path, "r", encoding="utf-8") as f:
                    self.player_messages_pool = [line.strip() for line in f if line.strip()]
                if not self.player_messages_pool:
                    self.player_messages_pool = default_pool
                elif len(self.player_messages_pool) > 500:
                    self.player_messages_pool = self.player_messages_pool[-500:]
            except Exception as e:
                Log.error(f"Failed to load player_chat_pool.txt: {e}")
                self.player_messages_pool = default_pool
        else:
            self.player_messages_pool = default_pool
            self.SaveChatPool()

    def SaveChatPool(self):
        try:
            with open(self.chat_pool_path, "w", encoding="utf-8") as f:
                for msg in self.player_messages_pool:
                    f.write(msg + "\n")
        except Exception as e:
            Log.error(f"Failed to save player_chat_pool.txt: {e}")

    def OnMessage(self, client, message):
        msg_text = message.strip()
        sender_name = colors.StripColorCodes(client.GetName())
        
        # Strip triggering bot commands for chat history
        display_text = msg_text
        if msg_text.startswith("!ai "):
            display_text = msg_text[4:].strip()
            
        # Add to chat history (used for conversation context)
        self.chat_history.append((sender_name, display_text))
        if len(self.chat_history) > self.config["context_length"]:
            self.chat_history.pop(0)

        # Collect player messages for style guide (excluding admin commands and bot's own messages)
        is_real_command = (msg_text.startswith("!") and not msg_text.startswith("!ai ")) or msg_text.startswith("/")
        is_bot = sender_name.lower() == self.config["bot_name"].lower()
        if not is_real_command and not is_bot:
            if display_text:
                self.player_messages_pool.append(display_text)
                if len(self.player_messages_pool) > 500:
                    self.player_messages_pool.pop(0)
                self.SaveChatPool()

        should_trigger = False

        if msg_text.startswith("!ai "):
            should_trigger = True
        elif self.config["bot_name"].lower() in msg_text.lower():
            should_trigger = True

        if should_trigger:
            self.TriggerOllama()

        return False

    def TriggerOllama(self):
        with self.queue_lock:
            if self.is_generating:
                return
            self.is_generating = True
            # Reset the periodic timer so it doesn't fire immediately after a manual command
            self.last_periodic_time = time()
            
        # Fire off a thread
        t = threading.Thread(target=self._OllamaWorker)
        t.start()

    def _OllamaWorker(self):
        # Format name dynamically
        system_prompt = self.config["system_prompt"].replace("{bot_name}", self.config["bot_name"])
        
        # Inject style guide dynamically from collected player messages pool
        if self.player_messages_pool:
            # Filter and sanitize messages for style examples
            filtered_msgs = []
            for msg in reversed(self.player_messages_pool):
                msg_clean = msg.strip()
                if not msg_clean:
                    continue
                # Skip if too long (typical MB2 chat is short, long messages are usually copy-pastes/jailbreaks)
                if len(msg_clean) > 60:
                    continue
                # Skip potential jailbreaks, instructions
                msg_lower = msg_clean.lower()
                jailbreak_words = ["ignore", "instruction", "prompt", "system:", "translate", "assistant", "system prompt"]
                if any(w in msg_lower for w in jailbreak_words):
                    continue
                filtered_msgs.append(msg_clean)
                if len(filtered_msgs) >= 30:
                    break
            filtered_msgs.reverse()
            
            if filtered_msgs:
                style_examples = "\n".join([f"- {msg}" for msg in filtered_msgs])
                style_guide = (
                    f"\n\nHere are some actual recent chat messages from players on this server. "
                    f"You MUST analyze and mimic their exact writing style, tone, vocabulary, spelling, grammar, "
                    f"capitalization, punctuation, and length. Speak like them:\n"
                    f"{style_examples}\n\n"
                    f"Your response MUST be: a brief, casual sentence, informal and natural, directly addressing or reacting to the user's statement. "
                    f"Do NOT output double quotes. Do NOT say you are an AI. "
                    f"Do NOT output any explanations or asterisks. "
                    f"Output ONLY your spoken dialogue, prefixed with '{self.config['bot_name']}: '"
                )
                system_prompt += style_guide

        # Get up to 3 last replies from the bot in chat history to avoid repeating
        last_bot_replies = [msg.strip() for name, msg in reversed(self.chat_history) if name == self.config["bot_name"]][:3]
        last_bot_replies = [r for r in last_bot_replies if r]
        
        reply = ""
        success = False
        api_url = self.config["api_url"]
        if "/api/generate" in api_url:
            api_url = api_url.replace("/api/generate", "/api/chat")
            
        for attempt in range(3):
            # Modify system prompt/messages for retry attempts to enforce diversity
            current_system_prompt = system_prompt
            if attempt > 0 and last_bot_replies:
                avoid_str = ", ".join([f"'{r}'" for r in last_bot_replies])
                current_system_prompt += f"\n\nCRITICAL: Do NOT respond with any of the following recently used messages: {avoid_str}. You must say something different, using other words or game slang."
            
            messages = [{"role": "system", "content": current_system_prompt}]
            
            for name, msg in self.chat_history:
                if name == self.config["bot_name"]:
                    messages.append({"role": "assistant", "content": f"{name}: {msg}"})
                else:
                    messages.append({"role": "user", "content": f"{name}: {msg}"})
            
            enforcement_string = f"\n\n(Reply as {self.config['bot_name']} in one short sentence. Output ONLY your dialogue prefixed with '{self.config['bot_name']}: '. Do not explain your response.)"
            if len(messages) > 1 and messages[-1]["role"] == "user":
                messages[-1]["content"] += enforcement_string
            else:
                messages.append({"role": "user", "content": f"Respond to the chat.{enforcement_string}"})

            # Use options with temperature scaling and repetition penalty
            # Temperature starts at 0.8. On retry we increase to 0.95 or 1.1 to encourage creativity.
            temp = 0.8 if attempt == 0 else (0.95 if attempt == 1 else 1.1)
            data = {
                "model": self.config["model"],
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temp,
                    "repeat_penalty": 1.3,
                    "repeat_last_n": 10
                }
            }
            
            try:
                req = urllib.request.Request(
                    api_url,
                    data=json.dumps(data).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    
                    if "message" in result and "content" in result["message"]:
                        cand_reply = result["message"]["content"].strip()
                    else:
                        cand_reply = result.get("response", "").strip()

                    if cand_reply:
                        # Strip <think> blocks
                        if "<think>" in cand_reply and "</think>" in cand_reply:
                            cand_reply = cand_reply.split("</think>")[-1].strip()
                        elif cand_reply.startswith("<think>"):
                            cand_reply = ""
                            
                        # Clean bot name prefix
                        bot_name_lower = self.config['bot_name'].lower()
                        for prefix in [bot_name_lower, f"[{bot_name_lower}]", f"**{bot_name_lower}**"]:
                            if cand_reply.lower().startswith(prefix):
                                cand_reply = cand_reply[len(prefix):].strip()
                                cand_reply = cand_reply.lstrip(":").strip()
                                break
                            
                        cand_reply = self._sanitize_for_q3(cand_reply)
                        cand_reply = cand_reply.replace('\n', ' ').strip()
                        
                        # Validate reply is not a duplicate
                        if cand_reply.lower() not in [r.lower() for r in last_bot_replies]:
                            reply = cand_reply
                            success = True
                            break
                        else:
                            Log.warning(f"Ollama generated a duplicate response on attempt {attempt}: '{cand_reply}'. Retrying...")
            except Exception as e:
                Log.error(f"Ollama API request failed on attempt {attempt}: {e}")
                
        # Fallback to the latest parsed candidate reply if all retry attempts failed or returned duplicates
        if not success and 'cand_reply' in locals() and cand_reply:
            reply = cand_reply

        if reply:
            with self.queue_lock:
                self.response_queue.append(reply)
        else:
            Log.warning("Ollama worker finished without generating any valid non-empty reply.")
            
        with self.queue_lock:
            self.is_generating = False

    def OnLoop(self):
        # Trigger periodic message
        periodic_interval = self.config.get("periodic_interval", 0)
        if periodic_interval > 0:
            current_time = time()
            if current_time - self.last_periodic_time >= periodic_interval:
                self.last_periodic_time = current_time
                # Only trigger if there is conversation history
                if len(self.chat_history) > 0:
                    self.TriggerOllama()

        with self.queue_lock:
            while self.response_queue:
                reply = self.response_queue.pop(0)
                
                # Add bot's own response to chat history
                self.chat_history.append((self.config['bot_name'], reply))
                if len(self.chat_history) > self.config["context_length"]:
                    self.chat_history.pop(0)
                
                # Ensure we don't exceed max length for a single server command (usually ~256 chars for Say)
                chunks = self._chunk_message(reply, 150)
                for chunk in chunks:
                    message_prefix = colors.ColorizeText(f"[{self.config['bot_name']}]", "blue") + "^7: "
                    self._serverData.interface.Say(message_prefix + chunk)

    def _sanitize_for_q3(self, text):
        replacements = {
            '“': '"', '”': '"',
            '‘': "'", '’': "'",
            '—': '-', '–': '-',
            '…': '...'
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        # remove remaining non-ascii safely
        return text.encode('ascii', 'ignore').decode('ascii')

    def _chunk_message(self, message, max_length):
        words = message.split(' ')
        chunks = []
        current_chunk = []
        current_len = 0
        for word in words:
            if current_len + len(word) + 1 > max_length:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = [word]
                    current_len = len(word)
                else: 
                    # A single word is longer than max_length
                    chunks.append(word[:max_length])
                    pass
            else:
                current_chunk.append(word)
                current_len += len(word) + 1
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        return chunks

def OnInitialize(serverData: serverdata.ServerData, exports=None) -> bool:
    global SERVER_DATA
    SERVER_DATA = serverData
    global PluginInstance
    PluginInstance = OllamabotPlugin(serverData)
    return True

def OnStart():
    return True

def OnLoop():
    if PluginInstance:
        PluginInstance.OnLoop()

def OnFinish():
    pass

def OnEvent(event) -> bool:
    if event.type == godfingerEvent.GODFINGER_EVENT_TYPE_MESSAGE:
        if PluginInstance:
            return PluginInstance.OnMessage(event.client, event.message)
    return False
