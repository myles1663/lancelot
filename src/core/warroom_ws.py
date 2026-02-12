"""
War Room WebSocket endpoint — /ws/warroom

Broadcasts events from the EventBus to all connected War Room clients.
Supports JSON message protocol for bidirectional communication.
"""

import json
import logging
import asyncio
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from event_bus import event_bus, Event

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active War Room WebSocket connections."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
        count = len(self._connections)
        logger.info("War Room WS connected (%d active)", count)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)
        count = len(self._connections)
        logger.info("War Room WS disconnected (%d active)", count)

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to all connected clients."""
        if not self._connections:
            return
        data = json.dumps(message)
        async with self._lock:
            stale: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_text(data)
                except Exception:
                    stale.append(ws)
            for ws in stale:
                self._connections.remove(ws)

    @property
    def active_count(self) -> int:
        return len(self._connections)


# Global singleton
connection_manager = ConnectionManager()


async def _on_event(event: Event) -> None:
    """Forward all EventBus events to connected WS clients."""
    await connection_manager.broadcast(event.to_dict())


# Wire the event bus to the connection manager
event_bus.subscribe_all(_on_event)


async def warroom_websocket(websocket: WebSocket) -> None:
    """WebSocket handler for /ws/warroom — mounted by gateway.py."""
    await connection_manager.connect(websocket)
    try:
        while True:
            # Read client messages (for future bidirectional commands)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                else:
                    logger.debug("War Room WS received: %s", msg_type)
            except json.JSONDecodeError:
                logger.warning("War Room WS: invalid JSON received")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("War Room WS error: %s", exc)
    finally:
        await connection_manager.disconnect(websocket)
