# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Google OAuth 2.0 Token Manager — Authorization Code + PKCE flow,
vault-backed token storage, auto-refresh, and connector fan-out.

V26: Adds Google OAuth 2.0 as a built-in flow so users can enter
their Google Cloud Client ID + Secret and Lancelot handles the
consent redirect, code exchange, encrypted token storage, background
refresh, and fan-out to Gmail + Calendar connector vault keys.

Public API:
    GoogleOAuthManager(vault, port)
    manager.generate_auth_url(client_id, client_secret) -> str
    manager.exchange_code(code, state) -> bool
    manager.get_valid_token()          -> str | None
    manager.get_status()               -> dict
    manager.revoke()                   -> None
    manager.recover_from_vault()       -> bool
    manager.start_background_refresh()
    manager.stop_background_refresh()

Module-level helpers:
    get_google_oauth_manager()  -> GoogleOAuthManager | None
    set_google_oauth_manager(m) -> None
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# ── Google OAuth Constants ───────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Combined scopes for Gmail + Calendar
GOOGLE_SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
])

# Timing constants
PENDING_FLOW_TTL = 900               # PKCE flow timeout (15 min)
REFRESH_WINDOW = 300                 # refresh 5 min before expiry
BACKGROUND_CHECK_INTERVAL = 300      # background thread checks every 5 min
DEFAULT_TOKEN_TTL = 3600             # Google default (1 hour)

# Vault keys — canonical Google OAuth storage
VAULT_CLIENT_ID = "google.oauth.client_id"
VAULT_CLIENT_SECRET = "google.oauth.client_secret"
VAULT_ACCESS_TOKEN = "google.oauth.access_token"
VAULT_REFRESH_TOKEN = "google.oauth.refresh_token"
VAULT_TOKEN_EXPIRY = "google.oauth.token_expiry"

# Fan-out vault keys — connector-specific (read by ConnectorProxy)
VAULT_GMAIL_TOKEN = "email.gmail_token"
VAULT_CALENDAR_TOKEN = "calendar.google_token"


# ── PKCE Helpers ─────────────────────────────────────────────────

def _generate_code_verifier() -> str:
    """Generate a random code verifier (43-128 URL-safe chars)."""
    return secrets.token_urlsafe(64)[:128]


