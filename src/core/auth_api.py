"""
War Room Authentication API.

Validates username/password against environment variables and issues
session tokens with configurable timeout.
"""

import hashlib
import hmac
import os
import time
import uuid
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("lancelot.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# Session store: {token: {"expires_at": float, "username": str}}
_sessions: dict = {}

_SESSION_TIMEOUT = int(os.getenv("WARROOM_SESSION_TIMEOUT_MINUTES", "30")) * 60


def _get_warroom_username() -> str:
    """Lazy-load War Room username from secret_cache (or env fallback)."""
    try:
        import secret_cache
        return secret_cache.get("WARROOM_USERNAME", "")
    except Exception:
        return os.getenv("WARROOM_USERNAME", "")


def _get_warroom_password() -> str:
    """Lazy-load War Room password hash from secret_cache (or env fallback)."""
    try:
        import secret_cache
        return secret_cache.get("WARROOM_PASSWORD", "")
    except Exception:
        return os.getenv("WARROOM_PASSWORD", "")


def _verify_password(plain: str, stored: str) -> bool:
    """Verify a plaintext password against a stored value.

    Detects format:
    - SHA-256 hex (64 chars): hash the input and compare.
    - Legacy plaintext: direct constant-time comparison.
    """
    if len(stored) == 64:
        # Stored value looks like a SHA-256 hex digest
        input_hash = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        return hmac.compare_digest(input_hash, stored)
    # Legacy plaintext comparison
    return hmac.compare_digest(plain, stored)

_audit_logger = None


def init_auth_api(audit_logger=None):
    """Inject audit logger (called from gateway startup)."""
    global _audit_logger
    _audit_logger = audit_logger


def _cleanup_expired():
    now = time.time()
    expired = [t for t, s in _sessions.items() if s["expires_at"] < now]
    for t in expired:
        del _sessions[t]


@router.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")

    wr_user = _get_warroom_username()
    wr_pass = _get_warroom_password()

    if not wr_user or not wr_pass:
        return JSONResponse(status_code=503, content={
            "error": "War Room credentials not configured",
            "detail": "Set WARROOM_USERNAME and WARROOM_PASSWORD environment variables",
        })

    if not (hmac.compare_digest(username, wr_user)
            and _verify_password(password, wr_pass)):
        if _audit_logger:
            _audit_logger.log_event(
                "AUTH_LOGIN_FAILED",
                f"Failed login attempt for user: {username}",
                user=username,
            )
        return JSONResponse(status_code=401, content={"error": "Invalid credentials"})

    token = str(uuid.uuid4())
    _sessions[token] = {
        "expires_at": time.time() + _SESSION_TIMEOUT,
        "username": username,
    }
    _cleanup_expired()

    if _audit_logger:
        _audit_logger.log_event(
            "AUTH_LOGIN_SUCCESS",
            f"User {username} logged in",
            user=username,
        )

    return {
        "token": token,
        "expires_in": _SESSION_TIMEOUT,
        "username": username,
    }


@router.post("/validate")
async def validate_token(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"valid": False})
    token = auth[7:]
    session = _sessions.get(token)
    if not session or session["expires_at"] < time.time():
        return JSONResponse(status_code=401, content={"valid": False})
    remaining = session["expires_at"] - time.time()
    return {
        "valid": True,
        "remaining_seconds": int(remaining),
        "username": session["username"],
    }


@router.post("/logout")
async def logout(request: Request):
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        removed = _sessions.pop(token, None)
        if removed and _audit_logger:
            _audit_logger.log_event(
                "AUTH_LOGOUT",
                f"User {removed['username']} logged out",
                user=removed["username"],
            )
    return {"status": "ok"}


def verify_warroom_session(request: Request) -> bool:
    """Check if request has a valid War Room session token."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:]
    session = _sessions.get(token)
    if not session:
        return False
    if session["expires_at"] < time.time():
        del _sessions[token]
        return False
    return True
