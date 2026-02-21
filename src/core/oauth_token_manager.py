"""
Anthropic OAuth Token Manager — PKCE flow, vault-backed token storage, auto-refresh.

V28 (v0.2.14): Adds OAuth 2.0 Authorization Code + PKCE as an alternative
to API keys for Anthropic authentication.  Tokens are stored in the
encrypted connector vault and refreshed proactively before expiry.

Public API:
    OAuthTokenManager(vault, port)
    manager.generate_auth_url()     -> (auth_url, state_nonce)
    manager.exchange_code(code, state) -> bool
    manager.get_valid_token()       -> str | None
    manager.get_token_status()      -> dict
    manager.revoke()                -> None
    manager.start_background_refresh()
    manager.stop_background_refresh()

Module-level helpers:
    get_oauth_manager()  -> OAuthTokenManager | None
    set_oauth_manager(m) -> None
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# ── OAuth Constants ──────────────────────────────────────────────────

ANTHROPIC_AUTH_URL = "https://claude.ai/oauth/authorize"
ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPES = "user:inference user:profile"

ACCESS_TOKEN_TTL = 8 * 3600          # 8 hours (Anthropic default)
REFRESH_WINDOW = 600                 # refresh 10 min before expiry
PENDING_FLOW_TTL = 600               # PKCE flow timeout (10 min)
BACKGROUND_CHECK_INTERVAL = 300      # background thread checks every 5 min

# Vault keys for persistent token storage
VAULT_ACCESS_TOKEN = "anthropic.oauth.access_token"
VAULT_REFRESH_TOKEN = "anthropic.oauth.refresh_token"
VAULT_TOKEN_EXPIRY = "anthropic.oauth.token_expiry"

# In-memory token cache (replaces os.environ for security — F-009)
# Avoids /proc/PID/environ exposure on Linux
ENV_OAUTH_TOKEN = "ANTHROPIC_OAUTH_TOKEN"  # kept for backward compat naming
_oauth_token_cache: Dict[str, str] = {}


def get_oauth_token() -> Optional[str]:
    """Retrieve the current OAuth access token from the in-memory cache.

    Used by FlagshipClient and gateway instead of os.environ.
    """
    return _oauth_token_cache.get("access_token")


# ── PKCE Helpers ─────────────────────────────────────────────────────

def _generate_code_verifier() -> str:
    """Generate a random code verifier (43-128 URL-safe chars)."""
    return secrets.token_urlsafe(64)[:128]


def _generate_code_challenge(verifier: str) -> str:
    """Compute S256 code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── OAuthTokenManager ────────────────────────────────────────────────

