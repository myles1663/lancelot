"""
Credential Vault — Encrypted credential storage for connectors.

Credentials are encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
and stored on disk. The encryption key is resolved in priority order:

1. Docker secret file (/run/secrets/<name>)
2. Environment variable (LANCELOT_VAULT_KEY)
3. Passphrase → PBKDF2-derived Fernet key (if value is not valid Fernet)
By default the vault fails closed if no key is configured. A development-only
ephemeral fallback can be enabled explicitly with
`LANCELOT_ALLOW_EPHEMERAL_VAULT=true`.
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
_VAULT_METADATA_FILE = "credentials.meta.json"
_VAULT_RESET_BACKUPS_DIR = "reset_backups"


class VaultConfigurationError(RuntimeError):
    """Raised when the vault cannot be initialized securely."""


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


@dataclass
class VaultHealthSnapshot:
    """Non-secret credential-vault diagnostics for operator recovery flows."""

    status: str
    message: str
    available: bool
    key_configured: bool
    key_source: str
    key_origin: str
    key_id: Optional[str]
    metadata_present: bool
    metadata_key_id: Optional[str]
    suspected_key_mismatch: bool
    has_primary: bool
    has_backup: bool
    primary_path: str
    backup_path: str
    metadata_path: str
    reset_backups_path: str
    entry_count: int = 0
    primary_last_modified: Optional[str] = None
    backup_last_modified: Optional[str] = None
    metadata_last_modified: Optional[str] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "available": self.available,
            "key_configured": self.key_configured,
            "key_source": self.key_source,
            "key_origin": self.key_origin,
            "key_id": self.key_id,
            "metadata_present": self.metadata_present,
            "metadata_key_id": self.metadata_key_id,
            "suspected_key_mismatch": self.suspected_key_mismatch,
            "has_primary": self.has_primary,
            "has_backup": self.has_backup,
            "primary_path": self.primary_path,
            "backup_path": self.backup_path,
            "metadata_path": self.metadata_path,
            "reset_backups_path": self.reset_backups_path,
            "entry_count": self.entry_count,
            "primary_last_modified": self.primary_last_modified,
            "backup_last_modified": self.backup_last_modified,
            "metadata_last_modified": self.metadata_last_modified,
            "last_error": self.last_error,
        }


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
        self._config_path = config_path

        # Resolve paths
        (
            self._storage_path,
            self._backup_path,
            self._metadata_path,
            self._reset_backups_path,
        ) = self._resolve_storage_paths(self._config)

        # Audit config
        audit = self._config.get("audit", {})
        self._audit_enabled = audit.get("log_access", True)
        self._audit_path = Path(audit.get("log_path", "data/vault/access.log"))

        # Encryption key — resolved in priority order:
        # 1. Docker secret file  2. Env var  3. Passphrase→PBKDF2
        enc = self._config.get("encryption", {})
        key_env_var = enc.get("key_env_var", "LANCELOT_VAULT_KEY")
        docker_secret_name = enc.get("docker_secret", "lancelot_vault_key")

        key_str, self._key_origin = self._resolve_key_with_origin(key_env_var, docker_secret_name)
        self._key_source: str = "unknown"
        self._key_id: Optional[str] = self._fingerprint_key_material(key_str) if key_str else None
        self._last_error: Optional[str] = None

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
            if self._ephemeral_override_enabled():
                new_key = Fernet.generate_key()
                self._cipher = Fernet(new_key)
                self._key_source = "ephemeral"
                logger.warning(
                    "Vault key missing and LANCELOT_ALLOW_EPHEMERAL_VAULT=true — "
                    "generated ephemeral key for development mode only. "
                    "Credentials will NOT survive restarts."
                )
            else:
                raise VaultConfigurationError(
                    "Credential vault initialization failed: no vault key configured. "
                    "Set LANCELOT_VAULT_KEY (or the configured Docker secret), or set "
                    "LANCELOT_ALLOW_EPHEMERAL_VAULT=true only for explicit development use."
                )

        # Access policy
        self._access_policy = VaultAccessPolicy()

        # Load existing credentials
        self._load()
        self._sync_metadata()

    @staticmethod
    def _load_config(config_path: str) -> Dict[str, Any]:
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    @staticmethod
    def _resolve_storage_paths(config: Dict[str, Any]) -> tuple[Path, Path, Path, Path]:
        storage = config.get("storage", {})
        storage_path = Path(storage.get("path", "data/vault/credentials.enc"))
        backup_path = Path(storage.get("backup_path", "data/vault/credentials.enc.bak"))
        metadata_path = storage_path.parent / _VAULT_METADATA_FILE
        reset_backups_path = storage_path.parent / _VAULT_RESET_BACKUPS_DIR
        return storage_path, backup_path, metadata_path, reset_backups_path

    @staticmethod
    def _resolve_key(env_var: str, docker_secret_name: str) -> str:
        key, _origin = CredentialVault._resolve_key_with_origin(env_var, docker_secret_name)
        return key

    @staticmethod
    def _resolve_key_with_origin(env_var: str, docker_secret_name: str) -> tuple[str, str]:
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
                    logger.debug("Vault key loaded from Docker secret: %s", secret_path)
                    return key, "docker_secret"
            except Exception as exc:
                logger.warning("Failed to read Docker secret %s: %s", secret_path, exc)

        # 2. Environment variable
        key = os.environ.get(env_var, "")
        if key:
            return key, "environment"

        return "", "unconfigured"

    @staticmethod
    def _fingerprint_key_material(key_material: str) -> str:
        return hashlib.sha256(key_material.encode("utf-8")).hexdigest()

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

    @staticmethod
    def _ephemeral_override_enabled() -> bool:
        return os.environ.get("LANCELOT_ALLOW_EPHEMERAL_VAULT", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @property
    def key_source(self) -> str:
        """How the encryption key was resolved: 'fernet', 'pbkdf2', or 'ephemeral'."""
        return self._key_source

    @property
    def key_origin(self) -> str:
        """Where the vault key came from: docker_secret, environment, or unconfigured."""
        return self._key_origin

    @property
    def key_id(self) -> Optional[str]:
        """Stable, non-secret fingerprint of the configured vault key material."""
        return self._key_id

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def health_snapshot(self, last_error: str | None = None) -> VaultHealthSnapshot:
        """Return non-secret health details for the active vault instance."""
        metadata = self._read_metadata(self._metadata_path)
        snapshot = self.inspect_health(config_path=self._config_path, last_error=last_error or self._last_error)
        snapshot.status = "ready"
        snapshot.message = "Connector vault initialized."
        snapshot.available = True
        snapshot.entry_count = len(self._entries)
        if metadata:
            snapshot.metadata_present = True
            snapshot.metadata_key_id = metadata.get("key_id")
        return snapshot

    @classmethod
    def inspect_health(
        cls,
        config_path: str = "config/vault.yaml",
        *,
        last_error: str | None = None,
    ) -> VaultHealthSnapshot:
        """Inspect vault state without decrypting any stored credentials."""
        config = cls._load_config(config_path)
        storage_path, backup_path, metadata_path, reset_backups_path = cls._resolve_storage_paths(config)
        enc = config.get("encryption", {})
        key_env_var = enc.get("key_env_var", "LANCELOT_VAULT_KEY")
        docker_secret_name = enc.get("docker_secret", "lancelot_vault_key")
        key_str, key_origin = cls._resolve_key_with_origin(key_env_var, docker_secret_name)
        key_configured = bool(key_str)
        key_source = "unknown"
        key_id = None
        if key_str:
            key_id = cls._fingerprint_key_material(key_str)
            key_source = "fernet" if cls._is_valid_fernet_key(key_str) else "pbkdf2"
        elif cls._ephemeral_override_enabled():
            key_source = "ephemeral"

        metadata = cls._read_metadata(metadata_path)
        metadata_present = metadata is not None
        metadata_key_id = metadata.get("key_id") if metadata else None
        has_primary = storage_path.exists()
        has_backup = backup_path.exists()
        suspected_key_mismatch = bool(
            key_id and metadata_key_id and key_id != metadata_key_id
        )

        status = "empty"
        message = "Connector vault is configured and empty."
        if key_source == "ephemeral":
            status = "ephemeral_key"
            message = "Connector vault is running with an explicit ephemeral development key."
        elif not key_configured:
            status = "missing_key"
            message = "No connector vault key is configured."
        elif suspected_key_mismatch:
            status = "key_mismatch"
            message = "Stored vault metadata indicates the configured key does not match the encrypted vault."
        elif has_primary or has_backup:
            if last_error:
                if "could not be decrypted" in last_error.lower():
                    status = "decryption_failed"
                    message = "Encrypted connector vault contents could not be decrypted."
                else:
                    status = "unavailable"
                    message = "Connector vault is unavailable."
            else:
                status = "configured"
                message = "Encrypted connector vault data is present."

        return VaultHealthSnapshot(
            status=status,
            message=message,
            available=False,
            key_configured=key_configured,
            key_source=key_source,
            key_origin=key_origin,
            key_id=key_id,
            metadata_present=metadata_present,
            metadata_key_id=metadata_key_id,
            suspected_key_mismatch=suspected_key_mismatch,
            has_primary=has_primary,
            has_backup=has_backup,
            primary_path=str(storage_path),
            backup_path=str(backup_path),
            metadata_path=str(metadata_path),
            reset_backups_path=str(reset_backups_path),
            primary_last_modified=cls._iso_mtime(storage_path),
            backup_last_modified=cls._iso_mtime(backup_path),
            metadata_last_modified=cls._iso_mtime(metadata_path),
            last_error=last_error,
        )

    @classmethod
    def reset_storage(cls, config_path: str = "config/vault.yaml") -> Dict[str, Any]:
        """Archive the current vault artifacts so a clean vault can be re-created."""
        config = cls._load_config(config_path)
        storage_path, backup_path, metadata_path, reset_backups_path = cls._resolve_storage_paths(config)
        salt_path = storage_path.parent / _PBKDF2_SALT_FILE
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_dir = reset_backups_path / timestamp
        archived_files: List[str] = []

        for path in (storage_path, backup_path, metadata_path, salt_path):
            if not path.exists():
                continue
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / path.name
            shutil.move(str(path), str(target))
            archived_files.append(str(target))

        return {
            "archive_dir": str(archive_dir),
            "archived_files": archived_files,
            "storage_path": str(storage_path),
            "backup_path": str(backup_path),
            "metadata_path": str(metadata_path),
        }

    @staticmethod
    def _iso_mtime(path: Path) -> Optional[str]:
        if not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()

    @staticmethod
    def _read_metadata(metadata_path: Path) -> Optional[Dict[str, Any]]:
        if not metadata_path.exists():
            return None
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read vault metadata %s: %s", metadata_path, exc)
            return None

    def _sync_metadata(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            self._write_metadata()
        except Exception as exc:
            logger.warning("Failed to sync vault metadata %s: %s", self._metadata_path, exc)

    def _write_metadata(self) -> None:
        if not self._key_id or self._key_source == "ephemeral":
            return
        metadata = {
            "version": 1,
            "key_id": self._key_id,
            "key_source": self._key_source,
            "key_origin": self._key_origin,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entry_count": len(self._entries),
            "storage_path": str(self._storage_path),
            "backup_path": str(self._backup_path),
        }
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._metadata_path.with_name(f"{self._metadata_path.name}.tmp")
        try:
            tmp_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp_path, self._metadata_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError as exc:
                    logger.debug("Failed to remove temporary vault metadata %s: %s", tmp_path, exc)

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

    def get_entry_type(self, key: str) -> str:
        """Return the stored credential type for a vault key."""
        entry = self._entries.get(key)
        if entry is None:
            raise KeyError(f"Credential '{key}' not found in vault")
        return entry.type

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

        # Serialize → encrypt → write
        plaintext = json.dumps(
            {k: v.to_dict() for k, v in self._entries.items()}
        ).encode("utf-8")
        encrypted = self._cipher.encrypt(plaintext)
        tmp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")

        try:
            with open(tmp_path, "wb") as f:
                f.write(encrypted)

            # Backup the last known-good primary before swapping in the new file.
            if self._storage_path.exists():
                shutil.copy2(self._storage_path, self._backup_path)

            os.replace(tmp_path, self._storage_path)
            try:
                self._write_metadata()
            except Exception as exc:
                logger.warning("Failed to update vault metadata %s: %s", self._metadata_path, exc)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError as exc:
                    logger.debug("Failed to remove temporary vault file %s: %s", tmp_path, exc)

    def _load(self) -> None:
        """Load and decrypt existing credentials from disk."""
        if not self._storage_path.exists():
            return
        primary_error: Exception | None = None
        try:
            self._entries = self._decrypt_entries(self._storage_path)
            self._last_error = None
            return
        except Exception as exc:
            primary_error = exc
            self._last_error = str(exc)
            logger.error("Failed to load vault from %s: %s", self._storage_path, primary_error)

        if self._backup_path.exists():
            try:
                self._entries = self._decrypt_entries(self._backup_path)
                self._restore_primary_from_backup()
                self._last_error = None
                logger.warning(
                    "Recovered vault state from backup %s after primary load failure",
                    self._backup_path,
                )
                return
            except Exception as backup_error:
                logger.error(
                    "Failed to load vault backup %s: %s",
                    self._backup_path,
                    backup_error,
                )
                raise VaultConfigurationError(
                    "Credential vault initialization failed: encrypted vault contents could not be decrypted from the primary or backup file."
                ) from backup_error

        raise VaultConfigurationError(
            "Credential vault initialization failed: encrypted vault contents could not be decrypted."
        ) from primary_error

    def _decrypt_entries(self, path: Path) -> Dict[str, VaultEntry]:
        with open(path, "rb") as f:
            encrypted = f.read()
        plaintext = self._cipher.decrypt(encrypted)
        data = json.loads(plaintext.decode("utf-8"))
        return {k: VaultEntry.from_dict(v) for k, v in data.items()}

    def _restore_primary_from_backup(self) -> None:
        """Restore the primary vault file from the last known-good backup."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._backup_path, self._storage_path)

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
