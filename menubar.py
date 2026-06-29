"""MJAI menubar app — entry point. Run: .venv/bin/python menubar.py"""
import fcntl
import os
import queue
import sys
import threading
import time

# Single-instance lock — exit immediately if another instance is already running
_LOCK_PATH = os.path.expanduser("~/.mjai/.lock")
_lock_file = open(_LOCK_PATH, "w")
try:
    fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(0)  # Another instance holds the lock — quit silently

import rumps
from AppKit import NSColor

from daemon import Daemon
from hotkey import start_listener
from overlay import Overlay
from voice import record, transcribe

# States
IDLE = "idle"
LISTENING = "listening"
THINKING = "thinking"


class MJAIApp(rumps.App):
    def __init__(self):
        self.daemon = Daemon()
        self._trigger_queue: queue.Queue = queue.Queue()
        self._result_queue: queue.Queue = queue.Queue()
        self._state = IDLE
        self._rec_stop = threading.Event()
        self._overlay_hide_at: float | None = None

        mode_items = [
            rumps.MenuItem(name, callback=self._make_direct_switch(name))
            for name in self.daemon.config.modes
            if name != "BOOT"
        ]
        modes_menu = rumps.MenuItem("Modes")
        for item in mode_items:
            modes_menu.add(item)

        super().__init__(
            name="MJAI",
            title=self._mode_title(),
            menu=[modes_menu, "About", None, "Quit"],
        )

        self.overlay = Overlay()
        self.daemon.start_watcher()

        start_listener(
            self.daemon.config.hotkey,
            on_start=lambda: self._trigger_queue.put("start"),
            on_stop=lambda: self._trigger_queue.put("stop"),
        )

        self._poll_timer = rumps.Timer(self._poll, 0.1)
        self._poll_timer.start()

        # Request mic permission explicitly so macOS shows the dialog
        threading.Timer(1.0, self._request_mic_permission).start()
        # Auto-trigger BOOT on startup
        threading.Timer(3.0, self._auto_boot).start()

    # ── Permissions ───────────────────────────────────────────────────────────

    def _request_mic_permission(self):
        try:
            from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
            status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
            import logging; log = logging.getLogger(__name__)
            log.info("Mic TCC status: %s (3=authorized)", status)
            if status != 3:
                AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    AVMediaTypeAudio,
                    lambda granted: log.info("Mic permission granted: %s", granted),
                )
        except Exception as e:
            import logging; logging.getLogger(__name__).warning("Mic permission request failed: %s", e)

    # ── Title ─────────────────────────────────────────────────────────────────

    def _mode_title(self) -> str:
        m = self.daemon.current_mode
        return f"MJAI · {m}" if m else "MJAI"

    # ── Direct switch from menu ───────────────────────────────────────────────

    def _make_direct_switch(self, mode_name: str):
        def _switch(_):
            errors = self.daemon.trigger_mode(mode_name)
            self.title = self._mode_title()
            self.overlay.show(
                mode_name,
                color=NSColor.systemGreenColor(),
            )
            self._overlay_hide_at = time.time() + 5
            if errors:
                rumps.alert("MJAI", f"{len(errors)} action(s) failed. Check ~/.mjai/action.log")
        return _switch

    # ── Poll timer ────────────────────────────────────────────────────────────

    def _poll(self, _):
        # Trigger signals from pynput thread
        try:
            sig = self._trigger_queue.get_nowait()
            if sig == "start" and self._state == IDLE:
                self._state = LISTENING
                self._rec_stop.clear()
                self.overlay.show(
                    "MJAI  ·  listening",
                    color=NSColor.systemOrangeColor(),
                )
                threading.Thread(target=self._record_audio, daemon=True).start()

            elif sig == "stop" and self._state == LISTENING:
                self._state = THINKING
                self._rec_stop.set()
                self.overlay.update("MJAI  ·  thinking")
        except queue.Empty:
            pass

        # Results from background threads
        try:
            item = self._result_queue.get_nowait()
            kind = item[0]

            if kind == "title_update":
                self.title = self._mode_title()

            elif kind == "transcript":
                text = item[1]
                if text:
                    threading.Thread(
                        target=self._call_claude, args=(text,), daemon=True
                    ).start()
                else:
                    self.overlay.update(
                        "MJAI  ·  didn't catch that",
                        color=NSColor.systemGrayColor(),
                    )
                    self._overlay_hide_at = time.time() + 5
                    self._state = IDLE

            elif kind == "mode":
                _, mode, rationale = item
                self._apply(mode, rationale)

        except queue.Empty:
            pass

        # Auto-hide overlay
        if self._overlay_hide_at and time.time() >= self._overlay_hide_at:
            self.overlay.hide()
            self._overlay_hide_at = None

    # ── Background workers ────────────────────────────────────────────────────

    def _auto_boot(self):
        self.daemon.trigger_mode("BOOT")
        self._result_queue.put(("title_update", None))

    def _record_audio(self):
        audio = record(self._rec_stop)
        transcript = transcribe(audio)
        self._result_queue.put(("transcript", transcript))

    def _call_claude(self, intent: str):
        mode, rationale = self.daemon.claude_suggest(intent)
        self._result_queue.put(("mode", mode, rationale))

    # ── Apply mode ────────────────────────────────────────────────────────────

    def _apply(self, mode: str | None, rationale: str):
        if mode is None:
            self.overlay.update(
                f"MJAI  ·  {rationale or 'no match'}",
                color=NSColor.systemGrayColor(),
            )
            self._overlay_hide_at = time.time() + 3
            self._state = IDLE
            return

        errors = self.daemon.trigger_mode(mode)
        self.title = self._mode_title()

        display = f"{mode}  —  {rationale}" if rationale else mode
        self.overlay.update(display, color=NSColor.systemGreenColor())
        self._overlay_hide_at = time.time() + 6
        self._state = IDLE

        if errors:
            rumps.alert("MJAI", f"{len(errors)} action(s) failed. Check ~/.mjai/action.log")

    # ── Menu items ────────────────────────────────────────────────────────────

    @rumps.clicked("About")
    def about(self, _):
        m = self.daemon.current_mode or "—"
        rumps.alert("MJAI", f"Current mode: {m}\nHotkey: {self.daemon.config.hotkey}")

    @rumps.clicked("Quit")
    def quit_app(self, _):
        self.daemon.stop_watcher()
        rumps.quit_application()


if __name__ == "__main__":
    MJAIApp().run()
