"""Core logic for MJAI: action runner, Claude integration, state, watchdog."""
import json
import logging
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import shutil
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import Action, Config, load

MJAI_DIR = Path.home() / ".mjai"
ACTIONS_DIR = MJAI_DIR / "actions"
STATE_PATH = MJAI_DIR / ".state.json"
LOG_PATH = MJAI_DIR / "action.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


class Daemon:
    def __init__(self):
        self.config: Config = load()
        self.current_mode: Optional[str] = None
        self._lock = threading.Lock()
        self._observer: Optional[Observer] = None
        self.load_state()

    # ── Action runner ─────────────────────────────────────────────────────────

    def trigger_mode(self, name: str) -> list[str]:
        """Run all actions for a mode. Returns list of error strings."""
        mode = self.config.modes.get(name)
        if not mode:
            return [f"Unknown mode: {name}"]

        errors = []
        for action in mode.actions:
            err = self.run_action(action)
            if err:
                errors.append(err)

        with self._lock:
            self.current_mode = name
        self.save_state()

        if errors:
            self._log_errors(name, errors)

        # Broadcast to Windows companion if configured
        if self.config.windows_host and name != "BOOT":
            threading.Thread(
                target=self._notify_windows, args=(name,), daemon=True
            ).start()

        return errors

    def _notify_windows(self, mode: str):
        """Send mode switch to Windows companion service (non-blocking)."""
        import socket, json as _json
        try:
            host, port = self.config.windows_host.rsplit(":", 1)
            sock = socket.create_connection((host, int(port)), timeout=3)
            sock.sendall(_json.dumps({"mode": mode}).encode() + b"\n")
            sock.close()
            log.info("Windows notified: %s", mode)
        except Exception as e:
            log.debug("Windows companion not reachable: %s", e)

    def run_action(self, action: Action) -> Optional[str]:
        """Run a single action script. Returns error string or None."""
        script_path = self._find_script(action.script)
        if not script_path:
            err = f"Script not found: {action.script} (looked in {ACTIONS_DIR})"
            log.warning(err)
            return err

        cmd = [str(script_path)] + action.args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                err = f"{action.script}: exit {result.returncode} — {result.stderr.strip() or result.stdout.strip()}"
                log.warning(err)
                return err
            log.info("action ok: %s %s", action.script, " ".join(action.args))
            return None
        except subprocess.TimeoutExpired:
            err = f"{action.script}: timed out after 10s"
            log.warning(err)
            return err
        except Exception as e:
            err = f"{action.script}: {e}"
            log.warning(err)
            return err

    def _find_script(self, name: str) -> Optional[Path]:
        """Find script in actions dir — any executable, no extension required."""
        # Exact name first
        candidate = ACTIONS_DIR / name
        if candidate.exists() and candidate.stat().st_mode & 0o111:
            return candidate
        # Glob for name.*
        matches = list(ACTIONS_DIR.glob(f"{name}.*"))
        for m in matches:
            if m.stat().st_mode & 0o111:
                return m
        return None

    def _log_errors(self, mode: str, errors: list[str]):
        with open(LOG_PATH, "a") as f:
            ts = datetime.now(timezone.utc).isoformat()
            f.write(f"\n[{ts}] {mode} — {len(errors)} error(s):\n")
            for e in errors:
                f.write(f"  {e}\n")

    # ── Claude integration ────────────────────────────────────────────────────

    def _gather_context(self) -> str:
        """Snapshot of what the user is doing right now."""
        lines = []
        now = datetime.now()
        lines.append(f"Current time: {now.strftime('%A %B %d, %Y  %I:%M %p')}")
        lines.append(f"Active mode: {self.current_mode or 'none'}")

        # Frontmost app
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell app "System Events" to name of first process whose frontmost is true'],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                lines.append(f"Active app: {r.stdout.strip()}")
        except Exception:
            pass

        # Running apps (non-background)
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell app "System Events" to name of every process whose background only is false'],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                apps = [a.strip() for a in r.stdout.strip().split(",") if a.strip()][:12]
                lines.append(f"Open apps: {', '.join(apps)}")
        except Exception:
            pass

        # Recent git activity across workspace
        try:
            r = subprocess.run(
                ["git", "-C", str(Path.home() / "workspace"), "log",
                 "--oneline", "--all", "--author-date-order", "-5"],
                capture_output=True, text=True, timeout=4)
            if r.returncode == 0 and r.stdout.strip():
                lines.append(f"Recent git commits:\n{r.stdout.strip()}")
        except Exception:
            pass

        # Active git branch
        try:
            r = subprocess.run(
                ["git", "-C", str(Path.home() / "workspace"), "branch", "--show-current"],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                lines.append(f"Current git branch: {r.stdout.strip()}")
        except Exception:
            pass

        return "\n".join(lines)

    def claude_suggest(self, intent: str) -> tuple[Optional[str], str]:
        """Ask Claude to suggest a mode via the claude CLI. Returns (mode_name|None, rationale)."""
        claude_bin = shutil.which("claude") or "/Users/manujawahar/.local/bin/claude"
        context = self._gather_context()
        prompt = (
            f"{self.config.claude_system_prompt}\n\n"
            f"--- USER CONTEXT ---\n{context}\n\n"
            f"Available modes:\n{self.config.mode_list_for_claude()}\n\n"
            f"User said: {intent}"
        )
        try:
            env = dict(__import__("os").environ)
            env["CLAUDE_DISABLE_DESKTOP"] = "1"
            result = subprocess.run(
                [claude_bin, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=30,
                cwd="/tmp",
                env=env,
            )
            text = result.stdout.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = "\n".join(
                    line for line in text.splitlines()
                    if not line.startswith("```")
                ).strip()
            data = json.loads(text)
            mode = data.get("mode")
            rationale = data.get("rationale", "")
            if mode and mode not in self.config.modes:
                return None, f"Claude suggested unknown mode '{mode}': {rationale}"
            return mode, rationale
        except json.JSONDecodeError as e:
            log.warning("Claude response parse error: %s — raw: %s", e, result.stdout[:200])
            return None, "Could not parse Claude response."
        except subprocess.TimeoutExpired:
            return None, "Claude timed out."
        except Exception as e:
            log.error("Claude error: %s", e)
            return None, f"Claude error: {e}"

    # ── State persistence ─────────────────────────────────────────────────────

    def save_state(self):
        state = {
            "current_mode": self.current_mode,
            "mode_start": datetime.now(timezone.utc).isoformat(),
        }
        STATE_PATH.write_text(json.dumps(state))

    def load_state(self):
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text())
                self.current_mode = state.get("current_mode")
            except Exception:
                pass

    # ── Config reload ─────────────────────────────────────────────────────────

    def reload_config(self):
        try:
            self.config = load()
            log.info("Config reloaded.")
        except Exception as e:
            log.error("Config reload failed: %s", e)

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def start_watcher(self):
        handler = _ConfigHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(MJAI_DIR), recursive=False)
        self._observer.start()
        log.info("Watching %s for changes.", MJAI_DIR)

    def stop_watcher(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "trigger":
        d = Daemon()
        errors = d.trigger_mode(sys.argv[2])
        if errors:
            for e in errors:
                print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1 if errors else 0)
    else:
        print("Usage: python daemon.py trigger <MODE>")
        sys.exit(1)


class _ConfigHandler(FileSystemEventHandler):
    def __init__(self, daemon: Daemon):
        self._daemon = daemon

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith("config.yaml"):
            log.info("config.yaml changed — reloading.")
            self._daemon.reload_config()