def _generate_code_challenge(verifier: str) -> str:
    """Compute S256 code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── GoogleOAuthManager ───────────────────────────────────────────

class GoogleOAuthManager:
    """Manages the full Google OAuth lifecycle: PKCE, exchange, vault storage, refresh, fan-out."""

    def __init__(self, vault: Any, port: int = 8000):
        self._vault = vault
        self._port = port
        self._pending_flows: Dict[str, Dict[str, Any]] = {}  # state -> {code_verifier, created_at}
        self._lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── Auth URL Generation ──────────────────────────────────────

    def generate_auth_url(self, client_id: str, client_secret: str) -> str:
        """Store client credentials in vault, generate PKCE auth URL, return consent URL."""
        # Store client credentials in vault (encrypted)
        self._vault.store(VAULT_CLIENT_ID, client_id, type="config")
        self._vault.store(VAULT_CLIENT_SECRET, client_secret, type="config")

        verifier = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)
        state = secrets.token_urlsafe(32)
        redirect_uri = f"http://localhost:{self._port}/google/callback"

        # Store pending flow for later code exchange
        self._pending_flows[state] = {
            "code_verifier": verifier,
            "created_at": time.time(),
        }
        # Housekeep expired flows
        self._cleanup_pending_flows()

        params = urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        })
        url = f"{GOOGLE_AUTH_URL}?{params}"
        logger.info("Google OAuth auth URL generated (state=%s…)", state[:8])
        return url

    # ── Code Exchange ────────────────────────────────────────────

    def exchange_code(self, code: str, state: str) -> bool:
        """Exchange authorization code for tokens. Returns True on success."""
        flow = self._pending_flows.pop(state, None)
        if not flow:
            logger.warning("Google OAuth exchange: unknown or expired state %s…", state[:8])
            return False

        age = time.time() - flow["created_at"]
        if age > PENDING_FLOW_TTL:
            logger.warning("Google OAuth exchange: flow expired (%.0fs old)", age)
            return False

        # Retrieve client credentials from vault
        client_id = self._vault.retrieve(VAULT_CLIENT_ID, accessor_id="")
        client_secret = self._vault.retrieve(VAULT_CLIENT_SECRET, accessor_id="")
        if not client_id or not client_secret:
            logger.error("Google OAuth exchange: client credentials not found in vault")
            return False

        redirect_uri = f"http://localhost:{self._port}/google/callback"
        try:
            resp = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "client_secret": client_secret,
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
            logger.error("Google OAuth token exchange failed: %s", e)
            return False

        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        expires_in = data.get("expires_in", DEFAULT_TOKEN_TTL)

        if not access_token:
            logger.error("Google OAuth exchange: missing access_token in response")
            return False

        self._store_tokens(access_token, refresh_token, expires_in)
        self.start_background_refresh()
        logger.info("Google OAuth tokens stored (expires_in=%ds)", expires_in)
        return True

    # ── Token Retrieval ──────────────────────────────────────────

    def get_valid_token(self) -> Optional[str]:
        """Return a valid access token, refreshing if near expiry. None if unavailable."""
        if not self._vault.exists(VAULT_ACCESS_TOKEN):
            return None

        expiry = self._get_expiry()
        remaining = expiry - time.time()

        if remaining <= 0:
            logger.info("Google OAuth access token expired, refreshing…")
            if self._refresh_token():
                return self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")
            return None

        if remaining <= REFRESH_WINDOW:
            logger.info("Google OAuth access token expiring in %.0fs, refreshing…", remaining)
            self._refresh_token()

        return self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")

    # ── Token Status ─────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Return token health for War Room display."""
        has_client_id = self._vault.exists(VAULT_CLIENT_ID)
        has_access = self._vault.exists(VAULT_ACCESS_TOKEN)
        has_refresh = self._vault.exists(VAULT_REFRESH_TOKEN)

        if not has_client_id:
            return {
                "configured": False,
                "valid": False,
                "status": "not_configured",
                "has_client_credentials": False,
                "has_access_token": False,
                "has_refresh_token": False,
                "scopes": GOOGLE_SCOPES.split(),
            }

        if not has_access:
            return {
                "configured": True,
                "valid": False,
                "status": "awaiting_authorization",
                "has_client_credentials": True,
                "has_access_token": False,
                "has_refresh_token": has_refresh,
                "scopes": GOOGLE_SCOPES.split(),
            }

        expiry = self._get_expiry()
        remaining = expiry - time.time()

        if remaining <= 0:
            status = "expired"
            valid = False
        elif remaining <= REFRESH_WINDOW:
            status = "expiring_soon"
            valid = True
        else:
            status = "healthy"
            valid = True

        return {
            "configured": True,
            "valid": valid,
            "status": status,
            "has_client_credentials": True,
            "has_access_token": True,
            "has_refresh_token": has_refresh,
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry)),
            "expires_in_seconds": max(0, int(remaining)),
            "scopes": GOOGLE_SCOPES.split(),
            "refresh_thread_alive": (
                self._refresh_thread is not None and self._refresh_thread.is_alive()
            ),
        }

    # ── Revoke ───────────────────────────────────────────────────

    def revoke(self) -> None:
        """Revoke tokens with Google and clear all stored OAuth data."""
        # Best-effort revocation call to Google
        access_token = None
        try:
            if self._vault.exists(VAULT_ACCESS_TOKEN):
                access_token = self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")
        except Exception:
            pass

        if access_token:
            try:
                requests.post(
                    GOOGLE_REVOKE_URL,
                    data={"token": access_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
            except Exception as e:
                logger.warning("Google token revocation call failed: %s", e)

        # Clear all vault keys
        for key in (
            VAULT_ACCESS_TOKEN, VAULT_REFRESH_TOKEN, VAULT_TOKEN_EXPIRY,
            VAULT_CLIENT_ID, VAULT_CLIENT_SECRET,
            VAULT_GMAIL_TOKEN, VAULT_CALENDAR_TOKEN,
        ):
            try:
                if self._vault.exists(key):
                    self._vault.delete(key)
            except Exception:
                pass

        logger.info("Google OAuth tokens revoked and vault cleared")

    # ── Startup Recovery ─────────────────────────────────────────

    def recover_from_vault(self) -> bool:
        """Called at startup. If tokens exist in vault, validate and start refresh.

        Returns True if valid tokens were recovered or refreshed.
        """
        if not self._vault.exists(VAULT_ACCESS_TOKEN):
            return False
        if not self._vault.exists(VAULT_REFRESH_TOKEN):
            return False

        expiry = self._get_expiry()
        remaining = expiry - time.time()

        if remaining <= 0:
            # Expired — attempt refresh
            logger.info("Google OAuth: recovered tokens expired, refreshing…")
            if not self._refresh_token():
                logger.warning("Google OAuth: refresh failed during recovery")
                return False

        # Ensure fan-out keys are populated
        token = self._vault.retrieve(VAULT_ACCESS_TOKEN, accessor_id="")
        if token:
            self._vault.store(VAULT_GMAIL_TOKEN, token, type="oauth_token")
            self._vault.store(VAULT_CALENDAR_TOKEN, token, type="oauth_token")

        self.start_background_refresh()
        logger.info("Google OAuth tokens recovered from vault")
        return True

    # ── Background Refresh ───────────────────────────────────────

    def start_background_refresh(self) -> None:
        """Start daemon thread that proactively refreshes tokens."""
        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        self._stop_event.clear()
        self._refresh_thread = threading.Thread(
            target=self._background_refresh_loop,
            name="google-oauth-refresh",
            daemon=True,
        )
        self._refresh_thread.start()
        logger.info("Google OAuth background refresh thread started")

    def stop_background_refresh(self) -> None:
        """Stop the background refresh thread."""
        self._stop_event.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=10)
            self._refresh_thread = None
        logger.info("Google OAuth background refresh thread stopped")

    # ── Internal ─────────────────────────────────────────────────

    def _store_tokens(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        """Atomically store tokens in the vault and fan out to connector keys."""
        expiry_ts = str(int(time.time() + expires_in))

        # Canonical Google OAuth keys
        self._vault.store(VAULT_ACCESS_TOKEN, access_token, type="oauth_token")
        if refresh_token:
            self._vault.store(VAULT_REFRESH_TOKEN, refresh_token, type="oauth_token")
        self._vault.store(VAULT_TOKEN_EXPIRY, expiry_ts, type="metadata")

        # Fan-out: write to connector-specific vault keys so ConnectorProxy
        # injects Bearer tokens automatically for both Gmail and Calendar.
        self._vault.store(VAULT_GMAIL_TOKEN, access_token, type="oauth_token")
        self._vault.store(VAULT_CALENDAR_TOKEN, access_token, type="oauth_token")

    def _refresh_token(self) -> bool:
        """Refresh the access token using the refresh token. Thread-safe."""
        with self._lock:
            if not self._vault.exists(VAULT_REFRESH_TOKEN):
                return False

            current_refresh = self._vault.retrieve(VAULT_REFRESH_TOKEN, accessor_id="")
            client_id = self._vault.retrieve(VAULT_CLIENT_ID, accessor_id="")
            client_secret = self._vault.retrieve(VAULT_CLIENT_SECRET, accessor_id="")

            if not current_refresh or not client_id or not client_secret:
                return False

            try:
                resp = requests.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": current_refresh,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error("Google OAuth token refresh failed: %s", e)
                return False

            new_access = data.get("access_token", "")
            # Google may or may not return a new refresh token
            new_refresh = data.get("refresh_token", "")
            expires_in = data.get("expires_in", DEFAULT_TOKEN_TTL)

            if not new_access:
                logger.error("Google OAuth refresh: missing access_token in response")
                return False

            self._store_tokens(new_access, new_refresh or current_refresh, expires_in)
            logger.info("Google OAuth tokens refreshed (expires_in=%ds)", expires_in)
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
                        logger.info("Google OAuth background refresh: token expiring in %.0fs", remaining)
                        self._refresh_token()
                    elif remaining <= 0 and self._vault.exists(VAULT_REFRESH_TOKEN):
                        logger.info("Google OAuth background refresh: token expired, attempting refresh")
                        self._refresh_token()
            except Exception as e:
                logger.warning("Google OAuth background refresh error: %s", e)

            self._stop_event.wait(BACKGROUND_CHECK_INTERVAL)


# ── Module Singleton ─────────────────────────────────────────────

_google_oauth_manager: Optional[GoogleOAuthManager] = None


def set_google_oauth_manager(manager: GoogleOAuthManager) -> None:
    """Set the global GoogleOAuthManager instance (called at gateway startup)."""
    global _google_oauth_manager
    _google_oauth_manager = manager


def get_google_oauth_manager() -> Optional[GoogleOAuthManager]:
    """Get the global GoogleOAuthManager instance."""
    return _google_oauth_manager
