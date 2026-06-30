# UnamOS Architecture

UnamOS is a voice-first personal automation OS running natively on Mac and Windows.
Zero cloud cost. No subscriptions. Your machine, your AI, your workflows.

## Stack

```
Voice (hotkey + mic + whisper)
    │
    ▼
WorkflowEngine ──→ find_by_voice() ──→ run workflow steps
    │                                       │
    │ (no match)                           ├─ ollama action  → Ollama :11434 (local, free)
    ▼                                      ├─ claude action  → claude CLI subprocess
Claude daemon ──→ claude_suggest()         ├─ http action   → any REST API
    │                                      ├─ shell action  → ~/.mjai/actions/* scripts
    ▼                                      ├─ notify action → macOS notification
Mode switch                                ├─ set_mode      → pending mode file
    │                                      ├─ open_app      → open -a
    ▼                                      └─ set_volume    → osascript
FastAPI server :7700 (dashboard + webhooks)
    │
MongoDB :27017 (run history, future: memory store)
```

## Local Infrastructure (already on this machine)

| Service | Port | Role |
|---------|------|------|
| Ollama | :11434 | Local LLM — dolphin3:8b (131k ctx), kimi-k2.5 (262k, vision), mistral:7b |
| MongoDB | :27017 | Workflow run history, persistent state |
| UnamOS Engine | :7700 | Dashboard + webhook trigger API |
| OpenClaw | :18789 | Optional: multi-agent gateway to Ollama |

## Threading Model

```
Main thread (AppKit/rumps)
  └─ rumps.Timer @ 100ms  ←── reads trigger_queue, result_queue, .pending_mode file
pynput thread              ──→ trigger_queue (start/stop)
Audio/Whisper thread       ──→ result_queue (transcript)
Claude thread              ──→ result_queue (mode, rationale)
Engine thread (asyncio)
  ├─ uvicorn FastAPI server :7700
  ├─ workflow step execution (async)
  └─ MongoDB writes (motor async)
```

## Workflow System

Workflows live in `~/.mjai/workflows/*.yaml`. Hot-reloaded on change.

### Trigger types

| Trigger | Example |
|---------|---------|
| `voice` | Say phrase → engine catches it before mode matching |
| `cron` | `schedule: "0 8 * * 1-5"` → fires at 8am weekdays |
| `webhook` | POST `/webhook/my-path` from any app/script |
| `api` | POST `/api/run/<workflow-name>` |

### Action types

| Action | What it does |
|--------|-------------|
| `ollama` | Local LLM inference (dolphin3, kimi-k2.5, mistral) |
| `claude` | Claude CLI — complex reasoning, code generation |
| `shell` | Run any script in `~/.mjai/actions/` |
| `http` | Call any REST API |
| `notify` | macOS notification |
| `set_mode` | Switch MJAI mode from within a workflow |
| `open_app` | Launch any Mac app |
| `set_volume` | Set output volume |
| `wait` | Pause N seconds |

### Workflow YAML schema

```yaml
name: my_workflow
description: "What it does"
triggers:
  - type: voice
    phrase: "trigger phrase"
  - type: cron
    schedule: "0 9 * * 1-5"
  - type: webhook
    path: /my-hook
steps:
  - id: step1
    action: ollama
    model: dolphin3:8b
    prompt: "Do something with {{voice_input}}"
    output_key: result

  - id: step2
    action: notify
    message: "{{result}}"

  # Conditional step
  - condition: "{{some_value}} == 'yes'"
    then:
      - action: set_mode
        mode: DEEP
    else:
      - action: notify
        message: "Not today"

  # Parallel steps
  - parallel:
      - action: open_app
        app: "Visual Studio Code"
      - action: set_volume
        level: 40
```

### Template variables

All string fields support `{{variable}}` substitution. Built-in variables:
- `{{date}}` — "Saturday June 28 2026"
- `{{time}}` — "5:30 PM"
- `{{timestamp}}` — ISO8601
- `{{workflow_name}}`, `{{run_id}}`, `{{trigger}}`
- `{{voice_input}}` — the raw voice transcript (voice triggers)
- Any output from a previous step by its `output_key`

## Modes (config-driven)

Modes live in `~/.mjai/config.yaml`. Workflows can switch modes via `set_mode` action.
Claude AI picks the best mode when voice doesn't match a workflow.

| Mode | Purpose |
|------|---------|
| DEEP | Focus work — DND on, VS Code + iTerm2, volume 30 |
| COMMS | Communication — DND off, Slack + Discord + WhatsApp |
| FLOW | Creative — DND on, Spotify, volume 50 |
| ADMIN | Admin work — DND off, Arc browser |
| REST | Break — DND on, volume 0 |

## Windows Companion

`windows/mjai_windows.py` — WebSocket service on port 56789.
Mac sends mode changes; Windows side applies equivalent automations.
Configure `windows_host` in `~/.mjai/config.yaml`.

## macOS Specifics

- `NUMBA_DISABLE_JIT=1` — prevents JIT hitting macOS 26.2 CS_KILL enforcement
- `CLAUDE_DISABLE_DESKTOP=1` + `cwd=/tmp` — stops Downloads TCC prompts from claude CLI
- `LSUIElement=true` — no Dock icon, menubar only
- Bundle ID `com.mjai.app` — stable identity for TCC permissions
- `entitlements.plist` — audio input entitlement for mic access
- `codesign --deep --sign -` — single sign during build (second sign on deploy resets TCC)

## Dashboard

Open `http://127.0.0.1:7700` in any browser while MJAI.app is running.
Shows: service status (Ollama, MongoDB), workflow list with run buttons, recent run history.

## File Layout

```
~/.mjai/
  config.yaml          — modes + hotkey + system prompt
  workflows/           — your workflow YAML files (hot-reloaded)
  actions/             — executable shell scripts
  .state.json          — current mode + start time
  mjai.log             — all logs
  .pending_mode        — set_mode action writes here; poll loop reads it

/Applications/MJAI.app — the bundle (LaunchAgent auto-starts it)

~/Library/LaunchAgents/com.mjai.startup.plist — KeepAlive LaunchAgent
```
