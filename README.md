# UnamOS

> Your personal automation OS. n8n + AI agent power, zero cloud cost, runs on your own Mac and Windows machines.

## What it does

- **Voice-first** — press hotkey, speak, let AI pick the right workflow or mode
- **Workflow engine** — multi-step automation chains with local AI, HTTP calls, shell scripts, conditionals
- **Local AI** — Ollama (dolphin3:8b, kimi-k2.5, mistral) for free inference in workflows  
- **Claude integration** — uses your Claude subscription for mode selection and complex reasoning
- **Web dashboard** — `http://localhost:7700` for workflow management and run history
- **Mode switching** — DEEP/COMMS/FLOW/ADMIN/REST modes that configure your environment
- **Windows companion** — WebSocket bridge for cross-machine automation

## Quick start

```bash
# Press your hotkey (default: cmd+shift+space)
# Speak any of these:
"good morning"         → morning workflow: AI brief + DEEP mode
"what should I work on" → checks git log + asks Claude for focus suggestion
"research [topic]"     → asks local AI, sends notification
"deep work"            → switches to DEEP mode (VS Code + iTerm2, DND on)
"let's communicate"    → switches to COMMS mode (Slack, DND off)
```

## Dashboard

Open http://localhost:7700 while MJAI.app is running.

## Adding workflows

Drop a YAML file in `~/.mjai/workflows/` — no restart needed, hot-reloaded:

```yaml
name: my_workflow
description: "Do something useful"
triggers:
  - type: voice
    phrase: "trigger phrase"
  - type: cron
    schedule: "0 9 * * 1-5"  # 9am weekdays
steps:
  - id: get_brief
    action: ollama
    model: dolphin3:8b
    prompt: "Give me a quick brief about {{voice_input}}. 3 sentences max."
    output_key: brief
  
  - id: notify
    action: notify
    message: "{{brief}}"
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full schema, action types, and template variables.

## Project layout

```
engine/          — workflow engine (actions.py, workflow.py, server.py)
dashboard/       — web UI (static HTML, served at :7700)
windows/         — Windows companion WebSocket service
workflows/       — example workflow templates
~/.mjai/
  config.yaml    — modes + hotkey
  workflows/     — your personal workflows (live here, hot-reloaded)
  actions/       — shell scripts used by shell action
```

## Stack

Python + rumps (Mac menubar) · PyObjC (overlay) · pynput (hotkey) · whisper (local STT) · Ollama (local LLM) · FastAPI + uvicorn (web server) · MongoDB (run history) · PyInstaller (bundle)

## Docs

- [SETUP.md](SETUP.md) — first-time setup
- [MODES.md](MODES.md) — mode configuration 
- [ARCHITECTURE.md](ARCHITECTURE.md) — full technical architecture
