# MJAI Architecture

## System overview

```
┌─────────────────────────────────────────────────────────┐
│  macOS LaunchAgent (com.mjai.startup)                   │
│  MJAI.app [LSUIElement=true, no Dock icon]              │
│                                                         │
│  ┌──────────────┐     ┌──────────────┐                 │
│  │  menubar.py  │     │  hotkey.py   │                 │
│  │  rumps App   │◄────│  pynput      │                 │
│  │  main thread │     │  listener    │                 │
│  └──────┬───────┘     └──────────────┘                 │
│         │ 100ms poll timer                              │
│         ▼                                               │
│  ┌──────────────┐     ┌──────────────┐                 │
│  │  overlay.py  │     │  voice.py    │                 │
│  │  NSPanel     │     │  sounddevice │                 │
│  │  pill bar    │     │  + Whisper   │                 │
│  └──────────────┘     └──────┬───────┘                 │
│                              │ transcript               │
│                              ▼                          │
│  ┌──────────────────────────────────────────┐          │
│  │  daemon.py                               │          │
│  │  - _gather_context() → active app, time, │          │
│  │    open apps, git commits                │          │
│  │  - claude_suggest() → claude -p "..."    │          │
│  │  - trigger_mode() → run action scripts   │          │
│  └──────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────┘
```

## Threading model

```
pynput thread  →  trigger_queue.put("start"/"stop")
                          │
main thread (rumps.Timer 100ms poll)
  ├── picks up "start" → overlay.show("listening") + spawn record thread
  ├── picks up "stop"  → overlay.update("thinking") + rec_stop.set()
  └── picks up result  → overlay.update(text) + trigger mode

record thread  →  voice.record() → voice.transcribe() → result_queue.put()
claude thread  →  daemon.claude_suggest() → result_queue.put()
```

AppKit (NSPanel overlay) ONLY called from main thread. No exceptions.

## Claude integration

```python
prompt = f"""
{system_prompt}

--- USER CONTEXT ---
Current time: Saturday June 28, 2026  5:20 PM
Active mode: DEEP
Active app: Visual Studio Code
Open apps: Code, iTerm2, Arc, Slack, Spotify
Recent git commits:
  abc1234 fix overlay positioning
  def5678 add FLOW and ADMIN modes

Available modes: DEEP | COMMS | FLOW | ADMIN | REST

User said: let me grind on this pr
"""

# Claude returns:
# {"mode": "DEEP", "rationale": "already in it — stay locked"}
```

Claude runs via `claude -p "<prompt>"` subprocess with cwd=/tmp to avoid triggering Downloads folder TCC prompts.

## PyInstaller bundle

```
MJAI.app/
  Contents/
    Info.plist          ← CFBundleIdentifier: com.mjai.app, LSUIElement: true
    MacOS/MJAI          ← PyInstaller bootloader
    Frameworks/         ← Python runtime + all deps
    Resources/          ← whisper model weights, pynput resources
```

Key build flags:
- `--collect-all whisper` — includes model weights
- `--collect-all rumps` — includes ObjC resources
- `--collect-all AVFoundation` — for mic TCC permission dialog
- `--windowed` — no terminal window

## macOS 26.2 compatibility

macOS Tahoe enforces `CS_KILL` — kills any process that writes to unsigned executable memory pages. `openai-whisper` uses `numba` JIT compilation which hits this.

**Fix:** `NUMBA_DISABLE_JIT=1` in LaunchAgent `EnvironmentVariables`. This makes numba a Python no-op with no JIT, letting Whisper run in pure Python mode.

**Entitlements required:**
```xml
<key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
<key>com.apple.security.cs.disable-library-validation</key><true/>
<key>com.apple.security.device.audio-input</key><true/>
```

## Config hot-reload

`watchdog.observers.Observer` watches `~/.mjai/` for file changes. When `config.yaml` changes, `daemon.reload_config()` runs without restarting the app. New modes and prompts take effect immediately.

## TCC permissions

| Service | Bundle ID | Status |
|---------|-----------|--------|
| Microphone | com.mjai.app | Granted once per build (resets on code signature change) |
| Accessibility | com.mjai.app | Granted once, persists (pynput global hotkey) |

**Mic resets:** Each rebuild changes the code signature hash. TCC associates permissions with (bundle_id, code_signature). Different signature = new app = new permission request.

**Permanent fix (future):** Sign with Apple Developer ID certificate. The same certificate across builds means TCC won't reset.

## Windows companion (planned)

A lightweight Windows service (`mjai-windows/`) will:
1. Expose a local WebSocket on port 56789
2. Receive mode commands from MJAI macOS
3. Execute Windows automations (PowerShell, COM, Win32 API)
4. Allow unified voice → intent → cross-platform execution

```
macOS MJAI     →  "DEEP mode"  →  Windows MJAI service
                                  → close Teams notifications
                                  → set Windows Focus Assist ON
                                  → open VSCode on Windows
```
