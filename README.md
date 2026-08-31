<div align="center">

# ⚡ Kaiburr

### A modular scripting platform for MBII OpenJK servers

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3127/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-blue?style=for-the-badge)](#)

---

**Kaiburr** is a plugin-driven script extension system that provides streamlined RCON & log-based interaction for [Movie Battles II](https://www.moviebattles.org/) dedicated servers. Built in Python, it gives server owners the tools to build anything from simple chat commands to full-featured game systems.

*Originally created by [ViceDice](https://github.com/ViceDice) & [ACHUTA](https://github.com/mantlar) for the [MBII Supremacy Project](https://community.moviebattles.org/threads/supremacy-release-tracker.10667/).*

</div>

---

## ✨ Features

| Category | Capabilities |
|----------|-------------|
| 🗳️ **Voting Systems** | Rock the Vote (RTV), Rock the Mode (RTM), Votekick, Votemute, Vote Team Swap |
| 🎮 **Game Management** | Auto-moderation, Anti-AFK, Anti-Padawan, TK Manager, Whitelist, Bouncer |
| 💰 **Economy & Stats** | Account system, Credits/Banking, Round Betting, ELO rankings, Analytics |
| 🤖 **Integrations** | Discord bot (reports, chat bridge, admin logs), PUG system, AI chatbot (Ollama) |
| 🔧 **Server Ops** | Watchdog auto-restart, Git-based update system, Docker support, WinSCP FTP sync |
| 🎵 **Fun** | Soundboard, 8-Ball, Auto-messages, Custom gamemodes |

## 🚀 Quick Start

### Prerequisites

- [**Python 3.11+**](https://www.python.org/downloads/)
- [**Git**](https://git-scm.com/downloads/) installed on your system
- A running MBII dedicated server with RCON access

### 1. Clone & Install

```bash
git clone https://github.com/mantlar/kaiburr.git
cd kaiburr
```


### 2. Configure your server.cfg

Add the following cvars to your MBII server config:

```cfg
set g_logExplicit    "3"
set g_logSync        "1"
set com_logChat      "2"
set g_logClientInfo  "1"
set g_statLog        "1"
set g_statLogFile    "statlog.log"
set com_logfile      "2"
set com_logSync      "1"
set logfile          "2"
```

> [!TIP]
> Set `sv_maxOOBRateIP` to at least `3` to prevent RCON rate limiting. Increase if rate limiting persists.

### 3. Start Kaiburr

| Platform | Command |
|----------|---------|
| Windows | `kaiburr.bat` |
| Linux / macOS | `./kaiburr.sh` |

On the very first run, Kaiburr will automatically:
1. Create a virtual environment and install all dependencies.
2. Launch an interactive **Setup Wizard** to configure your server paths and RCON password.
3. Start the MBII server (if autostart is enabled) and Kaiburr itself.

On subsequent runs, it skips setup and boots directly.

> [!NOTE]
> If you installed MBII inside `C:\Program Files (x86)`, you may need to run Kaiburr as Administrator so it can create the virtual environment successfully.

---

## 📖 Documentation

<details>
<summary><strong>📋 Configuration Reference (kaiburrCfg.json)</strong></summary>

<br>

| Key | Description |
|-----|-------------|
| `Remote.address.ip` | Server IP address (usually `127.0.0.1` for localhost) |
| `Remote.address.port` | Server port (default: `29070`) |
| `Remote.bindAddress` | Bind address for the script (usually same as IP) |
| `Remote.password` | RCON password (must match `server.cfg`) |
| `MBIIPath` | File path to your MBII installation |
| `logFilename` | Server log file name (default: `server.log`) |
| `serverFileName` | Server executable file name |
| `logicDelay` | Interval between script heartbeat loops |
| `logReadDelay` | Interval between log file reads |
| `restartOnCrash` | Auto-restart on fatal exception (`true`/`false`) |
| `interface` | Interface mode: `"rcon"` (default) or `"pty"` |
| `prologueMessage` | Message shown on successful startup |
| `epilogueMessage` | Message shown on clean shutdown |
| `Plugins` | Array of plugin package paths to load |

#### Watchdog Settings

| Key | Description |
|-----|-------------|
| `watchdog.enabled` | Enable auto-restart when MB2 server process dies |
| `watchdog.restartServer` | Attempt restart on crash |
| `watchdog.serverStartCommand` | Path to server start script (auto-detected if empty) |

#### Interface Modes

**RCON** (default) — Connects via RCON and parses the server log file:

| Key | Description |
|-----|-------------|
| `interfaces.rcon.Remote.address.ip` | Server IP |
| `interfaces.rcon.Remote.address.port` | Server port |
| `interfaces.rcon.Remote.password` | RCON password |
| `interfaces.rcon.logFilename` | Log file name |
| `interfaces.rcon.logReadDelay` | Log read interval |

**PTY** — Wraps the mbiided process via pseudo-terminal (Linux/macOS):

| Key | Description |
|-----|-------------|
| `interfaces.pty.target` | Path to MBII dedicated server executable |
| `interfaces.pty.inputDelay` | Terminal heartbeat interval |

> [!NOTE]
> Python may require double backslashes `\\` in JSON file paths on Windows.
> e.g: `C:\\Program Files (x86)\\SteamCMD\\JKA\\Gamedata\\MBII\\`

</details>

<details>
<summary><strong>🔌 Plugin System</strong></summary>

<br>

Plugins are loaded as Python packages via `kaiburrCfg.json`:

```json
{
    "Plugins": [
        {
            "path": "plugins.shared.pluginfolder.pluginfile"
        }
    ]
}
```

| Segment | Meaning |
|---------|---------|
| `plugins` | Root plugins directory (do not modify) |
| `shared` | `shared` for public plugins, `private` for your own |
| `pluginfolder` | Your plugin's folder name |
| `pluginfile` | Your plugin's `.py` file (omit the extension) |

> [!TIP]
> Place a `requirements.txt` alongside your plugin for any extra dependencies. See the [test plugin](https://github.com/mantlar/kaiburr/blob/main/plugins/shared/test/testPlugin.py) for a minimal example.

### Included Plugins

| Plugin | Description |
|--------|-------------|
| `RTV` | Rock the Vote / Rock the Mode with nominations, conditional maps, and tiebreakers |
| `RoundBetting` | Bet credits on round outcomes with early-bird bonuses |
| `accountsystem` | Persistent player accounts with credit banking |
| `ghost_yoda` | Discord bot — reports, admin action logs, chat bridge, account sync |
| `pug` | Discord-integrated PUG queue system |
| `votekick` | Player-initiated votekick with IP protection |
| `votemute` | Player-initiated votemute |
| `voteteamswap` | Vote to enable/disable team swapping |
| `tkmanager` | Automated teamkill tracking and punishment |
| `automod` | Automated moderation actions |
| `antiafk` | AFK detection and handling |
| `antipadawan` | Padawan rank restrictions |
| `bouncer` | Connection filtering |
| `whitelist` | IP/player whitelist management |
| `automessage` | Timed server announcements |
| `analytics` | Player and server statistics |
| `elo` | ELO-based ranking system |
| `soundboard` | In-game sound effects |
| `EightBall` | Magic 8-Ball chat command |
| `ollamabot` | AI chatbot powered by Ollama |
| `gittracker` | Git-based asset tracking with optional WinSCP sync |
| `vpnmonitor` | VPN/proxy detection |
| `discordbot` | Legacy Discord integration |
| `autoclient` | Automated client management |

</details>

<details>
<summary><strong>🐳 Docker Deployment</strong></summary>

<br>

> [!IMPORTANT]
> Ensure [Docker](https://docs.docker.com/get-started/get-docker/) is installed:
> `sudo apt install -y docker.io && pip install docker`

Kaiburr supports Docker for isolated server instances on Linux.

#### Option 1: Manual Build

```bash
# From the kaiburr root directory
docker build -f docker/Dockerfile -t kaiburr .

docker run --rm -it \
  -v $(pwd)/../dockerize:/app/jediacademy \
  -v $(pwd):/app/jediacademy/gamedata/kaiburr \
  -v $(pwd)/../configstore_kaiburr:/app/jediacademy/gamedata/kaiburr \
  -p 29070:29070/udp \
  -p 29070:29070/tcp \
  kaiburr
```

#### Option 2: Automated Build

```bash
chmod +x docker/build-image.sh
cd docker/
./build-image.sh
```

#### Option 3: Pterodactyl Egg

1. Access Admin Panel → Nests → Create a New Nest
2. Eggs → Create Egg → Import `docker/kaiburr-egg.json`
3. Select the Kaiburr Egg and apply your Docker Image

#### Directory Structure

Ensure Jedi Academy & MBII are installed in a sibling `dockerize/` directory:

```
dockerize/
└── gamedata/
    └── MBII/

kaiburr/
└── (project root)

configstore_kaiburr/        # Your config overlay
├── kaiburrCfg.json
└── plugins/
    └── shared/
        └── myplugin/
            ├── pluginCfg.json
            └── envfile.env
```

</details>

<details>
<summary><strong>🔑 Private Codebase Deployments</strong></summary>

<br>

Deploy private plugin codebases using [SSH deploy keys](https://docs.gitlab.com/user/project/deploy_keys/):

1. Run the `update` process — a `deployments.env` will be generated
2. Edit `deployments.env` with your SSH key paths:
   ```
   {user}/{repo}/{branch}=./key/{keyfile}
   ```
3. Each entry generates a folder in `./update/deploy/<foldername>/`
4. Add the deploy path to `kaiburrCfg.json`:
   ```json
   {
       "paths": [
           ".\\",
           "update\\deploy\\<foldername>\\"
       ]
   }
   ```

> [!TIP]
> Use `.gitignore` in your private repo to prevent config files from being overwritten on update.

</details>

<details>
<summary><strong>📡 WinSCP FTP Sync</strong></summary>

<br>

Sync your `gamedata/` directory to a remote server via FTP:

1. Run `installwinSCP_portable.bat` to install portable WinSCP
2. Edit the generated `winscp_sync_gamedata.bat` in `/venv/portable_winSCP/`:
   ```batch
   SET "FTP_HOST=your_ftp_host.com"
   SET "FTP_USER=your_ftp_username"
   SET "FTP_PASS=your_ftp_password"
   SET "REMOTE_FTP_PATH=/path/on/ftp/server/to/Gamedata"
   ```
3. Enable in the [gittracker](https://github.com/mantlar/kaiburr/tree/main/plugins/shared/gittracker) plugin by setting `isWinSCPBuilding` to `true`

</details>

---

## ⚠️ Known Limitations

| # | Issue |
|---|-------|
| 1 | Python requires double backslashes `\\` in JSON file paths on Windows |
| 2 | RCON payload limits: **138 bytes** (svsay), **993 bytes** (vstr), **2048 bytes** (general). Rate: ~5 messages per 20ms. Exceeding this blocks the calling thread until the next frame. |
| 3 | Kaiburr requires [Git](https://git-scm.com/downloads/) to be installed and available in your system's PATH. |

---

## 📜 License

[MIT License](LICENSE) — Originally created for the MBII Supremacy Project, now available for public use.

## 🤝 Contributing

Pull requests for bugfixes and optimizations are welcome! Please target the [`merge`](https://github.com/mantlar/kaiburr/tree/merge) branch for review before merging into `dev` or `main`.

> Custom plugins will not be accepted upstream, outside of widely-useful plugins like RTV designed for general server hosting.
