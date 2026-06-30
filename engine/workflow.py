"""
UnamOS Workflow Engine
Loads YAML workflow definitions from ~/.mjai/workflows/ and executes them.
Supports: voice triggers, cron schedules, webhook triggers, conditional steps.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LAST_RUN_PATH = Path.home() / ".mjai" / ".last_run"

import yaml

from engine.actions import run_action

log = logging.getLogger(__name__)

WORKFLOWS_DIR = Path.home() / ".mjai" / "workflows"
WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


class WorkflowRun:
    def __init__(self, workflow_name: str, trigger: str, ctx: dict):
        self.id = str(uuid.uuid4())[:8]
        self.workflow_name = workflow_name
        self.trigger = trigger
        self.started_at = datetime.now(timezone.utc)
        self.ctx = dict(ctx)
        self.steps_run: list[dict] = []
        self.status = "running"
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow": self.workflow_name,
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
            "status": self.status,
            "steps_run": self.steps_run,
            "error": self.error,
        }


class WorkflowEngine:
    def __init__(self, db=None):
        self._workflows: dict[str, dict] = {}
        self._db = db
        self._active_runs: list[WorkflowRun] = []
        self._memory = None   # set after async connect
        self._scheduler = None  # set after cron scheduler starts
        self.load_all()

    def load_all(self):
        self._workflows.clear()
        for path in WORKFLOWS_DIR.glob("*.yaml"):
            try:
                wf = yaml.safe_load(path.read_text())
                name = wf.get("name", path.stem)
                self._workflows[name] = wf
                log.info("Loaded workflow: %s", name)
            except Exception as e:
                log.warning("Failed to load %s: %s", path, e)
        log.info("Loaded %d workflow(s) from %s", len(self._workflows), WORKFLOWS_DIR)

    def reload(self):
        self.load_all()
        if self._scheduler is not None:
            self._scheduler.reload()

    def list_workflows(self) -> list[dict]:
        return [
            {
                "name": wf.get("name", k),
                "description": wf.get("description", ""),
                "triggers": [t.get("type") for t in wf.get("triggers", [])],
            }
            for k, wf in self._workflows.items()
        ]

    def find_by_voice(self, transcript: str) -> Optional[dict]:
        """Find a workflow whose voice trigger phrase matches the transcript."""
        tl = transcript.lower()
        best = None
        best_score = 0
        for wf in self._workflows.values():
            for trigger in wf.get("triggers", []):
                if trigger.get("type") != "voice":
                    continue
                phrase = trigger.get("phrase", "").lower()
                # Score: fraction of phrase words present in transcript
                words = phrase.split()
                score = sum(1 for w in words if w in tl) / max(len(words), 1)
                if score > best_score and score >= 0.6:
                    best_score = score
                    best = wf
        return best

    def get_cron_workflows(self) -> list[tuple[str, dict]]:
        """Return (schedule, workflow) pairs for all cron-triggered workflows."""
        result = []
        for wf in self._workflows.values():
            for trigger in wf.get("triggers", []):
                if trigger.get("type") == "cron":
                    result.append((trigger["schedule"], wf))
        return result

    def get_webhook_workflows(self) -> dict[str, dict]:
        """Return {path: workflow} for all webhook-triggered workflows."""
        result = {}
        for wf in self._workflows.values():
            for trigger in wf.get("triggers", []):
                if trigger.get("type") == "webhook":
                    path = trigger.get("path", f"/{wf.get('name', 'workflow')}")
                    result[path] = wf
        return result

    async def execute(self, workflow: dict, trigger: str, extra_ctx: dict = None) -> WorkflowRun:
        """Execute a workflow. Returns the run record."""
        name = workflow.get("name", "unknown")
        ctx = {
            "workflow_name": name,
            "trigger": trigger,
            "run_id": str(uuid.uuid4())[:8],
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%A %B %d %Y"),
            "time": datetime.now().strftime("%I:%M %p"),
        }
        # Inject persistent memory as template variables
        if self._memory is not None:
            try:
                ctx.update(self._memory.as_context())
            except Exception:
                pass
        if extra_ctx:
            ctx.update(extra_ctx)

        run = WorkflowRun(name, trigger, ctx)
        self._active_runs.append(run)
        log.info("Starting workflow: %s (run %s)", name, run.id)

        try:
            await self._run_steps(workflow.get("steps", []), run)
            run.status = "completed"
        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            log.error("Workflow %s failed: %s", name, e)
        finally:
            self._active_runs = [r for r in self._active_runs if r.id != run.id]
            if self._db is not None:
                try:
                    await self._db.workflow_runs.insert_one(run.to_dict())
                except Exception as e:
                    log.warning("Failed to save run to MongoDB: %s", e)

        log.info("Workflow %s → %s", name, run.status)
        try:
            LAST_RUN_PATH.write_text(json.dumps({
                "workflow": name, "status": run.status, "ts": time.time()
            }))
        except Exception:
            pass
        return run

    async def _run_steps(self, steps: list, run: WorkflowRun):
        for step in steps:
            step_id = step.get("id", step.get("action", "step"))

            # Handle conditional steps
            condition = step.get("condition")
            if condition:
                result = self._eval_condition(condition, run.ctx)
                branch = step.get("then", []) if result else step.get("else", [])
                await self._run_steps(branch, run)
                continue

            # Handle parallel steps
            if "parallel" in step:
                await asyncio.gather(*[
                    self._exec_step(s, run) for s in step["parallel"]
                ])
                continue

            await self._exec_step(step, run)

    async def _exec_step(self, step: dict, run: WorkflowRun):
        step_id = step.get("id", step.get("action", "step"))
        t0 = time.time()
        output = await run_action(step, run.ctx)
        elapsed = time.time() - t0
        run.ctx.update(output)
        run.steps_run.append({
            "id": step_id,
            "action": step.get("action"),
            "output": output,
            "elapsed_s": round(elapsed, 2),
        })
        log.info("  step %s → %s (%.1fs)", step_id, list(output.keys()), elapsed)

    def _eval_condition(self, condition: str, ctx: dict) -> bool:
        """Evaluate a simple condition string against context."""
        for k, v in ctx.items():
            condition = condition.replace(f"{{{{{k}}}}}", str(v))
        try:
            return bool(eval(condition, {"__builtins__": {}}, {}))
        except Exception:
            return False
