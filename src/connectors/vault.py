"""
Credential Vault — Encrypted credential storage for connectors.

Credentials are encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
and stored on disk. The encryption key is resolved in priority order:

1. Docker secret file (/run/secrets/<name>)
2. Environment variable (LANCELOT_VAULT_KEY)
3. Passphrase → PBKDF2-derived Fernet key (if value is not valid Fernet)
4. Ephemeral generated key (warning — credentials won't survive restart)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from src.connectors.base import CredentialSpec

logger = logging.getLogger(__name__)

# ── PBKDF2 Constants ─────────────────────────────────────────────
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT_FILE = "vault_salt.bin"  # Stored alongside vault data


# ── Vault Entry ────────────────────────────────────────────────────

@dataclass
class VaultEntry:
    """A single credential stored in the vault."""
    key: str
    value: str
    type: str
    created_at: str
    updated_at: str
    accessed_by: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "type": self.type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "accessed_by": self.accessed_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VaultEntry:
        return cls(**data)


# ── Vault Access Policy ───────────────────────────────────────────

class VaultAccessPolicy:
    """Scoped access control for vault credentials.

    Each vault key can be granted to specific connector IDs.
    Connectors can only retrieve credentials they've been granted access to.
    """

    def __init__(self) -> None:
        self._grants: Dict[str, set] = {}  # vault_key → set of connector_ids

    def grant(self, connector_id: str, vault_key: str) -> None:
        """Grant a connector access to a vault key."""
        if vault_key not in self._grants:
            self._grants[vault_key] = set()
        self._grants[vault_key].add(connector_id)

    def revoke(self, connector_id: str, vault_key: str) -> None:
        """Revoke a specific connector's access to a vault key."""
        if vault_key in self._grants:
            self._grants[vault_key].discard(connector_id)

    def revoke_all(self, connector_id: str) -> None:
        """Revoke all access for a connector."""
        for key_grants in self._grants.values():
            key_grants.discard(connector_id)

    def is_allowed(self, connector_id: str, vault_key: str) -> bool:
        """Check if a connector has access to a vault key."""
        return connector_id in self._grants.get(vault_key, set())

    def list_grants(self, connector_id: str) -> List[str]:
        """List all vault keys a connector has access to."""
        return [
            key for key, ids in self._grants.items()
            if connector_id in ids
        ]


# ── Credential Vault ──────────────────────────────────────────────

