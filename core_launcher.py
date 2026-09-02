import os
import sys
import subprocess
import time
import urllib.request
import winreg

KAIBURR_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(KAIBURR_ROOT, "kaiburrCfg.yaml")

def print_header(text):
    print("\n" + "="*50)
    print(f" {text}")
    print("="*50)

def run_command(cmd, desc):
    print(f"[*] {desc}...")
    try:
        subprocess.run(cmd, check=True, cwd=KAIBURR_ROOT)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] {desc} failed: {e}")
        sys.exit(1)

def check_msvc():
    print_header("Checking Dependencies")
    if os.name != 'nt':
        return
        
    target_name = "Microsoft Visual C++ 2015-2022 Redistributable (x86)"
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
    ]

    installed = False
    for root, path in reg_paths:
        try:
            with winreg.OpenKey(root, path) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                            if target_name in display_name:
                                installed = True
                                break
                    except (FileNotFoundError, OSError):
                        continue
        except FileNotFoundError:
            continue
            
    if installed:
        print("[OK] MSVC Redistributable (x86) is already installed.")
    else:
        print("[!] MSVC Redistributable (x86) is missing. Installing...")
        installer_path = "vc_redist.x86.exe"
        url = "https://download.microsoft.com/download/2/e/6/2e61cfa4-993b-4dd4-91da-3737cd5cd6e3/vcredist_x86.exe"
        urllib.request.urlretrieve(url, installer_path)
        subprocess.run([installer_path, "/install", "/quiet", "/norestart"], check=True)
        if os.path.exists(installer_path):
            os.remove(installer_path)
        print("[OK] MSVC Redistributable installed.")

def setup_environment():
    print_header("Preparing Environment")
    
    # Upgrade pip
    run_command([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], "Upgrading pip")
    
    # Core requirements
    run_command([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], "Installing core requirements")
    
    # Platform requirements
    if os.name == 'nt':
        run_command([sys.executable, "-m", "pip", "install", "-r", "win_requirements.txt"], "Installing Windows requirements")
    
    # Find all plugin requirements
    plugins_dir = os.path.join(KAIBURR_ROOT, "plugins")
    for root, dirs, files in os.walk(plugins_dir):
        if "requirements.txt" in files:
            req_path = os.path.join(root, "requirements.txt")
            plugin_name = os.path.basename(root)
            run_command([sys.executable, "-m", "pip", "install", "-r", req_path], f"Installing requirements for plugin '{plugin_name}'")
            
    check_msvc()

def auto_detect_paths():
    current_dir = KAIBURR_ROOT
    max_depth = 5
    depth = 0
    
    while depth < max_depth:
        if os.path.exists(os.path.join(current_dir, "mbiided.x86.exe")) or os.path.exists(os.path.join(current_dir, "mbiided.i386")):
            server_path = current_dir
            mbii_path = os.path.join(server_path, "MBII")
            if os.path.exists(mbii_path):
                return server_path, mbii_path
        
        parent = os.path.dirname(current_dir)
        if parent == current_dir:
            break
        current_dir = parent
        depth += 1
        
    return None, None

