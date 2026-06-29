"""
MJAI Windows Companion Service
Receives mode commands from macOS MJAI via WebSocket and executes Windows automations.

Run on Windows:
    pip install websockets
    python mjai_windows.py

Pair from macOS MJAI by setting WINDOWS_HOST in ~/.mjai/config.yaml:
    windows_host: "192.168.1.x:56789"
"""
import asyncio
import json
import logging
import subprocess
import sys

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)

PORT = 56789
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


MODE_ACTIONS = {
    "DEEP": [
        ("focus_assist", "on"),
        ("close_notifications", None),
        ("open_app", "Code"),
        ("open_app", "Windows Terminal"),
    ],
    "COMMS": [
        ("focus_assist", "off"),
        ("open_app", "Microsoft Teams"),
        ("open_app", "Discord"),
    ],
    "FLOW": [
        ("focus_assist", "on"),
        ("set_volume", "40"),
        ("open_app", "Spotify"),
    ],
    "ADMIN": [
        ("focus_assist", "off"),
        ("open_app", "Outlook"),
    ],
    "REST": [
        ("focus_assist", "on"),
        ("set_volume", "0"),
    ],
}


def run_action(action: str, arg):
    """Execute a Windows automation action."""
    if action == "open_app":
        try:
            subprocess.Popen(["start", arg], shell=True)
            log.info("opened: %s", arg)
        except Exception as e:
            log.warning("open_app failed: %s", e)

    elif action == "set_volume":
        # Uses nircmd (download from nirsoft.net/utils/nircmd.html)
        try:
            level = int(arg)
            vol = int(level * 655.35)  # 0-100 → 0-65535
            subprocess.run(["nircmd", "setsysvolume", str(vol)], check=False)
            log.info("volume: %s%%", level)
        except Exception as e:
            log.warning("set_volume failed: %s", e)

    elif action == "focus_assist":
        # Requires PowerShell with Focus Assist module
        state = "1" if arg == "on" else "0"
        try:
            subprocess.run([
                "powershell", "-Command",
                f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CloudStore\\Store\\DefaultAccount\\Current\\default$windows.data.notifications.quiethourssettings\\windows.data.notifications.quiethourssettings' -Name 'Data' -Value ([byte[]]($Data))"
            ], check=False, capture_output=True)
            log.info("focus_assist: %s", arg)
        except Exception as e:
            log.warning("focus_assist failed: %s", e)

    elif action == "close_notifications":
        try:
            subprocess.run(["powershell", "-Command",
                "Get-Process -Name 'ms-teams' -ErrorAction SilentlyContinue | Stop-Process -Force"], check=False)
        except Exception:
            pass


async def handle_client(websocket):
    log.info("macOS MJAI connected from %s", websocket.remote_address)
    async for message in websocket:
        try:
            cmd = json.loads(message)
            mode = cmd.get("mode")
            log.info("Received mode: %s", mode)

            actions = MODE_ACTIONS.get(mode, [])
            for action, arg in actions:
                run_action(action, arg)

            await websocket.send(json.dumps({"ok": True, "mode": mode}))
        except Exception as e:
            log.error("Error: %s", e)
            await websocket.send(json.dumps({"ok": False, "error": str(e)}))


async def main():
    log.info("MJAI Windows service listening on ws://0.0.0.0:%d", PORT)
    async with websockets.serve(handle_client, "0.0.0.0", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
