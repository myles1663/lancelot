"""
Secret Cache — Vault-backed in-memory secret store.

Module-level singleton that replaces os.getenv() for sensitive credentials.
Secrets are loaded from the CredentialVault into a thread-safe in-memory cache
at boot, then scrubbed from os.environ to prevent /proc/PID/environ exposure.

Migration: If a vault key is missing but the corresponding env var exists,
the secret is auto-migrated into the vault (first-run / backward compat).

Public API:
    bootstrap(vault)     — Load secrets from vault; auto-migrate from env.
    get(key, default="") — Thread-safe cache lookup (drop-in os.getenv replacement).
    is_bootstrapped()    — Guard for fallback paths.
    scrub_environ()      — Remove migrated secrets from os.environ.
    reload(vault)        — Re-read vault into cache (for hot rotation).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Dict, Optional

logger = logging.getLogger("lancelot.secret_cache")

# ── Vault key mapping ────────────────────────────────────────────
# Maps environment variable names → vault key names.

_KEY_MAP: Dict[str, str] = {
    "LANCELOT_API_TOKEN": "system.api_token",
    "LANCELOT_OWNER_TOKEN": "system.owner_token",
    "WARROOM_USERNAME": "system.warroom_username",
    "WARROOM_PASSWORD": "system.warroom_password_hash",
    "LANCELOT_TELEGRAM_TOKEN": "system.telegram_token",
    "LANCELOT_TELEGRAM_CHAT_ID": "system.telegram_chat_id",
}

# Keys whose values are SHA-256 hashed on migration (not on vault read).
_HASH_ON_MIGRATE = {"WARROOM_PASSWORD"}

# ── Module state ─────────────────────────────────────────────────

_lock = threading.RLock()
_cache: Dict[str, str] = {}
_bootstrapped = False


def _hash_value(value: str) -> str:
    """SHA-256 hex digest of a plaintext value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bootstrap(vault) -> None:
    """Load secrets from vault into memory cache.

    For each mapped secret:
    1. Try vault first.
    2. If not in vault but in os.environ, migrate into vault (hash password).
    3. Cache the value for fast lookups.

    Args:
        vault: CredentialVault instance with store/retrieve/exists methods.
    """
    global _bootstrapped
    with _lock:
        migrated = []
        for env_key, vault_key in _KEY_MAP.items():
            try:
                if vault.exists(vault_key):
                    # Already in vault — load into cache.
                    _cache[env_key] = vault.retrieve(vault_key)
                elif os.environ.get(env_key):
                    # First-run migration: env → vault.
                    raw = os.environ[env_key]
                    if env_key in _HASH_ON_MIGRATE:
                        store_val = _hash_value(raw)
                    else:
                        store_val = raw
                    vault.store(vault_key, store_val, type="system_secret")
                    _cache[env_key] = store_val
                    migrated.append(env_key)
                # else: not in vault and not in env — skip (will return default)
            except Exception as exc:
                logger.warning("secret_cache: failed to load %s: %s", env_key, exc)

        _bootstrapped = True
        if migrated:
            logger.info("secret_cache: migrated %d secrets to vault: %s",
                        len(migrated), ", ".join(migrated))
        logger.info("secret_cache: bootstrapped with %d secrets cached", len(_cache))


def get(key: str, default: str = "") -> str:
    """Thread-safe cache lookup. Drop-in replacement for os.getenv().

    Falls back to os.getenv() if cache is not bootstrapped (feature flag off
    or vault init failed).
    """
    if not _bootstrapped:
        return os.getenv(key, default)
    with _lock:
        return _cache.get(key, default)


def is_bootstrapped() -> bool:
    """Return True if bootstrap() completed successfully."""
    return _bootstrapped


def scrub_environ() -> None:
    """Remove migrated secrets from os.environ.

    Call after bootstrap() to close the /proc/PID/environ exposure window.
    Only removes keys that are now in the cache.
    """
    removed = []
    with _lock:
        for env_key in _KEY_MAP:
            if env_key in os.environ and env_key in _cache:
                del os.environ[env_key]
                removed.append(env_key)
    if removed:
        logger.info("secret_cache: scrubbed %d secrets from os.environ: %s",
                     len(removed), ", ".join(removed))


def reload(vault) -> Dict[str, bool]:
    """Re-read all vault keys into cache. Returns dict of changed keys.

    Used for hot rotation (Phase 2). The dict maps env_key → True if
    the value changed, False if unchanged.
    """
    changed: Dict[str, bool] = {}
    with _lock:
        for env_key, vault_key in _KEY_MAP.items():
            try:
                if vault.exists(vault_key):
                    new_val = vault.retrieve(vault_key)
                    old_val = _cache.get(env_key)
                    _cache[env_key] = new_val
                    changed[env_key] = (new_val != old_val)
                else:
                    changed[env_key] = False
            except Exception as exc:
                logger.warning("secret_cache: reload failed for %s: %s", env_key, exc)
                changed[env_key] = False
    return changed


def _reset() -> None:
    """Reset module state. For testing only."""
    global _bootstrapped, _cache
    with _lock:
        _cache.clear()
        _bootstrapped = False