def run_setup_wizard():
    print_header("Kaiburr - First Run Setup")
    print("Welcome to Kaiburr! Let's get your configuration set up.\n")
    
    password = input("[1/4] Enter your server's RCON password: ").strip()
    while not password:
        password = input("      Password cannot be empty. Try again: ").strip()
        
    port_str = input("[2/4] Enter your server port [29070]: ").strip()
    port = int(port_str) if port_str.isdigit() else 29070
    
    server_path, mbii_path = auto_detect_paths()
    
    if mbii_path:
        print(f"\n[3/4] Auto-detected MBII Path: {mbii_path}")
        use_auto = input("      Use this path? [Y/n]: ").strip().lower()
        if use_auto == 'n':
            mbii_path = input("      Enter manual MBII Path: ").strip()
    else:
        mbii_path = input("\n[3/4] Enter your MBII Path (e.g., C:/JediAcademy/GameData/MBII): ").strip()
        
    if server_path:
        print(f"\n[4/4] Auto-detected Server Executable Path: {server_path}")
        use_auto = input("      Use this path? [Y/n]: ").strip().lower()
        if use_auto == 'n':
            server_path = input("      Enter manual Server Path: ").strip()
    else:
        server_path = input("\n[4/4] Enter your Server Path (where mbiided is located): ").strip()
        
    print("\n[Optional] Do you want Kaiburr to automatically start the MBII server if it's not running?")
    autostart = input("      Enable autostart? [y/N]: ").strip().lower() == 'y'

    # Convert paths to use forward slashes to avoid JSON escaping issues
    mbii_path = mbii_path.replace('\\', '/')
    server_path = server_path.replace('\\', '/')
    
    server_file_name = "mbiided.x86.exe" if os.name == 'nt' else "mbiided.i386"
    
    config = {
        "Name": "MBII Kaiburr",
        "MBIIPath": mbii_path,
        "logFilename": "server.log",
        "serverPath": server_path,
        "serverFileName": server_file_name,
        "logicDelay": 0.016,
        "restartOnCrash": False,
        "watchdog": {
            "enabled": autostart,
            "restartServer": autostart,
            "serverStartCommand": ""
        },
        "floodProtection": {
            "enabled": False,
            "soft": False,
            "seconds": 1.5
        },
        "interfaces": {
            "rcon": {
                "ip": "localhost",
                "bindAddress": "localhost",
                "logReadDelay": 0.1,
                "Remotes": [
                    {
                        "port": port,
                        "emitterEnabled": False,
                        "emitterPort": 29071,
                        "logFilename": "server.log",
                        "qconsoleFilename": "qconsole.log",
                        "password": password
                    }
                ],
                "Debug": {
                    "TestRetrospect": False
                }
            }
        },
        "interface": "rcon",
        "paths": ["./"],
        "prologueMessage": "Initialized Kaiburr System",
        "epilogueMessage": "Finishing Kaiburr System",
        "Plugins": [
            {
                "path": "plugins.shared.test.testPlugin"
            }
        ]
    }
    
    import yaml
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    print("\n[OK] Configuration saved to kaiburrCfg.yaml!")

def autostart_server(config):
    if not config.get("watchdog", {}).get("enabled", False):
        return
        
    try:
        import psutil
    except ImportError:
        print("[WARNING] psutil not installed, skipping autostart check.")
        return
        
    server_file = config.get("serverFileName", "mbiided.x86.exe")
    rcon_cfg = config.get("interfaces", {}).get("rcon", {})
    remotes = rcon_cfg.get("Remotes", [])
    
    if not remotes:
        return
        
    port = str(remotes[0].get("port", "29070"))
    log_file = remotes[0].get("logFilename", "server.log")
    
    print_header("Checking Server Status")
    
    is_running = False
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if server_file.lower() in proc.info['name'].lower():
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if f"net_port {port}" in cmdline or port in cmdline:
                    is_running = True
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
            continue
            
    if is_running:
        print(f"[OK] {server_file} is already running on port {port}.")
        return
        
    server_path = config.get("serverPath", "")
    full_path = os.path.join(server_path, server_file)
    
    if not os.path.exists(full_path):
        print(f"[ERROR] Autostart failed. Could not find server at {full_path}")
        return
        
    print(f"[*] Server is not running. Launching {server_file}...")
    
    args = [
        full_path,
        "--debug",
        "+set", "g_log", log_file,
        "+set", "g_logExplicit", "3",
        "+set", "g_logClientInfo", "1",
        "+set", "g_logSync", "4",
        "+set", "com_logChat", "2",
        "+set", "dedicated", "2",
        "+set", "fs_game", "MBII",
        "+exec", "server.cfg",
        "+set", "net_port", port
    ]
    
    if os.name == "nt":
        subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=server_path)
    else:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True, cwd=server_path)
        
    time.sleep(3)

def main():
    config_file = CONFIG_PATH
    if not os.path.exists(config_file):
        legacy_config = os.path.join(KAIBURR_ROOT, "kaiburrCfg.json")
        if os.path.exists(legacy_config):
            config_file = legacy_config
        else:
            setup_environment()
            run_setup_wizard()
            config_file = CONFIG_PATH
        
    import yaml
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
        
    autostart_server(config)
    
    print_header("Starting Kaiburr")
    try:
        subprocess.run([sys.executable, "kaiburr.py"] + sys.argv[1:], check=True, cwd=KAIBURR_ROOT)
    except KeyboardInterrupt:
        print("\n[OK] Exiting Kaiburr.")
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Kaiburr exited with error code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()
