"""
WebSocket Connection Manager
Maintains active connections and broadcasts updates from Redis pub/sub
(or in-memory queue fallback when Redis is unavailable).
"""
import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info(f"WS connected — total: {len(self._connections)}")

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info(f"WS disconnected — total: {len(self._connections)}")

    async def broadcast(self, event: str, data: Any) -> None:
        message = json.dumps({"event": event, "data": data})
        dead = []
        for ws in list(self._connections):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def send_to(self, ws: WebSocket, event: str, data: Any) -> None:
        try:
            await ws.send_text(json.dumps({"event": event, "data": data}))
        except Exception as e:
            logger.warning(f"Failed to send to WS: {e}")
            self.disconnect(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


async def redis_listener(channels: list[str]) -> None:
    """
    Subscribe to Redis pub/sub channels and broadcast to WebSocket clients.
    Falls back to in-memory queue when Redis is unavailable.
    """
    from app.core.redis_client import _try_connect, _subscribe_in_memory, _use_memory

    r = await _try_connect()

    if r:
        # Real Redis path
        pubsub = r.pubsub()
        await pubsub.subscribe(*channels)
        logger.info(f"Redis listener subscribed to: {channels}")
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    channel = message["channel"]
                    try:
                        data = json.loads(message["data"])
                        await manager.broadcast(channel, data)
                    except Exception as e:
                        logger.error(f"Broadcast error on {channel}: {e}")
        except asyncio.CancelledError:
            await pubsub.unsubscribe()
    else:
        # In-memory fallback path
        logger.info("Using in-memory pub/sub listener")
        q = _subscribe_in_memory()
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(q.get(), timeout=1.0)
                    payload = json.loads(raw)
                    channel = payload.get("channel", "")
                    data = payload.get("data", {})
                    if channel in channels:
                        await manager.broadcast(channel, data)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
