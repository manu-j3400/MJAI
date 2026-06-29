#!/usr/bin/env bash
set -e

.venv/bin/pyinstaller --windowed \
  --collect-all rumps \
  --collect-all pynput \
  --collect-all whisper \
  --collect-all AVFoundation \
  --name MJAI \
  --noconfirm \
  menubar.py

PLIST="dist/MJAI.app/Contents/Info.plist"

/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'MJAI uses the microphone to hear your voice commands.'" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription 'MJAI uses the microphone to hear your voice commands.'" "$PLIST"

/usr/libexec/PlistBuddy -c "Add :NSAccessibilityUsageDescription string 'MJAI needs Accessibility to detect the hotkey.'" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :NSAccessibilityUsageDescription 'MJAI needs Accessibility to detect the hotkey.'" "$PLIST"

/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.mjai.app" "$PLIST"

# Background-only — no Dock icon
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"

# Re-sign with entitlements so macOS 26.2 doesn't reject the binary
codesign --force --deep --sign - --entitlements entitlements.plist dist/MJAI.app

echo "Built: dist/MJAI.app"

# Deploy: remove old bundle, copy fresh — keep existing signature (ditto preserves it)
launchctl stop com.mjai.startup 2>/dev/null || true
rm -rf /Applications/MJAI.app
ditto dist/MJAI.app /Applications/MJAI.app
launchctl start com.mjai.startup

echo "Deployed to /Applications/MJAI.app"
