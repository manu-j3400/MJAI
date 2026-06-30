"""
UnamOS Workflow Action Handlers
Each action takes a step dict + context dict and returns an output dict.
"""
import asyncio
import httpx
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"
MJAI_DIR = Path.home() / ".mjai"
ACTIONS_DIR = MJAI_DIR / "actions"


def _render(text: str, ctx: dict) -> str:
    """Simple {{variable}} template substitution."""
    for k, v in ctx.items():
        text = text.replace(f"{{{{{k}}}}}", str(v))
    return text


async def run_action(step: dict, ctx: dict) -> dict:
    """Dispatch to the right action handler. Returns output dict merged into ctx."""
    action_type = step.get("action", "shell")
    try:
        if action_type == "notify":
            return await _notify(step, ctx)
        elif action_type == "shell":
            return await _shell(step, ctx)
        elif action_type == "http":
            return await _http(step, ctx)
        elif action_type == "ollama":
            return await _ollama(step, ctx)
        elif action_type == "claude":
            return await _claude(step, ctx)
        elif action_type == "set_mode":
            return await _set_mode(step, ctx)
        elif action_type == "open_app":
            return await _open_app(step, ctx)
        elif action_type == "set_volume":
            return await _set_volume(step, ctx)
        elif action_type == "close_app":
            return await _close_app(step, ctx)
        elif action_type == "memory_set":
            return await _memory_set(step, ctx)
        elif action_type == "memory_get":
            return await _memory_get(step, ctx)
        elif action_type == "memory_append":
            return await _memory_append(step, ctx)
        elif action_type == "speak":
            return await _speak(step, ctx)
        elif action_type == "wait":
            await asyncio.sleep(float(step.get("seconds", 1)))
            return {}
        elif action_type == "windows":
            return await _windows(step, ctx)
        else:
            log.warning("Unknown action type: %s", action_type)
            return {"error": f"unknown action: {action_type}"}
    except Exception as e:
        log.error("Action %s failed: %s", action_type, e)
        return {"error": str(e)}


async def _notify(step: dict, ctx: dict) -> dict:
    msg = _render(step.get("message", ""), ctx)
    title = _render(step.get("title", "UnamOS"), ctx)
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e",
        f'display notification "{msg}" with title "{title}"',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    log.info("notify: %s", msg)
    return {"notified": msg}


async def _shell(step: dict, ctx: dict) -> dict:
    script = step.get("script") or step.get("command", "")
    args = [_render(str(a), ctx) for a in step.get("args", [])]

    if script:
        script_path = ACTIONS_DIR / script
        cmd = [str(script_path)] + args
    else:
        cmd = args

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path.home()),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    output = stdout.decode().strip()
    output_key = step.get("output_key", step.get("id", "shell_output"))
    log.info("shell %s → %s", cmd[0], output[:80])
    return {output_key: output, f"{output_key}_exit": proc.returncode}


async def _http(step: dict, ctx: dict) -> dict:
    url = _render(step.get("url", ""), ctx)
    method = step.get("method", "GET").upper()
    headers = step.get("headers", {})
    body = step.get("body")
    if body and isinstance(body, str):
        body = _render(body, ctx)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, url, headers=headers, content=body)
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            data = resp.text

    output_key = step.get("output_key", step.get("id", "http_response"))
    log.info("http %s %s → %d", method, url, resp.status_code)
    return {output_key: data}


async def _ollama(step: dict, ctx: dict) -> dict:
    """Run a prompt through local Ollama. Returns the text response."""
    model = step.get("model", "dolphin3:8b")
    prompt = _render(step.get("prompt", ""), ctx)
    system = _render(step.get("system", ""), ctx)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{OLLAMA_BASE}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()

    text = data.get("response", "").strip()
    output_key = step.get("output_key", step.get("id", "ollama_response"))
    log.info("ollama %s → %s...", model, text[:60])
    return {output_key: text}


async def _claude(step: dict, ctx: dict) -> dict:
    """Run a prompt through Claude CLI (claude -p)."""
    prompt = _render(step.get("prompt", ""), ctx)
    claude_bin = subprocess.run(
        ["which", "claude"], capture_output=True, text=True
    ).stdout.strip() or "/Users/manujawahar/.local/bin/claude"

    proc = await asyncio.create_subprocess_exec(
        claude_bin, "-p", prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd="/tmp",
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    text = stdout.decode().strip()
    output_key = step.get("output_key", step.get("id", "claude_response"))
    log.info("claude → %s...", text[:60])
    return {output_key: text}


async def _set_mode(step: dict, ctx: dict) -> dict:
    """Post a mode-change signal to the running MJAI daemon via state file."""
    mode = _render(step.get("mode", ""), ctx)
    state_path = MJAI_DIR / ".pending_mode"
    state_path.write_text(mode)
    log.info("set_mode → %s (pending)", mode)
    return {"mode_set": mode}


async def _open_app(step: dict, ctx: dict) -> dict:
    app = _render(step.get("app", step.get("args", [""])[0] if step.get("args") else ""), ctx)
    proc = await asyncio.create_subprocess_exec(
        "open", "-a", app,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return {"opened": app}


async def _set_volume(step: dict, ctx: dict) -> dict:
    level = _render(str(step.get("level", step.get("args", [50])[0])), ctx)
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", f"set volume output volume {level}",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return {"volume": int(level)}


async def _close_app(step: dict, ctx: dict) -> dict:
    app = _render(step.get("app", ""), ctx)
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", f'tell application "{app}" to quit',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return {"closed": app}


async def _memory_set(step: dict, ctx: dict) -> dict:
    from engine.memory import get_store
    key = _render(step.get("key", ""), ctx)
    value = _render(str(step.get("value", "")), ctx)
    await get_store().set(key, value)
    log.info("memory_set: %s = %s", key, value[:50] if isinstance(value, str) else value)
    return {f"memory.{key}": value}


async def _memory_get(step: dict, ctx: dict) -> dict:
    from engine.memory import get_store
    key = _render(step.get("key", ""), ctx)
    default = step.get("default", "")
    value = await get_store().get(key, default)
    output_key = step.get("output_key", f"memory.{key}")
    log.info("memory_get: %s = %s", key, str(value)[:50])
    return {output_key: value}


async def _memory_append(step: dict, ctx: dict) -> dict:
    from engine.memory import get_store
    key = _render(step.get("key", ""), ctx)
    value = _render(str(step.get("value", "")), ctx)
    max_len = int(step.get("max_len", 100))
    await get_store().append(key, value, max_len)
    return {f"memory.{key}_appended": value}


async def _speak(step: dict, ctx: dict) -> dict:
    """macOS text-to-speech via say command."""
    text = _render(step.get("text", step.get("message", "")), ctx)
    voice = step.get("voice", "Samantha")
    rate = step.get("rate", 175)
    proc = await asyncio.create_subprocess_exec(
        "say", "-v", voice, "-r", str(rate), text,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    log.info("speak: %s", text[:60])
    return {"spoken": text}


async def _windows(step: dict, ctx: dict) -> dict:
    """Forward an action to the Windows companion via the bridge."""
    from windows_bridge import get_bridge
    bridge = get_bridge()
    if bridge is None or not bridge.connected:
        log.warning("windows action: bridge not connected")
        return {"windows_error": "not connected"}

    action = step.get("windows_action", "notify")
    payload = {k: _render(str(v), ctx) for k, v in step.items()
               if k not in ("action", "id", "windows_action")}
    payload["action"] = action
    await bridge.send_action(action, **{k: v for k, v in payload.items() if k != "action"})
    log.info("windows action forwarded: %s", action)
    return {"windows_sent": action}
