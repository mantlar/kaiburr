Ollamabot Kaiburr Plugin

This plugin intercepts chat messages and uses a local Ollama instance to generate AI responses.

Requirements:
- Python 3
- A local Ollama instance running on your machine (or accessible over the network).

Installation:
1. Add "plugins.shared.ollamabot.ollamabotPlugin" to the "Plugins" list in "kaiburrCfg.json".
2. Edit "plugins/shared/ollamabot/ollamabotCfg.json" to configure:
   - model: The model you have pulled in Ollama (e.g., "llama3").
   - system_prompt: The persona the AI will take on.
   - random_chance: decimal (e.g. 0.05 for 5%) chance to randomly reply to chat.
   - context_length: How many past messages to include in the prompt.
   - api_url: The URL to your Ollama API (usually "http://127.0.0.1:11434/api/generate").
   - bot_name: The name used as a prefix for the bot's messages, and also to trigger the bot if mentioned.

Usage:
Players can trigger the bot in-game by:
- Typing "!ai <message>"
- Mentioning the bot's name (e.g. "ollamabot")
- The bot may also randomly reply based on "random_chance".