class OAuthTokenManager:
    """Manages the full Anthropic OAuth lifecycle: PKCE, exchange, vault storage, refresh."""

    def __init__(self, vault: Any, port: int = 8000):
        self._vault = vault
        self._port = port
        self._pending_flows: Dict[str, Dict[str, Any]] = {}  # state -> {code_verifier, created_at}
        self._lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── Auth URL Generation ──────────────────────────────────────

    def generate_auth_url(self) -> Tuple[str, str]:
        """Generate PKCE auth URL and return (url, state_nonce)."""
        verifier = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)
        state = secrets.token_urlsafe(32)
        redirect_uri = f"http://localhost:{self._port}/callback"

        # Store pending flow for later code exchange
        self._pending_flows[state] = {
            "code_verifier": verifier,
            "created_at": time.time(),
        }
        # Housekeep expired flows
        self._cleanup_pending_flows()

        params = urlencode({
            "code": "true",
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        })
        url = f"{ANTHROPIC_AUTH_URL}?{params}"
        logger.info("OAuth auth URL generated (state=%s…)", state[:8])
        return url, state

    # ── Code Exchange ────────────────────────────────────────────

    def exchange_code(self, code: str, state: str) -> bool:
        """Exchange authorization code for tokens. Returns True on success."""
        flow = self._pending_flows.pop(state, None)
        if not flow:
            logger.warning("OAuth exchange: unknown or expired state %s…", state[:8])
            return False

        age = time.time() - flow["created_at"]
        if age > PENDING_FLOW_TTL:
            logger.warning("OAuth exchange: flow expired (%.0fs old)", age)
            return False

        redirect_uri = f"http://localhost:{self._port}/callback"
        try:
            resp = requests.post(
                ANTHROPIC_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": flow["code_verifier"],
                    "state": state,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("OAuth token exchange failed: %s", e)
            return False

        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        expires_in = data.get("expires_in", ACCESS_TOKEN_TTL)

        if not access_token or not refresh_token:
            logger.error("OAuth exchange: missing tokens in response")
            return False

        self._store_tokens(access_token, refresh_token, expires_in)
        logger.info("OAuth tokens stored (expires_in=%ds)", expires_in)
        return True

    # ── Token Retrieval ──────────────────────────────────────────

    def get_valid_token(self) -> Optional[str]:
        """Return a valid access token, refreshing if near expiry. None if unavailable."""
        if not self._vault.exists(VAULT_ACCESS_TOKEN):
            return None

        expiry = self._get_expiry()
        remaining = expiry - time.time()

        if remaining <= 0:
            # Expired — must refresh
            logger.info("OAuth access token expired, refreshing…")
            if self._refresh_token():
                return self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")
            return None

        if remaining <= REFRESH_WINDOW:
            # Near expiry — proactive refresh
            logger.info("OAuth access token expiring in %.0fs, refreshing…", remaining)
            self._refresh_token()  # best-effort; return current if refresh fails

        return self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")

    # ── Token Status ─────────────────────────────────────────────

    def get_token_status(self) -> Dict[str, Any]:
        """Return token health for War Room display."""
        if not self._vault.exists(VAULT_ACCESS_TOKEN):
            return {"configured": False, "valid": False, "status": "not_configured"}

        expiry = self._get_expiry()
        remaining = expiry - time.time()

        if remaining <= 0:
            status = "expired"
            valid = False
        elif remaining <= REFRESH_WINDOW:
            status = "expiring"
            valid = True
        else:
            status = "active"
            valid = True

        return {
            "configured": True,
            "valid": valid,
            "status": status,
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry)),
            "expires_in_seconds": max(0, int(remaining)),
        }

    # ── Revoke ───────────────────────────────────────────────────

    def revoke(self) -> None:
        """Clear all stored OAuth tokens."""
        for key in (VAULT_ACCESS_TOKEN, VAULT_REFRESH_TOKEN, VAULT_TOKEN_EXPIRY):
            try:
                if self._vault.exists(key):
                    self._vault.delete(key)
            except Exception:
                pass
        _oauth_token_cache.pop("access_token", None)
        os.environ.pop(ENV_OAUTH_TOKEN, None)  # clean up legacy env if present
        logger.info("OAuth tokens revoked")

    # ── Background Refresh ───────────────────────────────────────

    def start_background_refresh(self) -> None:
        """Start daemon thread that proactively refreshes tokens.

        Also loads any existing valid token from vault into the env var
        so the provider can pick it up on startup.
        """
        # Hydrate in-memory cache from vault if tokens already exist (e.g. restart)
        token = self.get_valid_token()
        if token:
            _oauth_token_cache["access_token"] = token
            logger.info("OAuth token loaded from vault into cache (restart recovery)")

        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        self._stop_event.clear()
        self._refresh_thread = threading.Thread(
            target=self._background_refresh_loop,
            name="oauth-refresh",
            daemon=True,
        )
        self._refresh_thread.start()
        logger.info("OAuth background refresh thread started")

    def stop_background_refresh(self) -> None:
        """Stop the background refresh thread."""
        self._stop_event.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=10)
            self._refresh_thread = None
        logger.info("OAuth background refresh thread stopped")

    # ── Internal ─────────────────────────────────────────────────

    def _store_tokens(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        """Atomically store all three token fields in the vault."""
        expiry_ts = str(int(time.time() + expires_in))
        self._vault.store(VAULT_ACCESS_TOKEN, access_token, type="oauth_token")
        self._vault.store(VAULT_REFRESH_TOKEN, refresh_token, type="oauth_token")
        self._vault.store(VAULT_TOKEN_EXPIRY, expiry_ts, type="metadata")
        # Update in-memory cache for FlagshipClient runtime access (F-009)
        _oauth_token_cache["access_token"] = access_token

    def _refresh_token(self) -> bool:
        """Refresh the access token using the single-use refresh token."""
        with self._lock:
            if not self._vault.exists(VAULT_REFRESH_TOKEN):
                return False

            current_refresh = self._vault.retrieve(
                VAULT_REFRESH_TOKEN, accessor_id=""
            )
            if not current_refresh:
                return False

            try:
                resp = requests.post(
                    ANTHROPIC_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": CLIENT_ID,
                        "refresh_token": current_refresh,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error("OAuth token refresh failed: %s", e)
                return False

            new_access = data.get("access_token", "")
            new_refresh = data.get("refresh_token", "")
            expires_in = data.get("expires_in", ACCESS_TOKEN_TTL)

            if not new_access or not new_refresh:
                logger.error("OAuth refresh: missing tokens in response")
                return False

            self._store_tokens(new_access, new_refresh, expires_in)
            logger.info("OAuth tokens refreshed (expires_in=%ds)", expires_in)
            return True

    def _get_expiry(self) -> float:
        """Get stored token expiry as epoch timestamp."""
        try:
            raw = self._vault.retrieve(VAULT_TOKEN_EXPIRY, accessor_id="")
            return float(raw)
        except Exception:
            return 0.0

    def _cleanup_pending_flows(self) -> None:
        """Remove pending PKCE flows older than PENDING_FLOW_TTL."""
        now = time.time()
        expired = [s for s, f in self._pending_flows.items()
                   if now - f["created_at"] > PENDING_FLOW_TTL]
        for s in expired:
            del self._pending_flows[s]

    def _background_refresh_loop(self) -> None:
        """Background thread: check and refresh tokens periodically."""
        while not self._stop_event.is_set():
            try:
                if self._vault.exists(VAULT_ACCESS_TOKEN):
                    expiry = self._get_expiry()
                    remaining = expiry - time.time()
                    if 0 < remaining <= REFRESH_WINDOW:
                        logger.info("Background refresh: token expiring in %.0fs", remaining)
                        self._refresh_token()
                    elif remaining <= 0 and self._vault.exists(VAULT_REFRESH_TOKEN):
                        logger.info("Background refresh: token expired, attempting refresh")
                        self._refresh_token()
            except Exception as e:
                logger.warning("Background refresh error: %s", e)

            self._stop_event.wait(BACKGROUND_CHECK_INTERVAL)


# ── Module Singleton ─────────────────────────────────────────────────

_oauth_manager: Optional[OAuthTokenManager] = None


def set_oauth_manager(manager: OAuthTokenManager) -> None:
    """Set the global OAuthTokenManager instance (called at gateway startup)."""
    global _oauth_manager
    _oauth_manager = manager


def get_oauth_manager() -> Optional[OAuthTokenManager]:
    """Get the global OAuthTokenManager instance."""
    return _oauth_manager
