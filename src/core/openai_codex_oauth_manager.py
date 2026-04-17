"""
OpenAI Codex OAuth Token Manager — PKCE flow, vault-backed token storage, auto-refresh.

Enables ChatGPT Plus/Pro subscription-based API access via the Codex OAuth
flow (same approach as OpenClaw / Codex CLI).  Tokens are stored in the
encrypted connector vault and refreshed proactively before expiry.

OAuth Flow:
    1. PKCE auth URL → https://auth.openai.com/oauth/authorize
    2. User signs in with ChatGPT account in browser
    3. Callback receives authorization code
    4. Exchange code → access_token + refresh_token at https://auth.openai.com/oauth/token
    5. API calls use Bearer token against chatgpt.com/backend-api/codex/*

Public API:
    OpenAICodexOAuthManager(vault, port)
    manager.generate_auth_url()     -> (auth_url, state_nonce)
    manager.exchange_code(code, state) -> bool
    manager.get_valid_token()       -> str | None
    manager.get_token_status()      -> dict
    manager.revoke()                -> None
    manager.start_background_refresh()
    manager.stop_background_refresh()

Module-level helpers:
    get_openai_codex_manager()  -> OpenAICodexOAuthManager | None
    set_openai_codex_manager(m) -> None

Copyright (c) 2026, Myles Russell Hamilton.
Licensed under BSL 1.0.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import time
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# ── OAuth Constants ──────────────────────────────────────────────────

OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # OpenAI public Codex client
# The public Codex OAuth client only accepts the standard identity + refresh scopes.
# Requesting API/model scopes causes the authorization request to be rejected.
SCOPES = "openid profile email offline_access"

ACCESS_TOKEN_TTL = 3600              # 1 hour (Codex default)
REFRESH_WINDOW = 300                 # refresh 5 min before expiry
PENDING_FLOW_TTL = 600               # PKCE flow timeout (10 min)
BACKGROUND_CHECK_INTERVAL = 120      # background thread checks every 2 min

# Callback port — must match OpenAI's registered redirect for the Codex public client.
# The Codex CLI uses localhost:1455/auth/callback; OpenAI rejects other redirect URIs.
DEFAULT_CALLBACK_PORT = 1455

# Vault keys for persistent token storage
VAULT_ACCESS_TOKEN = "openai.codex.access_token"
VAULT_REFRESH_TOKEN = "openai.codex.refresh_token"
VAULT_TOKEN_EXPIRY = "openai.codex.token_expiry"
VAULT_ACCOUNT_ID = "openai.codex.account_id"

# In-memory token cache (replaces os.environ for security — F-009)
_codex_token_cache: Dict[str, str] = {}


def get_codex_oauth_token() -> Optional[str]:
    """Retrieve the current Codex OAuth access token from the in-memory cache.

    Used by OpenAIProviderClient and gateway instead of os.environ.
    """
    return _codex_token_cache.get("access_token")


# ── PKCE Helpers ─────────────────────────────────────────────────────

def _generate_code_verifier() -> str:
    """Generate a random code verifier (43-128 URL-safe chars)."""
    return secrets.token_urlsafe(64)[:128]


def _generate_code_challenge(verifier: str) -> str:
    """Compute S256 code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── OpenAICodexOAuthManager ─────────────────────────────────────────

