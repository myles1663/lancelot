"""
Shared backend auth dependency for War Room and control-plane routers.

Routers that expose administrative, governance, or runtime-management
operations should depend on `require_authenticated_request` so they fail
closed unless the gateway explicitly wires in the active auth verifier.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import HTTPException, WebSocket, WebSocketException
from starlette.requests import HTTPConnection

logger = logging.getLogger(__name__)

_verify_request: Optional[Callable[[HTTPConnection], bool]] = None


def init_api_auth(verify_request: Optional[Callable[[HTTPConnection], bool]]) -> None:
    """Wire the gateway request verifier into shared router dependencies."""
    global _verify_request
    _verify_request = verify_request


def require_authenticated_request(connection: HTTPConnection) -> None:
    """Reject requests unless gateway auth has been configured and passes."""
    if _verify_request is None:
        logger.error("Shared API auth verifier not configured; refusing request")
        if isinstance(connection, WebSocket):
            raise WebSocketException(code=1011, reason="API auth not configured")
        raise HTTPException(status_code=503, detail="API auth not configured")
    if not _verify_request(connection):
        if isinstance(connection, WebSocket):
            raise WebSocketException(code=1008, reason="Unauthorized")
        raise HTTPException(status_code=401, detail="Unauthorized")
