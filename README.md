# MJAI — Personal Automation OS

A macOS menubar app that listens to your voice, understands your intent via Claude AI, and executes automations (mode switches, app launchers, DND, notifications).

## How it works

```
Hold cmd+shift+space  →  Whisper transcribes locally
     ↓
Claude AI reads your intent + current context (active app, time, git status)
     ↓
Picks a mode or answers conversationally
     ↓
Floating overlay bar shows result
     ↓
Mode scripts execute (open apps, set DND, send notifications)
```

## Architecture

| File | Purpose |
|------|---------|
| `menubar.py` | Entry point — rumps app, hotkey bridge, poll timer |
| `overlay.py` | Floating NSPanel pill bar (shows above all windows) |
| `daemon.py` | Core logic — Claude integration, action runner, context gathering |
| `voice.py` | Sounddevice recording + Whisper tiny (local, offline transcription) |
| `hotkey.py` | pynput push-to-talk listener (holds key = recording) |
| `config.py` | YAML config parser — modes, actions, Claude system prompt |
| `build.sh` | PyInstaller build + deploy to /Applications/MJAI.app |
| `entitlements.plist` | macOS entitlements (mic, unsigned memory for Whisper) |

## Config

Lives at `~/.mjai/config.yaml`. Hot-reloads on change (no restart needed).

### Modes

Each mode has a name, description, and list of actions. Actions are shell scripts in `~/.mjai/actions/`.

```yaml
modes:
  DEEP:
    description: "Deep focus — heads-down work"
    actions:
      - script: set_dnd
        args: ["on"]
      - script: open_app
        args: ["Visual Studio Code"]
```

### Claude system prompt

The system prompt in `config.yaml` instructs Claude how to respond. It receives:
- User's spoken intent
- Current time and day
- Active frontmost app
- List of open apps
- Recent git commits

Claude returns JSON: `{"mode": "DEEP", "rationale": "locking in"}` or `{"mode": null, "rationale": "It's 2pm on a Tuesday, great time to focus!"}` for conversational replies.

## Action scripts

All scripts live in `~/.mjai/actions/` and are called with `args` from the config.

| Script | What it does |
|--------|-------------|
| `open_app` | `open -a <AppName>` |
| `set_dnd` | `shortcuts run "DND On/Off"` |
| `notify` | `osascript` notification |
| `close_app` | `osascript` to quit an app |
| `set_volume` | `osascript` to set system volume |

## Dependencies

- Python 3.12+ with `.venv`
- `rumps` — macOS menubar framework
- `pynput` — global hotkey listener (needs Accessibility permission)
- `sounddevice` — mic recording
- `openai-whisper` — local transcription (tiny model, offline)
- `pyobjc` — NSPanel, NSColor, NSScreen etc
- `watchdog` — hot-reload config.yaml
- `pyinstaller` — bundle to .app

## Build

```bash
bash build.sh
```

Bundles to `/Applications/MJAI.app`, re-signs for macOS 26.2, and restarts via LaunchAgent.

## LaunchAgent

Auto-starts on login via `~/Library/LaunchAgents/com.mjai.startup.plist`.

Key env vars set in the plist:
- `NUMBA_DISABLE_JIT=1` — prevents Whisper's numba JIT from writing unsigned executable memory (macOS 26.2 CS_KILL enforcement)

## Permissions required

| Permission | Why |
|-----------|-----|
| Microphone | Recording voice via sounddevice |
| Accessibility | pynput global hotkey detection |

Mic permission resets after each rebuild (code signature changes → TCC treats it as a new app). Click Allow once after each build session.

## Windows desktop integration (planned)

Future: a lightweight companion service on Windows that exposes the same mode API, allowing MJAI to control apps and automations across both machines from a single voice command.
