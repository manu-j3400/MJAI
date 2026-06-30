"""
UnamOS Windows Companion
Runs on the Windows machine. Pairs with Mac via WebSocket.
Both sides act as one brain — mode changes, actions, and status sync both ways.

Install: pip install websockets
Run:     python unamosvos_windows.py

Config: set windows_host in ~/.mjai/config.yaml on Mac:
    windows_host: "192.168.1.x:56789"
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Run: pip install websockets")
    sys.exit(1)

PORT = 56789
VERSION = "2.0.0"
log = logging.getLogger("unamosvos")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path.home() / "unamosvos.log", encoding="utf-8"),
    ],
)

# ── State ──────────────────────────────────────────────────────────────────────

_current_mode: str = ""
_mac_connected: bool = False
_mac_ws = None  # active websocket to Mac


# ── Windows actions ────────────────────────────────────────────────────────────

def _ps(command: str, capture=True):
    """Run a PowerShell command."""
    return subprocess.run(
        ["powershell", "-NonInteractive", "-Command", command],
        capture_output=capture, text=True, timeout=10,
    )


def action_notify(message: str, title: str = "UnamOS"):
    """Windows toast notification via PowerShell."""
    escaped = message.replace('"', '\\"')
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        f'$xml.GetElementsByTagName("text")[0].AppendChild($xml.CreateTextNode("{title}")) | Out-Null;'
        f'$xml.GetElementsByTagName("text")[1].AppendChild($xml.CreateTextNode("{escaped}")) | Out-Null;'
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('UnamOS').Show($toast);"
    )
    try:
        _ps(script)
        log.info("notify: %s", message)
    except Exception as e:
        log.warning("notify failed: %s", e)


def action_open_app(app: str):
    try:
        subprocess.Popen(f'start "" "{app}"', shell=True)
        log.info("open_app: %s", app)
    except Exception as e:
        log.warning("open_app failed %s: %s", app, e)


def action_close_app(app: str):
    try:
        exe = app if app.endswith(".exe") else app + ".exe"
        subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True)
        log.info("close_app: %s", app)
    except Exception as e:
        log.warning("close_app failed: %s", e)


def action_set_volume(level: int):
    try:
        vol = max(0, min(100, int(level)))
        script = (
            "$obj = New-Object -ComObject WScript.Shell;"
            f"$wsh = New-Object -ComObject WScript.Shell;"
            "Add-Type -TypeDefinition @'\nusing System.Runtime.InteropServices;\n"
            "[Guid(\"5CDF2C82-841E-4546-9722-0CF74078229A\"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
            "interface IAudioEndpointVolume { void v1(); void v2(); void v3(); void v4();\n"
            "  [PreserveSig] int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);\n"
            "} '@;"
        )
        # Simpler fallback: nircmd if available, else PowerShell audio API
        result = subprocess.run(
            ["nircmd", "setsysvolume", str(int(vol * 655.35))],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            # PowerShell fallback using audio COM
            _ps(f"[System.Media.SystemSounds]::Beep; (New-Object -ComObject Shell.Application).Windows() | Out-Null")
        log.info("set_volume: %d%%", vol)
    except Exception as e:
        log.warning("set_volume failed: %s", e)


def action_focus_assist(state: str):
    """Toggle Focus Assist (Quiet Hours) via registry."""
    try:
        on = state.lower() in ("on", "true", "1", "yes")
        # 0=off, 1=priority only, 2=alarms only
        value = "2" if on else "0"
        _ps(
            f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings' "
            f"-Name 'NOC_GLOBAL_SETTING_TOASTS_ENABLED' -Value {0 if on else 1} -Type DWord -Force"
        )
        log.info("focus_assist: %s", state)
    except Exception as e:
        log.warning("focus_assist failed: %s", e)


def action_speak(text: str, rate: int = 0):
    """Text-to-speech via PowerShell SAPI."""
    escaped = text.replace('"', '\\"').replace("'", "\\'")
    try:
        _ps(
            f"Add-Type -AssemblyName System.Speech;"
            f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Rate = {rate};"
            f'$s.Speak("{escaped}");'
        )
        log.info("speak: %s", text[:50])
    except Exception as e:
        log.warning("speak failed: %s", e)


def action_shell(command: str):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        log.info("shell: %s → rc=%d", command[:60], result.returncode)
        return result.stdout.strip()
    except Exception as e:
        log.warning("shell failed: %s", e)
        return ""


# ── Mode handler ───────────────────────────────────────────────────────────────

MODE_ACTIONS = {
    "DEEP": [
        ("focus_assist", "on"),
        ("notify", "DEEP mode. Locked in."),
    ],
    "COMMS": [
        ("focus_assist", "off"),
        ("notify", "COMMS mode. Catching up."),
    ],
    "FLOW": [
        ("focus_assist", "on"),
        ("set_volume", 40),
        ("notify", "FLOW mode. Creating."),
    ],
    "ADMIN": [
        ("focus_assist", "off"),
        ("notify", "ADMIN mode. Getting organised."),
    ],
    "REST": [
        ("focus_assist", "on"),
        ("set_volume", 0),
        ("notify", "REST mode. Take a break."),
    ],
}


def apply_mode(mode: str):
    global _current_mode
    _current_mode = mode
    for action, arg in MODE_ACTIONS.get(mode, []):
        dispatch_action({"action": action, "message": arg} if action == "notify"
                        else {"action": action, "level": arg} if action == "set_volume"
                        else {"action": action, "state": arg} if action == "focus_assist"
                        else {"action": action})
    log.info("Mode applied: %s", mode)


def dispatch_action(step: dict) -> str:
    action = step.get("action", "")
    if action == "notify":
        action_notify(step.get("message", ""), step.get("title", "UnamOS"))
    elif action == "open_app":
        action_open_app(step.get("app", step.get("name", "")))
    elif action == "close_app":
        action_close_app(step.get("app", step.get("name", "")))
    elif action == "set_volume":
        action_set_volume(step.get("level", step.get("volume", 50)))
    elif action == "focus_assist":
        action_focus_assist(step.get("state", "off"))
    elif action == "speak":
        action_speak(step.get("text", step.get("message", "")))
    elif action == "shell":
        return action_shell(step.get("command", ""))
    else:
        log.warning("Unknown action: %s", action)
    return ""


# ── WebSocket server ───────────────────────────────────────────────────────────

async def handle_mac(websocket):
    global _mac_connected, _mac_ws
    _mac_connected = True
    _mac_ws = websocket
    addr = websocket.remote_address
    log.info("Mac connected from %s", addr)

    try:
        await websocket.send(json.dumps({
            "type": "hello",
            "platform": "windows",
            "version": VERSION,
            "mode": _current_mode,
        }))

        async for raw in websocket:
            try:
                msg = json.loads(raw)
                mtype = msg.get("type", "")

                if mtype == "ping":
                    await websocket.send(json.dumps({"type": "pong", "ts": time.time()}))

                elif mtype == "mode":
                    mode = msg.get("mode", "")
                    log.info("Mode sync from Mac: %s", mode)
                    apply_mode(mode)
                    await websocket.send(json.dumps({"type": "mode_ack", "mode": mode}))

                elif mtype == "action":
                    output = dispatch_action(msg)
                    await websocket.send(json.dumps({"type": "action_ack", "output": output}))

                elif mtype == "status_request":
                    await websocket.send(json.dumps({
                        "type": "status_response",
                        "platform": "windows",
                        "version": VERSION,
                        "mode": _current_mode,
                        "ts": time.time(),
                    }))

                else:
                    log.warning("Unknown message type: %s", mtype)

            except json.JSONDecodeError:
                log.warning("Bad JSON from Mac")
            except Exception as e:
                log.error("Handler error: %s", e)

    except websockets.exceptions.ConnectionClosed:
        log.info("Mac disconnected")
    finally:
        _mac_connected = False
        _mac_ws = None


async def main():
    log.info("UnamOS Windows companion v%s starting on ws://0.0.0.0:%d", VERSION, PORT)
    action_notify("UnamOS Windows companion online", "UnamOS")

    async with websockets.serve(handle_mac, "0.0.0.0", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