class CredentialVault:
    """Encrypted credential storage with audit logging.

    Credentials are stored encrypted on disk using Fernet symmetric
    encryption. The encryption key is read from an environment variable.
    """

    def __init__(self, config_path: str = "config/vault.yaml") -> None:
        self._config = self._load_config(config_path)
        self._entries: Dict[str, VaultEntry] = {}

        # Resolve paths
        storage = self._config.get("storage", {})
        self._storage_path = Path(storage.get("path", "data/vault/credentials.enc"))
        self._backup_path = Path(storage.get("backup_path", "data/vault/credentials.enc.bak"))

        # Audit config
        audit = self._config.get("audit", {})
        self._audit_enabled = audit.get("log_access", True)
        self._audit_path = Path(audit.get("log_path", "data/vault/access.log"))

        # Encryption key — resolved in priority order:
        # 1. Docker secret file  2. Env var  3. Passphrase→PBKDF2  4. Ephemeral
        enc = self._config.get("encryption", {})
        key_env_var = enc.get("key_env_var", "LANCELOT_VAULT_KEY")
        docker_secret_name = enc.get("docker_secret", "lancelot_vault_key")

        key_str = self._resolve_key(key_env_var, docker_secret_name)
        self._key_source: str = "unknown"

        if key_str:
            if self._is_valid_fernet_key(key_str):
                # Raw Fernet key (from env or Docker secret)
                self._cipher = Fernet(key_str.encode())
                self._key_source = "fernet"
            else:
                # Treat as passphrase — derive Fernet key via PBKDF2
                salt_dir = self._storage_path.parent
                derived_key = self._derive_key_from_passphrase(key_str, salt_dir)
                self._cipher = Fernet(derived_key)
                self._key_source = "pbkdf2"
                logger.info("Vault key derived from passphrase via PBKDF2 (600k iterations).")
        else:
            new_key = Fernet.generate_key()
            self._cipher = Fernet(new_key)
            self._key_source = "ephemeral"
            logger.warning(
                "LANCELOT_VAULT_KEY not set — generated ephemeral key. "
                "Credentials will NOT survive restarts without setting this env var."
            )

        # Access policy
        self._access_policy = VaultAccessPolicy()

        # Load existing credentials
        self._load()

    @staticmethod
    def _load_config(config_path: str) -> Dict[str, Any]:
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    @staticmethod
    def _resolve_key(env_var: str, docker_secret_name: str) -> str:
        """Resolve encryption key: Docker secret → env var.

        Docker secrets are mounted at /run/secrets/<name> by the Docker
        runtime. Reading from file avoids /proc/PID/environ exposure.
        """
        # 1. Docker secret file (highest priority)
        secret_path = Path(f"/run/secrets/{docker_secret_name}")
        if secret_path.exists():
            try:
                key = secret_path.read_text(encoding="utf-8").strip()
                if key:
                    logger.info("Vault key loaded from Docker secret: %s", secret_path)
                    return key
            except Exception as exc:
                logger.warning("Failed to read Docker secret %s: %s", secret_path, exc)

        # 2. Environment variable
        key = os.environ.get(env_var, "")
        if key:
            return key

        return ""

    @staticmethod
    def _is_valid_fernet_key(key_str: str) -> bool:
        """Check if a string is a valid Fernet key (44-char url-safe base64)."""
        try:
            decoded = base64.urlsafe_b64decode(key_str.encode())
            return len(decoded) == 32
        except Exception:
            return False

    @staticmethod
    def _derive_key_from_passphrase(passphrase: str, salt_dir: Path) -> bytes:
        """Derive a Fernet key from a human-memorable passphrase using PBKDF2.

        Salt is stored in <salt_dir>/vault_salt.bin. A new random salt is
        generated on first use and persisted for subsequent derivations.

        Returns:
            Fernet key bytes (url-safe base64 encoded).
        """
        salt_dir.mkdir(parents=True, exist_ok=True)
        salt_path = salt_dir / _PBKDF2_SALT_FILE

        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            salt_path.write_bytes(salt)
            logger.info("Generated new PBKDF2 salt at %s", salt_path)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        derived = kdf.derive(passphrase.encode("utf-8"))
        return base64.urlsafe_b64encode(derived)

    @property
    def key_source(self) -> str:
        """How the encryption key was resolved: 'fernet', 'pbkdf2', or 'ephemeral'."""
        return self._key_source

    def store(self, key: str, value: str, type: str = "api_key") -> VaultEntry:
        """Store or update a credential. Returns the VaultEntry."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self._entries.get(key)
        if existing:
            entry = VaultEntry(
                key=key,
                value=value,
                type=type,
                created_at=existing.created_at,
                updated_at=now,
                accessed_by=existing.accessed_by,
            )
        else:
            entry = VaultEntry(
                key=key,
                value=value,
                type=type,
                created_at=now,
                updated_at=now,
            )
        self._entries[key] = entry
        self._save()
        self._audit_log("store", key)
        return entry

    def retrieve(self, key: str, accessor_id: str = "") -> str:
        """Retrieve a decrypted credential value.

        If accessor_id is provided, access policy is checked.
        Empty accessor_id (admin access) bypasses policy.

        Raises:
            KeyError: If key not found in vault
            PermissionError: If accessor not granted access
        """
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"Credential '{key}' not found in vault")

        # Check access policy for non-admin access
        if accessor_id and not self._access_policy.is_allowed(accessor_id, key):
            raise PermissionError(
                f"Connector '{accessor_id}' is not granted access to '{key}'"
            )

        if accessor_id and accessor_id not in entry.accessed_by:
            entry.accessed_by.append(accessor_id)

        self._audit_log("retrieve", key, accessor=accessor_id)
        return entry.value

    def delete(self, key: str) -> bool:
        """Delete a credential. Returns True if found and deleted."""
        if key not in self._entries:
            return False
        del self._entries[key]
        self._save()
        self._audit_log("delete", key)
        return True

    def exists(self, key: str) -> bool:
        """Check if a credential exists."""
        return key in self._entries

    def list_keys(self) -> List[str]:
        """Return all credential keys (not values)."""
        return list(self._entries.keys())

    @property
    def access_policy(self) -> VaultAccessPolicy:
        """Access the vault's access policy."""
        return self._access_policy

    def grant_connector_access(self, connector_id: str, manifest: "ConnectorManifest") -> None:
        """Grant a connector access to all credentials declared in its manifest."""
        from src.connectors.base import ConnectorManifest  # noqa: avoid circular
        for spec in manifest.required_credentials:
            self._access_policy.grant(connector_id, spec.vault_key)

    def revoke_connector_access(self, connector_id: str) -> None:
        """Revoke all vault access for a connector."""
        self._access_policy.revoke_all(connector_id)

    def check_requirements(self, specs: List[CredentialSpec]) -> Dict[str, bool]:
        """Check which required credentials exist in the vault.

        Returns a dict mapping vault_key → exists.
        """
        return {spec.vault_key: self.exists(spec.vault_key) for spec in specs}

    def _save(self) -> None:
        """Encrypt and persist all entries to disk."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Backup existing file first
        if self._storage_path.exists():
            shutil.copy2(self._storage_path, self._backup_path)

        # Serialize → encrypt → write
        plaintext = json.dumps(
            {k: v.to_dict() for k, v in self._entries.items()}
        ).encode("utf-8")
        encrypted = self._cipher.encrypt(plaintext)

        with open(self._storage_path, "wb") as f:
            f.write(encrypted)

    def _load(self) -> None:
        """Load and decrypt existing credentials from disk."""
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "rb") as f:
                encrypted = f.read()
            plaintext = self._cipher.decrypt(encrypted)
            data = json.loads(plaintext.decode("utf-8"))
            self._entries = {
                k: VaultEntry.from_dict(v) for k, v in data.items()
            }
        except Exception as e:
            logger.error("Failed to load vault: %s", e)
            self._entries = {}

    def _audit_log(self, action: str, key: str, accessor: str = "") -> None:
        """Append an entry to the audit log."""
        if not self._audit_enabled:
            return
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).isoformat()
            line = f"{timestamp} | {action} | {key} | accessor={accessor}\n"
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.warning("Audit log write failed: %s", e)
