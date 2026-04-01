"""
Generic WebSocket pub/sub manager with channel-based broadcasting and heartbeat.
"""

import asyncio
import logging
import time

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Channel-based WebSocket pub/sub with heartbeat monitoring."""

    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {}
        self._client_channels: dict[WebSocket, set[str]] = {}
        self._last_pong: dict[WebSocket, float] = {}
        self._heartbeat_task: asyncio.Task | None = None

    # ── Connection lifecycle ──────────────────────────────────────────────

    def connect(self, ws: WebSocket) -> None:
        self._client_channels[ws] = set()
        self._last_pong[ws] = time.monotonic()
        logger.info("ws_client_connected clients=%d", len(self._client_channels))

    def disconnect(self, ws: WebSocket) -> None:
        channels = self._client_channels.pop(ws, set())
        for channel in channels:
            subs = self._channels.get(channel)
            if subs:
                subs.discard(ws)
                if not subs:
                    del self._channels[channel]
        self._last_pong.pop(ws, None)
        logger.info("ws_client_disconnected clients=%d", len(self._client_channels))

    # ── Channel subscriptions ─────────────────────────────────────────────

    def subscribe(self, ws: WebSocket, channel: str) -> None:
        self._channels.setdefault(channel, set()).add(ws)
        if ws in self._client_channels:
            self._client_channels[ws].add(channel)
        logger.debug("ws_subscribed channel=%s subscribers=%d", channel, len(self._channels[channel]))

    def unsubscribe(self, ws: WebSocket, channel: str) -> None:
        subs = self._channels.get(channel)
        if subs:
            subs.discard(ws)
            if not subs:
                del self._channels[channel]
        if ws in self._client_channels:
            self._client_channels[ws].discard(channel)

    # ── Broadcasting ──────────────────────────────────────────────────────

    async def broadcast(self, channel: str, message: dict) -> None:
        subs = self._channels.get(channel)
        if not subs:
            return
        msg = {**message, "channel": channel}
        dead: list[WebSocket] = []
        for ws in subs:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(msg)
                else:
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """Return WebSocket connection statistics."""
        total_subs = sum(len(subs) for subs in self._channels.values())
        return {
            "connected_clients": len(self._client_channels),
            "active_channels": len(self._channels),
            "total_subscriptions": total_subs,
        }

    # ── Heartbeat ─────────────────────────────────────────────────────────

    def record_pong(self, ws: WebSocket) -> None:
        self._last_pong[ws] = time.monotonic()

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="ws-heartbeat")

    def stop_heartbeat(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                now = time.monotonic()
                dead: list[WebSocket] = []
                for ws in list(self._client_channels):
                    last = self._last_pong.get(ws, 0)
                    if now - last > 45:
                        dead.append(ws)
                        continue
                    try:
                        if ws.client_state == WebSocketState.CONNECTED:
                            await ws.send_json({"type": "ping"})
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self.disconnect(ws)
                    try:
                        await ws.close()
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass


# Module-level singleton
ws_manager = WebSocketManager()
