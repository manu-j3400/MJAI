"""MJAI / UnamOS menubar app — entry point. Run: .venv/bin/python menubar.py"""
import asyncio
import fcntl
import logging
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
from pathlib import Path
log = logging.getLogger(__name__)
from AppKit import NSColor

from daemon import Daemon
from hotkey import start_listener
from overlay import Overlay
from voice import record, transcribe
from engine.workflow import WorkflowEngine
from engine.server import app as _server_app, set_engine as _set_server_engine, set_config as _set_server_config

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

        # Async engine — runs in a background thread with its own event loop
        self._async_loop = asyncio.new_event_loop()
        self._engine = WorkflowEngine()
        _set_server_engine(self._engine)
        _set_server_config(self.daemon.config)
        self._engine_thread = threading.Thread(
            target=self._start_engine_loop, daemon=True, name="unamosvengine"
        )
        self._engine_thread.start()

        mode_items = [
            rumps.MenuItem(name, callback=self._make_direct_switch(name))
            for name in self.daemon.config.modes
            if name != "BOOT"
        ]
        modes_menu = rumps.MenuItem("Modes")
        for item in mode_items:
            modes_menu.add(item)

        # Dynamic status items (updated every 10s by _refresh_menu)
        self._status_item  = rumps.MenuItem("", callback=None)
        self._last_run_item = rumps.MenuItem("", callback=None)

        # Workflow quick-launch submenu
        self._workflows_menu = rumps.MenuItem("Workflows")

        super().__init__(
            name="UnamOS",
            title=self._mode_title(),
            menu=[
                self._status_item,
                None,
                self._workflows_menu,
                None,
                modes_menu,
                None,
                self._last_run_item,
                None,
                "About",
                None,
                "Quit",
            ],
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
        self._menu_refresh_timer = rumps.Timer(self._refresh_menu, 10)
        self._menu_refresh_timer.start()

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
        return f"UnamOS · {m}" if m else "UnamOS"

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
                rumps.alert("UnamOS", f"{len(errors)} action(s) failed. Check ~/.mjai/action.log")
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
                    "UnamOS  ·  listening",
                    color=NSColor.systemOrangeColor(),
                )
                threading.Thread(target=self._record_audio, daemon=True).start()

            elif sig == "stop" and self._state == LISTENING:
                self._state = THINKING
                self._rec_stop.set()
                self.overlay.update("UnamOS  ·  thinking")
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
                        "UnamOS  ·  didn't catch that",
                        color=NSColor.systemGrayColor(),
                    )
                    self._overlay_hide_at = time.time() + 5
                    self._state = IDLE

            elif kind == "mode":
                _, mode, rationale = item
                self._apply(mode, rationale)

            elif kind == "workflow_running":
                _, wf_name = item
                self.overlay.update(f"Running  ·  {wf_name}")
                self._overlay_hide_at = time.time() + 60  # keep showing while running
                self._state = IDLE

            elif kind == "workflow_done":
                _, msg = item
                self.overlay.update(msg)
                self._overlay_hide_at = time.time() + 5

            elif kind == "status_update":
                _, label = item
                self._status_item.title = label

        except queue.Empty:
            pass

        # Auto-hide overlay
        if self._overlay_hide_at and time.time() >= self._overlay_hide_at:
            self.overlay.hide()
            self._overlay_hide_at = None

        # Pending mode from workflow engine set_mode action
        _pending = os.path.expanduser("~/.mjai/.pending_mode")
        if os.path.exists(_pending):
            try:
                mode = open(_pending).read().strip()
                os.unlink(_pending)
                if mode:
                    errors = self.daemon.trigger_mode(mode)
                    self.title = self._mode_title()
                    self.overlay.show(f"{mode}  —  via workflow")
                    self._overlay_hide_at = time.time() + 5
            except Exception as e:
                log.warning("Pending mode error: %s", e)

        # Pending voice from iMessage trigger (e.g. "!deep work" → treat as spoken command)
        _pending_voice = os.path.expanduser("~/.mjai/.pending_voice")
        if os.path.exists(_pending_voice) and self._state == IDLE:
            try:
                text = open(_pending_voice).read().strip()
                os.unlink(_pending_voice)
                if text:
                    self._state = THINKING
                    self.overlay.show(
                        f"UnamOS  ·  {text[:30]}",
                        color=NSColor.systemOrangeColor(),
                    )
                    threading.Thread(
                        target=self._call_claude, args=(text,), daemon=True
                    ).start()
            except Exception as e:
                log.warning("Pending voice error: %s", e)

    # ── Async engine loop ─────────────────────────────────────────────────────

    def _start_engine_loop(self):
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_until_complete(self._run_engine())

    async def _run_engine(self):
        import uvicorn
        config = uvicorn.Config(
            _server_app, host="127.0.0.1", port=7700,
            log_level="warning", access_log=False,
        )
        server = uvicorn.Server(config)
        log.info("UnamOS engine server starting on http://127.0.0.1:7700")
        await server.serve()

    def _run_workflow_async(self, workflow: dict, trigger: str, extra_ctx: dict = None):
        """Submit a workflow to the async event loop from any thread."""
        async def _exec():
            run = await self._engine.execute(workflow, trigger, extra_ctx)
            msg = f"DONE  —  {workflow.get('name', '?')} ({run.status})"
            self._result_queue.put(("workflow_done", msg))
        asyncio.run_coroutine_threadsafe(_exec(), self._async_loop)

    # ── Background workers ────────────────────────────────────────────────────

    def _auto_boot(self):
        self.daemon.trigger_mode("BOOT")
        self._result_queue.put(("title_update", None))

    def _record_audio(self):
        audio = record(self._rec_stop)
        transcript = transcribe(audio)
        self._result_queue.put(("transcript", transcript))

    def _call_claude(self, intent: str):
        # First: check if a workflow matches the voice input
        workflow = self._engine.find_by_voice(intent)
        if workflow:
            self._run_workflow_async(workflow, trigger="voice", extra_ctx={"voice_input": intent})
            wf_name = workflow.get("name", "workflow")
            self._result_queue.put(("workflow_running", wf_name))
            return
        # Otherwise: fall back to Claude mode suggestion
        mode, rationale = self.daemon.claude_suggest(intent)
        self._result_queue.put(("mode", mode, rationale))

    # ── Apply mode ────────────────────────────────────────────────────────────

    def _apply(self, mode: str | None, rationale: str):
        if mode is None:
            self.overlay.update(
                f"UnamOS  ·  {rationale or 'no match'}",
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
            rumps.alert("UnamOS", f"{len(errors)} action(s) failed. Check ~/.mjai/action.log")

    # ── Menu refresh ──────────────────────────────────────────────────────────

    def _refresh_menu(self, _=None):
        """Update dynamic menu items — runs on main thread via rumps.Timer."""
        # Network checks in background; results posted via queue
        threading.Thread(target=self._check_services, daemon=True).start()
        # Menu tree updates must happen here (main thread)
        self._update_workflows_menu()
        self._update_last_run()

    def _check_services(self):
        """Background: probe services, post status string via result queue."""
        import urllib.request, socket
        services = []
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
            services.append("Ollama")
        except Exception:
            pass
        try:
            s = socket.create_connection(("localhost", 27017), timeout=1)
            s.close()
            services.append("MongoDB")
        except Exception:
            pass
        try:
            from windows_bridge import get_bridge
            bridge = get_bridge()
            if bridge and bridge.connected:
                services.append("Windows")
        except Exception:
            pass
        wf_count = len(self._engine._workflows)
        svc_str = "  ·  ".join(services) if services else "no services"
        self._result_queue.put(("status_update", f"{svc_str}  ·  {wf_count} workflows"))

    def _update_workflows_menu(self):
        """Main thread: rebuild workflows submenu."""
        try:
            self._workflows_menu.clear()
            for wf in self._engine.list_workflows():
                name = wf["name"]
                self._workflows_menu.add(
                    rumps.MenuItem(name, callback=self._make_workflow_trigger(name))
                )
            if not self._engine.list_workflows():
                self._workflows_menu.add(rumps.MenuItem("No workflows yet"))
        except Exception:
            pass

    def _update_last_run(self):
        """Main thread: update last run label."""
        import json
        last_run_path = Path.home() / ".mjai" / ".last_run"
        try:
            if last_run_path.exists():
                data = json.loads(last_run_path.read_text())
                wf = data.get("workflow", "?")
                status = data.get("status", "?")
                elapsed = int(time.time() - data.get("ts", 0))
                age = f"{elapsed // 60}m ago" if elapsed >= 60 else f"{elapsed}s ago"
                if elapsed >= 3600:
                    age = f"{elapsed // 3600}h ago"
                icon = "✓" if status == "completed" else "✗"
                self._last_run_item.title = f"Last run: {wf} {icon} {age}"
            else:
                self._last_run_item.title = "No runs yet"
        except Exception:
            pass

    def _make_workflow_trigger(self, wf_name: str):
        def _trigger(_):
            wf = self._engine._workflows.get(wf_name)
            if wf:
                self._run_workflow_async(wf, "menu")
                self.overlay.show(f"Running  ·  {wf_name}")
                self._overlay_hide_at = time.time() + 60
        return _trigger

    # ── Menu items ────────────────────────────────────────────────────────────

    @rumps.clicked("About")
    def about(self, _):
        m = self.daemon.current_mode or "—"
        rumps.alert("UnamOS", f"Current mode: {m}\nHotkey: {self.daemon.config.hotkey}")

    @rumps.clicked("Quit")
    def quit_app(self, _):
        self.daemon.stop_watcher()
        rumps.quit_application()


if __name__ == "__main__":
    MJAIApp().run()
