"""
War Room WebSocket endpoint — /ws/warroom

Broadcasts events from the EventBus to all connected War Room clients.
Supports JSON message protocol for bidirectional communication.

Security (F-003): Browser clients authenticate via the HttpOnly War Room
session cookie. Programmatic clients can still authenticate via the first
message: {"type": "auth", "token": "<bearer_token>"}.
"""

import hmac
import json
import logging
import asyncio
import os
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from event_bus import event_bus, Event

logger = logging.getLogger(__name__)

# Auth timeout: client must authenticate within this many seconds
_AUTH_TIMEOUT_S = 10


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


def _verify_ws_token(token: str) -> bool:
    """Validate a WebSocket auth token.

    Accepts either the LANCELOT_API_TOKEN (for programmatic clients)
    or a valid War Room session token (for browser-based War Room UI).
    """
    if not token:
        dev_mode = os.getenv("LANCELOT_DEV_MODE", "").lower() in ("true", "1", "yes")
        return dev_mode

    # Check against LANCELOT_API_TOKEN
    try:
        import secret_cache
        api_token = secret_cache.get("LANCELOT_API_TOKEN", "")
    except Exception:
        api_token = os.getenv("LANCELOT_API_TOKEN")
    if api_token and hmac.compare_digest(token, api_token):
        return True

    # Check against active War Room session tokens
    try:
        from src.core.auth_api import verify_warroom_session_token
        if verify_warroom_session_token(token):
            return True
    except ImportError as exc:
        logger.debug("War Room WS session-token verification unavailable: %s", exc)

    return False


def _verify_ws_cookie(websocket: WebSocket) -> bool:
    try:
        from src.core.auth_api import (
            get_warroom_session_cookie_name,
            verify_warroom_session_token,
        )

        cookie_name = get_warroom_session_cookie_name()
        token = websocket.cookies.get(cookie_name, "")
        return verify_warroom_session_token(token)
    except ImportError:
        return False


async def warroom_websocket(websocket: WebSocket) -> None:
    """WebSocket handler for /ws/warroom — mounted by gateway.py.

    Security (F-003): Requires authentication via first message.
    Client must send: {"type": "auth", "token": "<bearer_token>"}
    Server responds: {"type": "auth_ok"} or closes the connection.
    """
    await websocket.accept()

    # --- Authentication gate ---
    authenticated = _verify_ws_cookie(websocket)

    if not authenticated:
        try:
            data = await asyncio.wait_for(
                websocket.receive_text(), timeout=_AUTH_TIMEOUT_S
            )
            msg = json.loads(data)
            if msg.get("type") == "auth" and _verify_ws_token(msg.get("token", "")):
                authenticated = True
            elif msg.get("type") == "ping":
                # Some clients send ping first — check if dev mode allows unauthenticated
                if _verify_ws_token(""):
                    authenticated = True
        except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect) as exc:
            logger.debug("War Room WS unauthenticated handshake did not complete: %s", exc)

    if not authenticated:
        await websocket.send_text(json.dumps({
            "type": "auth_error",
            "error": "Authentication required. Browser clients use the session cookie; programmatic clients send {\"type\": \"auth\", \"token\": \"<token>\"}.",
        }))
        await websocket.close(code=4401, reason="Authentication required")
        logger.warning("War Room WS: rejected unauthenticated connection")
        return

    await websocket.send_text(json.dumps({"type": "auth_ok"}))

    # --- Authenticated: register and handle messages ---
    async with connection_manager._lock:
        connection_manager._connections.append(websocket)
    count = len(connection_manager._connections)
    logger.info("War Room WS authenticated and connected (%d active)", count)

    try:
        while True:
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
    except WebSocketDisconnect as exc:
        logger.debug("War Room WS disconnected: %s", exc)
    except Exception as exc:
        logger.error("War Room WS error: %s", exc)
    finally:
        await connection_manager.disconnect(websocket)
