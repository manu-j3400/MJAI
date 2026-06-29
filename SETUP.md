# MJAI Setup Guide

## Prerequisites

- macOS 13+ (tested on macOS 26.2 Tahoe)
- Python 3.12+
- Claude Code CLI installed (`which claude` should work)

## First-time setup

### 1. Clone and create venv

```bash
cd ~/workspace/MJAI
python3 -m venv .venv
source .venv/bin/activate
pip install rumps pynput openai-whisper sounddevice pyobjc watchdog pyinstaller pyobjc-framework-AVFoundation
```

### 2. Create action scripts

```bash
mkdir -p ~/.mjai/actions

cat > ~/.mjai/actions/open_app << 'EOF'
#!/usr/bin/env bash
open -a "$1"
EOF

cat > ~/.mjai/actions/notify << 'EOF'
#!/usr/bin/env bash
osascript -e "display notification \"$1\" with title \"MJAI\""
EOF

cat > ~/.mjai/actions/set_dnd << 'EOF'
#!/usr/bin/env bash
if [ "$1" = "on" ]; then
  shortcuts run "DND On" 2>/dev/null || true
else
  shortcuts run "DND Off" 2>/dev/null || true
fi
EOF

cat > ~/.mjai/actions/set_volume << 'EOF'
#!/usr/bin/env bash
osascript -e "set volume output volume $1"
EOF

chmod +x ~/.mjai/actions/*
```

> **DND scripts:** Create "DND On" and "DND Off" shortcuts in macOS Shortcuts.app using the Focus filter action.

### 3. Create LaunchAgent

```bash
cat > ~/Library/LaunchAgents/com.mjai.startup.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mjai.startup</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Applications/MJAI.app/Contents/MacOS/MJAI</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>NUMBA_DISABLE_JIT</key>
        <string>1</string>
    </dict>
</dict>
</plist>
EOF
```

### 4. Build

```bash
bash build.sh
```

### 5. Permissions

On first run, approve:
- **Microphone** — for voice recording
- **Accessibility** — for the global hotkey (pynput)

Both appear as system dialogs. If Microphone doesn't appear, go to System Settings → Privacy & Security → Microphone and add MJAI manually.

## Configuration

Edit `~/.mjai/config.yaml` — changes hot-reload without restarting MJAI.

## Hotkey

Default: `cmd+shift+space`

Hold to record, release to send to Claude.

## Logs

```bash
tail -f ~/.mjai/mjai.log
```
