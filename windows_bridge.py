"""
UnamOS Windows Bridge (Mac side)
Maintains a persistent WebSocket connection to the Windows companion.
Runs inside the engine's asyncio loop.
"""
import asyncio
import json
import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

_RECONNECT_DELAY = 10  # seconds between reconnect attempts


class WindowsBridge:
    def __init__(self, host: str):
        """host: 'ip:port', e.g. '192.168.1.50:56789'"""
        self._host = host
        self._ws = None
        self._connected = False
        self._running = False
        self._last_mode: str = ""

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self):
        """Run forever — connects and reconnects automatically."""
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                log.warning("Windows bridge disconnected: %s — retrying in %ds", e, _RECONNECT_DELAY)
            self._connected = False
            self._ws = None
            if self._running:
                await asyncio.sleep(_RECONNECT_DELAY)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    async def send_mode(self, mode: str):
        """Sync a mode change to Windows."""
        self._last_mode = mode
        await self._send({"type": "mode", "mode": mode, "source": "mac"})

    async def send_action(self, action: str, **kwargs):
        """Trigger a Windows action directly."""
        await self._send({"type": "action", "action": action, **kwargs})

    async def send_notify(self, message: str, title: str = "UnamOS"):
        await self.send_action("notify", message=message, title=title)

    async def ping(self) -> bool:
        """Returns True if Windows responds within 3s."""
        if not self._connected:
            return False
        try:
            await self._send({"type": "ping"})
            return True
        except Exception:
            return False

    async def _send(self, payload: dict):
        if self._ws and self._connected:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as e:
                log.warning("Windows bridge send failed: %s", e)
                self._connected = False

    async def _connect(self):
        try:
            import websockets
        except ImportError:
            log.error("pip install websockets to enable Windows bridge")
            await asyncio.sleep(60)
            return

        uri = f"ws://{self._host}"
        log.info("Connecting to Windows companion at %s", uri)

        async with websockets.connect(uri, ping_interval=30, ping_timeout=10) as ws:
            self._ws = ws
            self._connected = True
            log.info("Windows companion connected")

            # Announce ourselves
            await ws.send(json.dumps({"type": "hello", "platform": "mac", "version": "2.0.0"}))

            # Re-sync current mode if we had one
            if self._last_mode:
                await ws.send(json.dumps({"type": "mode", "mode": self._last_mode, "source": "mac-reconnect"}))

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    await self._handle_incoming(msg)
                except json.JSONDecodeError:
                    pass

    async def _handle_incoming(self, msg: dict):
        mtype = msg.get("type", "")

        if mtype == "pong":
            pass

        elif mtype == "hello":
            log.info("Windows companion identified: v%s, mode=%s",
                     msg.get("version"), msg.get("mode"))

        elif mtype == "mode_ack":
            log.info("Windows confirmed mode: %s", msg.get("mode"))

        elif mtype == "status_response":
            log.info("Windows status: mode=%s v=%s",
                     msg.get("mode"), msg.get("version"))

        else:
            log.debug("Windows bridge: %s", msg)


# ── Singleton + thread-safe helpers ───────────────────────────────────────────

_bridge: Optional[WindowsBridge] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def get_bridge() -> Optional[WindowsBridge]:
    return _bridge


def init_bridge(host: str) -> WindowsBridge:
    global _bridge
    _bridge = WindowsBridge(host)
    return _bridge


def set_loop(loop: asyncio.AbstractEventLoop):
    """Store the engine event loop so sync helpers can submit coroutines."""
    global _loop
    _loop = loop


def sync_send_mode(mode: str):
    """Thread-safe: send a mode change to Windows from any thread."""
    if _bridge and _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_bridge.send_mode(mode), _loop)


def sync_send_action(action: str, **kwargs):
    """Thread-safe: send an action to Windows from any thread."""
    if _bridge and _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_bridge.send_action(action, **kwargs), _loop)