class OpenAICodexOAuthManager:
    """Manages the full OpenAI Codex OAuth lifecycle: PKCE, exchange, vault storage, refresh."""

    def __init__(self, vault: Any, port: int = DEFAULT_CALLBACK_PORT):
        self._vault = vault
        self._port = port
        self._pending_flows: Dict[str, Dict[str, Any]] = {}  # state -> {code_verifier, created_at}
        self._lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state_file = Path(
            os.getenv("LANCELOT_CODEX_OAUTH_STATE_FILE", "/home/lancelot/data/codex_oauth_pending.json")
        )
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_pending_flows()

    # ── Auth URL Generation ──────────────────────────────────────

    def generate_auth_url(self) -> Tuple[str, str]:
        """Generate PKCE auth URL and return (url, state_nonce)."""
        verifier = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)
        state = secrets.token_urlsafe(32)
        redirect_uri = f"http://localhost:{self._port}/auth/callback"

        # Store pending flow for later code exchange
        self._pending_flows[state] = {
            "code_verifier": verifier,
            "created_at": time.time(),
        }
        # Housekeep expired flows
        self._cleanup_pending_flows()
        self._save_pending_flows()

        params = urlencode({
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        })
        url = f"{OPENAI_AUTH_URL}?{params}"
        logger.info("OpenAI Codex OAuth auth URL generated (state=%s…)", state[:8])
        return url, state

    # ── Code Exchange ────────────────────────────────────────────

    def exchange_code(self, code: str, state: str) -> bool:
        """Exchange authorization code for tokens. Returns True on success."""
        flow = self._pending_flows.pop(state, None)
        self._save_pending_flows()
        if not flow:
            logger.warning("Codex OAuth exchange: unknown or expired state %s…", state[:8])
            return False

        age = time.time() - flow["created_at"]
        if age > PENDING_FLOW_TTL:
            logger.warning("Codex OAuth exchange: flow expired (%.0fs old)", age)
            return False

        redirect_uri = f"http://localhost:{self._port}/auth/callback"
        try:
            resp = requests.post(
                OPENAI_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": flow["code_verifier"],
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("Codex OAuth token exchange failed: %s", e)
            return False

        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        expires_in = data.get("expires_in", ACCESS_TOKEN_TTL)

        if not access_token:
            logger.error("Codex OAuth exchange: missing access_token in response")
            return False

        # Extract accountId from token if present (JWT sub claim)
        account_id = self._extract_account_id(access_token)

        self._store_tokens(access_token, refresh_token, expires_in, account_id)
        logger.info("Codex OAuth tokens stored (expires_in=%ds, account=%s)", expires_in, account_id[:8] if account_id else "unknown")
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
            logger.info("Codex OAuth access token expired, refreshing…")
            if self._refresh_token():
                return self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")
            return None

        if remaining <= REFRESH_WINDOW:
            # Near expiry — proactive refresh
            logger.info("Codex OAuth access token expiring in %.0fs, refreshing…", remaining)
            self._refresh_token()  # best-effort; return current if refresh fails

        return self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")

    # ── Token Status ─────────────────────────────────────────────

    def get_token_status(self) -> Dict[str, Any]:
        """Return token health for War Room display."""
        if not self._vault.exists(VAULT_ACCESS_TOKEN):
            return {"configured": False, "valid": False, "status": "not_configured", "provider": "openai-codex"}

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

        result = {
            "configured": True,
            "valid": valid,
            "status": status,
            "provider": "openai-codex",
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry)),
            "expires_in_seconds": max(0, int(remaining)),
        }

        # Include account ID if available
        try:
            if self._vault.exists(VAULT_ACCOUNT_ID):
                result["account_id"] = self._vault.retrieve(VAULT_ACCOUNT_ID, accessor_id="")
        except Exception:
            pass

        return result

    # ── Revoke ───────────────────────────────────────────────────

    def revoke(self) -> None:
        """Clear all stored Codex OAuth tokens."""
        for key in (VAULT_ACCESS_TOKEN, VAULT_REFRESH_TOKEN, VAULT_TOKEN_EXPIRY, VAULT_ACCOUNT_ID):
            try:
                if self._vault.exists(key):
                    self._vault.delete(key)
            except Exception:
                pass
        _codex_token_cache.pop("access_token", None)
        logger.info("Codex OAuth tokens revoked")

    # ── Background Refresh ───────────────────────────────────────

    def start_background_refresh(self) -> None:
        """Start daemon thread that proactively refreshes tokens.

        Also loads any existing valid token from vault into the in-memory
        cache so the provider can pick it up on startup.
        """
        # Hydrate in-memory cache from vault if tokens already exist (e.g. restart)
        token = self.get_valid_token()
        if token:
            _codex_token_cache["access_token"] = token
            logger.info("Codex OAuth token loaded from vault into cache (restart recovery)")

        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        self._stop_event.clear()
        self._refresh_thread = threading.Thread(
            target=self._background_refresh_loop,
            name="codex-oauth-refresh",
            daemon=True,
        )
        self._refresh_thread.start()
        logger.info("Codex OAuth background refresh thread started")

    def stop_background_refresh(self) -> None:
        """Stop the background refresh thread."""
        self._stop_event.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=10)
            self._refresh_thread = None
        logger.info("Codex OAuth background refresh thread stopped")

    # ── Internal ─────────────────────────────────────────────────

    def _store_tokens(self, access_token: str, refresh_token: str, expires_in: int, account_id: str = "") -> None:
        """Atomically store all token fields in the vault."""
        expiry_ts = str(int(time.time() + expires_in))
        self._vault.store(VAULT_ACCESS_TOKEN, access_token, type="oauth_token")
        if refresh_token:
            self._vault.store(VAULT_REFRESH_TOKEN, refresh_token, type="oauth_token")
        self._vault.store(VAULT_TOKEN_EXPIRY, expiry_ts, type="metadata")
        if account_id:
            self._vault.store(VAULT_ACCOUNT_ID, account_id, type="metadata")
        # Update in-memory cache for runtime access (F-009)
        _codex_token_cache["access_token"] = access_token

    def _refresh_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        with self._lock:
            if not self._vault.exists(VAULT_REFRESH_TOKEN):
                logger.warning("Codex OAuth refresh: no refresh token stored")
                return False

            current_refresh = self._vault.retrieve(
                VAULT_REFRESH_TOKEN, accessor_id=""
            )
            if not current_refresh:
                return False

            try:
                resp = requests.post(
                    OPENAI_TOKEN_URL,
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
                logger.error("Codex OAuth token refresh failed: %s", e)
                return False

            new_access = data.get("access_token", "")
            new_refresh = data.get("refresh_token", current_refresh)  # some flows reuse refresh token
            expires_in = data.get("expires_in", ACCESS_TOKEN_TTL)

            if not new_access:
                logger.error("Codex OAuth refresh: missing access_token in response")
                return False

            self._store_tokens(new_access, new_refresh, expires_in)
            logger.info("Codex OAuth tokens refreshed (expires_in=%ds)", expires_in)
            return True

    def _get_expiry(self) -> float:
        """Get stored token expiry as epoch timestamp."""
        try:
            raw = self._vault.retrieve(VAULT_TOKEN_EXPIRY, accessor_id="")
            return float(raw)
        except Exception:
            return 0.0

    def _extract_account_id(self, access_token: str) -> str:
        """Extract accountId from a JWT access token (best-effort, no verification)."""
        try:
            # JWT has 3 parts separated by dots; payload is the second
            parts = access_token.split(".")
            if len(parts) >= 2:
                payload = parts[1]
                # Add padding
                padding = 4 - len(payload) % 4
                if padding != 4:
                    payload += "=" * padding
                decoded = base64.urlsafe_b64decode(payload)
                import json
                claims = json.loads(decoded)
                return claims.get("sub", "") or claims.get("account_id", "")
        except Exception:
            pass
        return ""

    def _cleanup_pending_flows(self) -> None:
        """Remove pending PKCE flows older than PENDING_FLOW_TTL."""
        now = time.time()
        expired = [s for s, f in self._pending_flows.items()
                   if now - f["created_at"] > PENDING_FLOW_TTL]
        for s in expired:
            del self._pending_flows[s]

    def _load_pending_flows(self) -> None:
        if not self._state_file.exists():
            return
        try:
            raw = self._state_file.read_text(encoding="utf-8").strip()
            if not raw:
                self._pending_flows = {}
                return
            data = json.loads(raw)
            if isinstance(data, dict):
                self._pending_flows = {
                    state: flow for state, flow in data.items() if isinstance(flow, dict)
                }
                self._cleanup_pending_flows()
                self._save_pending_flows()
        except Exception as exc:
            logger.warning("Failed to load Codex OAuth pending state: %s", exc)
            self._pending_flows = {}

    def _save_pending_flows(self) -> None:
        try:
            self._state_file.write_text(
                json.dumps(self._pending_flows, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to persist Codex OAuth pending state: %s", exc)

    def _background_refresh_loop(self) -> None:
        """Background thread: check and refresh tokens periodically."""
        while not self._stop_event.is_set():
            try:
                if self._vault.exists(VAULT_ACCESS_TOKEN):
                    expiry = self._get_expiry()
                    remaining = expiry - time.time()
                    if 0 < remaining <= REFRESH_WINDOW:
                        logger.info("Codex background refresh: token expiring in %.0fs", remaining)
                        self._refresh_token()
                    elif remaining <= 0 and self._vault.exists(VAULT_REFRESH_TOKEN):
                        logger.info("Codex background refresh: token expired, attempting refresh")
                        self._refresh_token()
            except Exception as e:
                logger.warning("Codex background refresh error: %s", e)

            self._stop_event.wait(BACKGROUND_CHECK_INTERVAL)


# ── Module Singleton ─────────────────────────────────────────────────

_codex_manager: Optional[OpenAICodexOAuthManager] = None


def set_openai_codex_manager(manager: OpenAICodexOAuthManager) -> None:
    """Set the global OpenAICodexOAuthManager instance (called at gateway startup)."""
    global _codex_manager
    _codex_manager = manager


def get_openai_codex_manager() -> Optional[OpenAICodexOAuthManager]:
    """Get the global OpenAICodexOAuthManager instance."""
    return _codex_manager
