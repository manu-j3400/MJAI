"""
UnamOS Engine Server
FastAPI app + background services inside MJAI.app:
  - Cron scheduler (APScheduler)
  - Background triggers: app events, battery, network, iMessage, file watcher
  - MongoDB memory store
  - /webhook/<path>   — trigger workflows via HTTP
  - /api/workflows    — list workflows
  - /api/runs         — recent run history
  - /api/status       — system status
  - /api/memory       — persistent memory store
  - /                 — web dashboard
"""
import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from engine.workflow import WorkflowEngine

log = logging.getLogger(__name__)

DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"

app = FastAPI(title="UnamOS Engine", version="2.0.0", docs_url=None)

_engine: Optional[WorkflowEngine] = None
_config = None   # set from menubar after config loads


def set_engine(engine: WorkflowEngine):
    global _engine
    _engine = engine


def set_config(config):
    global _config
    _config = config


@app.on_event("startup")
async def _startup():
    """Boot all background services when uvicorn starts."""
    if _engine is None:
        return

    # 1. MongoDB memory store
    from engine.memory import get_store
    store = get_store()
    connected = await store.connect()
    _engine._memory = store

    # Wire MongoDB for run history too
    if connected:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient("mongodb://localhost:27017", serverSelectionTimeoutMS=2000)
        _engine._db = client["unamosvos"]

    # 2. Cron scheduler — wire back to engine so reload() propagates
    from engine.scheduler import WorkflowScheduler
    scheduler = WorkflowScheduler(_engine)
    scheduler.start()
    _engine._scheduler = scheduler

    # 3. Background triggers
    asyncio.create_task(_start_triggers())

    # 4. Windows bridge (if configured)
    if _config and _config.windows_host:
        from windows_bridge import init_bridge, set_loop
        bridge = init_bridge(_config.windows_host)
        set_loop(asyncio.get_event_loop())
        asyncio.create_task(bridge.start())
        log.info("Windows bridge starting → %s", _config.windows_host)

    log.info("UnamOS engine fully booted.")


async def _start_triggers():
    """Boot background watchers from config auto_triggers."""
    if _config is None:
        log.info("No config for triggers — skipping auto-trigger boot")
        return

    from engine.triggers import AppWatcher, BatteryWatcher, NetworkWatcher, iMessageWatcher, FileWatcher
    at = _config.auto_triggers

    tasks = []

    if at.app_events:
        app_map = {t.app: t.mode or t.workflow for t in at.app_events}
        tasks.append(asyncio.create_task(AppWatcher(_engine, app_map).run()))
        log.info("App event trigger started (%d apps)", len(at.app_events))

    if at.battery:
        batt_cfg = [{"below": t.below, "workflow": t.workflow, "mode": t.mode} for t in at.battery]
        tasks.append(asyncio.create_task(BatteryWatcher(_engine, batt_cfg).run()))
        log.info("Battery trigger started")

    if at.network:
        net_cfg = [{"ssid": t.ssid, "workflow": t.workflow, "mode": t.mode} for t in at.network]
        tasks.append(asyncio.create_task(NetworkWatcher(_engine, net_cfg).run()))
        log.info("Network trigger started")

    if at.imessage:
        tasks.append(asyncio.create_task(iMessageWatcher(_engine).run()))
        log.info("iMessage trigger started")

    if at.files:
        file_cfg = [{"path": t.path, "pattern": t.pattern, "workflow": t.workflow} for t in at.files]
        tasks.append(asyncio.create_task(FileWatcher(_engine, file_cfg).run()))
        log.info("File watcher trigger started")

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)



# ── API ────────────────────────────────────────────────────────────────────────

@app.get("/api/workflows")
async def list_workflows():
    if not _engine:
        raise HTTPException(503, "Engine not initialized")
    return {"workflows": _engine.list_workflows()}


@app.get("/api/status")
async def status():
    checks = {}

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get("http://localhost:11434/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            checks["ollama"] = {"status": "ok", "models": models}
    except Exception as e:
        checks["ollama"] = {"status": "error", "error": str(e)}

    # OpenClaw
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get("http://localhost:18789/health")
            checks["openclaw"] = {"status": "ok"}
    except Exception:
        checks["openclaw"] = {"status": "offline"}

    # MongoDB
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient("mongodb://localhost:27017", serverSelectionTimeoutMS=2000)
        await client.admin.command("ping")
        checks["mongodb"] = {"status": "ok"}
    except Exception as e:
        checks["mongodb"] = {"status": "error", "error": str(e)}

    checks["unamosvos"] = {"status": "ok", "version": "2.0.0"}
    checks["workflows"] = len(_engine.list_workflows()) if _engine else 0

    # Windows bridge status
    try:
        from windows_bridge import get_bridge
        bridge = get_bridge()
        checks["windows"] = {"status": "connected" if (bridge and bridge.connected) else "offline"}
    except Exception:
        checks["windows"] = {"status": "not configured"}

    return {"status": "ok", "services": checks, "timestamp": datetime.now().isoformat()}


@app.get("/api/runs")
async def recent_runs():
    if not _engine or not _engine._db:
        return {"runs": [], "note": "MongoDB not connected"}
    try:
        cursor = _engine._db.workflow_runs.find({}, {"_id": 0}).sort("started_at", -1).limit(50)
        runs = await cursor.to_list(length=50)
        return {"runs": runs}
    except Exception as e:
        return {"runs": [], "error": str(e)}


@app.post("/webhook/{path:path}")
async def webhook_trigger(path: str, request: Request):
    if not _engine:
        raise HTTPException(503, "Engine not initialized")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    wh_workflows = _engine.get_webhook_workflows()
    full_path = f"/{path}"
    workflow = wh_workflows.get(full_path)
    if not workflow:
        raise HTTPException(404, f"No workflow with webhook path {full_path}")

    asyncio.create_task(
        _engine.execute(workflow, trigger="webhook", extra_ctx={"webhook_body": body})
    )
    return {"queued": True, "workflow": workflow.get("name")}


@app.post("/api/run/{workflow_name}")
async def trigger_workflow(workflow_name: str, request: Request):
    if not _engine:
        raise HTTPException(503, "Engine not initialized")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    wf = _engine._workflows.get(workflow_name)
    if not wf:
        raise HTTPException(404, f"Workflow '{workflow_name}' not found")

    asyncio.create_task(
        _engine.execute(wf, trigger="api", extra_ctx=body)
    )
    return {"queued": True, "workflow": workflow_name}


@app.post("/api/reload")
async def reload_workflows():
    if not _engine:
        raise HTTPException(503, "Engine not initialized")
    _engine.reload()
    return {"reloaded": len(_engine.list_workflows())}


@app.get("/api/memory")
async def get_memory():
    if not _engine or not _engine._memory:
        return {"memory": {}, "note": "Memory store not connected"}
    return {"memory": await _engine._memory.all()}


@app.post("/api/memory/{key}")
async def set_memory(key: str, request: Request):
    if not _engine or not _engine._memory:
        raise HTTPException(503, "Memory store not connected")
    body = await request.json()
    value = body.get("value", body)
    await _engine._memory.set(key, value)
    return {"key": key, "value": value}


@app.delete("/api/memory/{key}")
async def delete_memory(key: str):
    if not _engine or not _engine._memory:
        raise HTTPException(503, "Memory store not connected")
    await _engine._memory.delete(key)
    return {"deleted": key}
