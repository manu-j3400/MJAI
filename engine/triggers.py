"""
UnamOS Background Trigger System
Async watchers that fire workflows based on system events:
  - App watcher: frontmost app changes
  - Battery watcher: battery level thresholds
  - Network watcher: WiFi SSID changes
  - iMessage watcher: new iMessages to self containing UnamOS commands
"""
import asyncio
import logging
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

IMESSAGE_DB = Path.home() / "Library/Messages/chat.db"
UNAMOSVOS_PREFIX = "!"   # messages to self starting with ! trigger workflows


class AppWatcher:
    """Fires workflows when specific apps become frontmost."""

    def __init__(self, engine, app_triggers: dict[str, str]):
        # app_triggers: {"Visual Studio Code": "DEEP", "Slack": "COMMS", ...}
        self._engine = engine
        self._app_triggers = {k.lower(): v for k, v in app_triggers.items()}
        self._last_app: Optional[str] = None

    async def run(self):
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
                app = stdout.decode().strip()

                if app and app != self._last_app:
                    self._last_app = app
                    await self._check(app)
            except Exception:
                pass
            await asyncio.sleep(4)

    async def _check(self, app: str):
        key = app.lower()
        for pattern, target in self._app_triggers.items():
            if pattern in key:
                # Check if target is a mode name or workflow name
                wf = self._engine.find_by_voice(target)
                if wf:
                    log.info("App trigger: %s → workflow %s", app, wf.get("name"))
                    asyncio.create_task(self._engine.execute(wf, "app_event", {"app": app}))
                else:
                    # It's a mode name — write pending mode
                    pending = Path.home() / ".mjai/.pending_mode"
                    pending.write_text(target)
                    log.info("App trigger: %s → mode %s", app, target)
                break


