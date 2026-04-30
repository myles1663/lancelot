from __future__ import annotations

import hmac
import sys
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse


def bind_gateway_globals(**kwargs):
    globals().update(kwargs)


def _resolve_gateway_runtime_state():
    gateway_module = sys.modules.get("gateway")
    runtime_logger = getattr(gateway_module, "logger", globals().get("logger"))
    runtime_api_token = getattr(gateway_module, "API_TOKEN", globals().get("API_TOKEN"))
    runtime_dev_mode = getattr(gateway_module, "DEV_MODE", globals().get("DEV_MODE"))
    return runtime_logger, runtime_api_token, runtime_dev_mode


def error_response(
    status_code: int,
    message: str,
    detail: str = None,
    request_id: str = None,
) -> JSONResponse:
    content = {"error": message, "status": status_code}
    if detail:
        content["detail"] = detail
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=content)


class RateLimiter:
    """Sliding-window rate limiter per client IP with stale-entry cleanup."""

    _CLEANUP_INTERVAL_S = 300

    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = {}
        self._last_cleanup = time.time()

    def check(self, ip: str) -> bool:
        now = time.time()
        if now - self._last_cleanup > self._CLEANUP_INTERVAL_S:
            self._cleanup_stale(now)
        if ip not in self._requests:
            self._requests[ip] = []
        self._requests[ip] = [t for t in self._requests[ip] if t > now - self.window]
        if len(self._requests[ip]) >= self.max_requests:
            return False
        self._requests[ip].append(now)
        return True

    def _cleanup_stale(self, now: float) -> None:
        stale_ips = [
            ip for ip, timestamps in self._requests.items()
            if not timestamps or all(t <= now - self.window for t in timestamps)
        ]
        for ip in stale_ips:
            del self._requests[ip]
        self._last_cleanup = now
        if stale_ips:
            logger.debug("Rate limiter: cleaned %d stale IP entries", len(stale_ips))


def verify_token(request: Request) -> bool:
    """Validate gateway authentication for API tokens or War Room sessions."""
    runtime_logger, runtime_api_token, runtime_dev_mode = _resolve_gateway_runtime_state()

    if not runtime_api_token:
        if runtime_dev_mode:
            runtime_logger.warning(
                "SECURITY: Gateway running in dev mode (LANCELOT_DEV_MODE=true) "
                "all requests accepted without authentication."
            )
            return True
        try:
            from src.core.auth_api import verify_warroom_session

            if verify_warroom_session(request):
                return True
        except ImportError as exc:
            runtime_logger.debug("War Room session verifier unavailable during API auth fallback: %s", exc)
        runtime_logger.error(
            "SECURITY: No LANCELOT_API_TOKEN configured and dev mode not enabled. "
            "Set LANCELOT_API_TOKEN for production or LANCELOT_DEV_MODE=true for development."
        )
        return False

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        if hmac.compare_digest(auth_header[7:], runtime_api_token):
            return True

    try:
        from src.core.auth_api import verify_warroom_session

        return verify_warroom_session(request)
    except ImportError:
        return False


def _require_request_capability(
    request: Request,
    capability: str,
    *,
    request_id: str | None = None,
) -> JSONResponse | None:
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        from src.core.auth_api import request_has_capability

        if request_has_capability(request, capability):
            return None
    except Exception as exc:
        logger.warning("Capability enforcement failed for %s: %s", capability, exc)
        return error_response(503, "Authorization unavailable", request_id=request_id)

    return error_response(403, f"Missing capability: {capability}", request_id=request_id)


def make_request_id() -> str:
    return str(uuid.uuid4())