class BatteryWatcher:
    """Fires workflows when battery drops below configured thresholds."""

    def __init__(self, engine, thresholds: list[dict]):
        # thresholds: [{"below": 20, "workflow": "low_battery"}, ...]
        self._engine = engine
        self._thresholds = thresholds
        self._fired: set[int] = set()
        self._last_charging: Optional[bool] = None

    async def run(self):
        while True:
            try:
                info = await self._get_battery()
                if info:
                    level, charging = info
                    # Reset fired thresholds when plugged in
                    if charging and not self._last_charging:
                        self._fired.clear()
                    self._last_charging = charging
                    if not charging:
                        await self._check(level)
            except Exception as e:
                log.debug("Battery watcher error: %s", e)
            await asyncio.sleep(60)

    async def _get_battery(self) -> Optional[tuple[int, bool]]:
        proc = await asyncio.create_subprocess_exec(
            "pmset", "-g", "batt",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        text = stdout.decode()
        import re
        m = re.search(r'(\d+)%', text)
        if not m:
            return None
        level = int(m.group(1))
        charging = "AC Power" in text or "charging" in text.lower()
        return level, charging

    async def _check(self, level: int):
        for t in self._thresholds:
            threshold = t.get("below", 20)
            if level <= threshold and threshold not in self._fired:
                self._fired.add(threshold)
                wf_name = t.get("workflow")
                wf = self._engine._workflows.get(wf_name) if wf_name else None
                if wf:
                    log.info("Battery trigger: %d%% ≤ %d → %s", level, threshold, wf_name)
                    asyncio.create_task(self._engine.execute(
                        wf, "battery", {"battery_level": level}
                    ))
                elif wf_name:
                    log.warning("Battery trigger: workflow %s not found", wf_name)


class NetworkWatcher:
    """Fires workflows when WiFi SSID changes."""

    def __init__(self, engine, network_triggers: list[dict]):
        # network_triggers: [{"ssid": "Home", "workflow": "home_mode"}, ...]
        self._engine = engine
        self._triggers = network_triggers
        self._last_ssid: Optional[str] = None

    async def run(self):
        while True:
            try:
                ssid = await self._get_ssid()
                if ssid != self._last_ssid:
                    prev = self._last_ssid
                    self._last_ssid = ssid
                    if ssid:
                        await self._check(ssid)
                        log.info("Network changed: %s → %s", prev, ssid)
            except Exception as e:
                log.debug("Network watcher error: %s", e)
            await asyncio.sleep(30)

    async def _get_ssid(self) -> Optional[str]:
        # Try airport utility first
        airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        if Path(airport).exists():
            proc = await asyncio.create_subprocess_exec(
                airport, "-I",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            for line in stdout.decode().splitlines():
                if " SSID:" in line:
                    return line.split("SSID:")[-1].strip()
        # Fallback: networksetup
        proc = await asyncio.create_subprocess_exec(
            "networksetup", "-getairportnetwork", "en0",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        text = stdout.decode().strip()
        if "Current Wi-Fi Network:" in text:
            return text.split("Current Wi-Fi Network:")[-1].strip()
        return None

    async def _check(self, ssid: str):
        for t in self._triggers:
            if t.get("ssid", "").lower() in ssid.lower():
                wf_name = t.get("workflow")
                mode = t.get("mode")
                if wf_name:
                    wf = self._engine._workflows.get(wf_name)
                    if wf:
                        log.info("Network trigger: SSID %s → workflow %s", ssid, wf_name)
                        asyncio.create_task(self._engine.execute(
                            wf, "network", {"ssid": ssid}
                        ))
                elif mode:
                    pending = Path.home() / ".mjai/.pending_mode"
                    pending.write_text(mode)
                    log.info("Network trigger: SSID %s → mode %s", ssid, mode)
                break


class iMessageWatcher:
    """
    Watches ~/Library/Messages/chat.db for new messages to self
    that start with ! — interprets them as UnamOS voice commands.
    Example: send yourself "!deep work" on iMessage → triggers DEEP mode.
    Requires Full Disk Access for MJAI.app in System Settings.
    """

    def __init__(self, engine):
        self._engine = engine
        self._last_rowid: Optional[int] = None

    async def run(self):
        if not IMESSAGE_DB.exists():
            log.info("iMessage DB not found — iMessage trigger disabled.")
            return

        # Seed last rowid so we don't replay old messages on startup
        self._last_rowid = await self._get_max_rowid()
        log.info("iMessage watcher started (last rowid: %s)", self._last_rowid)

        while True:
            try:
                await self._poll()
            except Exception as e:
                log.debug("iMessage watcher error: %s", e)
            await asyncio.sleep(5)

    async def _get_max_rowid(self) -> int:
        try:
            conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
            row = conn.execute("SELECT MAX(ROWID) FROM message").fetchone()
            conn.close()
            return row[0] or 0
        except Exception:
            return 0

    async def _poll(self):
        loop = asyncio.get_event_loop()
        messages = await loop.run_in_executor(None, self._read_new_messages)
        for text in messages:
            if text.startswith(UNAMOSVOS_PREFIX):
                command = text[len(UNAMOSVOS_PREFIX):].strip()
                log.info("iMessage command: %s", command)
                asyncio.create_task(self._handle(command))

    def _read_new_messages(self) -> list[str]:
        try:
            conn = sqlite3.connect(f"file:{IMESSAGE_DB}?mode=ro", uri=True)
            rows = conn.execute(
                """
                SELECT m.ROWID, m.text
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.ROWID > ?
                  AND m.is_from_me = 0
                  AND m.text IS NOT NULL
                  AND m.text != ''
                ORDER BY m.ROWID ASC
                LIMIT 20
                """,
                (self._last_rowid or 0,),
            ).fetchall()
            conn.close()
            if rows:
                self._last_rowid = rows[-1][0]
            return [r[1] for r in rows if r[1]]
        except Exception as e:
            log.debug("iMessage read error: %s", e)
            return []

    async def _handle(self, command: str):
        # Try workflow match first, then treat as voice command to Claude
        wf = self._engine.find_by_voice(command)
        if wf:
            await self._engine.execute(wf, "imessage", {"voice_input": command})
            return

        # Fall back: pending mode signal via file (daemon picks it up)
        # Import here to avoid circular
        from daemon import Daemon
        _d = Daemon.__new__(Daemon)
        _d.__dict__ = {}
        # Just write a pending voice command for the overlay to pick up
        cmd_path = Path.home() / ".mjai" / ".pending_voice"
        cmd_path.write_text(command)
        log.info("iMessage command queued as pending voice: %s", command)


class FileWatcher:
    """Fires workflows when files in watched directories change."""

    def __init__(self, engine, file_triggers: list[dict]):
        # file_triggers: [{"path": "~/Downloads", "pattern": "*.pdf", "workflow": "process_pdf"}]
        self._engine = engine
        self._triggers = file_triggers
        self._mtimes: dict[str, float] = {}

    async def run(self):
        if not self._triggers:
            return
        log.info("File watcher started for %d path(s)", len(self._triggers))
        while True:
            try:
                await self._scan()
            except Exception as e:
                log.debug("File watcher error: %s", e)
            await asyncio.sleep(10)

    async def _scan(self):
        import glob
        for t in self._triggers:
            path = os.path.expanduser(t.get("path", ""))
            pattern = t.get("pattern", "*")
            wf_name = t.get("workflow")
            full_pattern = os.path.join(path, pattern)
            for fp in glob.glob(full_pattern):
                mtime = os.path.getmtime(fp)
                if fp not in self._mtimes:
                    self._mtimes[fp] = mtime
                elif mtime > self._mtimes[fp]:
                    self._mtimes[fp] = mtime
                    wf = self._engine._workflows.get(wf_name)
                    if wf:
                        log.info("File trigger: %s changed → %s", fp, wf_name)
                        asyncio.create_task(self._engine.execute(
                            wf, "file_change", {"changed_file": fp, "file_path": fp}
                        ))
