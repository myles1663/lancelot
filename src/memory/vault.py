import os
import sys
import json
from typing import Optional
from cryptography.fernet import Fernet

from security import AuditLogger


class SecretVault:
    """Encrypted secret storage for OAuth tokens and API keys."""

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self.data_dir = data_dir
        self.key_file = os.path.join(data_dir, "vault.key")
        self.secrets_file = os.path.join(data_dir, "vault_secrets.json")
        self.audit_logger = AuditLogger()
        self.fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        """Loads Fernet key from environment variable, existing file, or generates a new one.

        Priority:
        1. VAULT_ENCRYPTION_KEY env var (base64-encoded Fernet key)
        2. Existing key file on disk
        3. Generate new key and write to disk (with 0o600 permissions on Linux)
        """
        # Priority 1: Environment variable
        env_key = os.getenv("VAULT_ENCRYPTION_KEY")
        if env_key:
            return env_key.encode()

        # Priority 2: Existing key file
        if os.path.exists(self.key_file):
            print("WARNING: Loading vault key from file. "
                  "Set VAULT_ENCRYPTION_KEY env var for production use.")
            with open(self.key_file, "rb") as f:
                return f.read()

        # Priority 3: Generate new key
        print("WARNING: Generating new vault key and saving to file. "
              "Set VAULT_ENCRYPTION_KEY env var for production use.")
        key = Fernet.generate_key()
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.key_file, "wb") as f:
            f.write(key)

        # On Linux, restrict file permissions to owner-only
        if sys.platform.startswith("linux"):
            try:
                os.chmod(self.key_file, 0o600)
            except OSError:
                pass

        return key

    def _load_secrets(self) -> dict:
        """Loads the encrypted secrets file."""
        if not os.path.exists(self.secrets_file):
            return {}
        with open(self.secrets_file, "r") as f:
            return json.load(f)

    def _save_secrets(self, secrets: dict):
        """Saves the secrets file."""
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.secrets_file, "w") as f:
            json.dump(secrets, f, indent=2)

    def store(self, name: str, value: str) -> bool:
        """Encrypts and stores a secret."""
        try:
            encrypted = self.fernet.encrypt(value.encode()).decode()
            secrets = self._load_secrets()
            secrets[name] = encrypted
            self._save_secrets(secrets)
            self.audit_logger.log_event("VAULT_STORE", f"Secret stored: {name}")
            return True
        except Exception as e:
            print(f"Vault store error: {e}")
            return False

    def retrieve(self, name: str) -> Optional[str]:
        """Decrypts and returns a secret by name."""
        self.audit_logger.log_event("VAULT_RETRIEVE", f"Secret retrieved: {name}")
        secrets = self._load_secrets()
        encrypted = secrets.get(name)
        if encrypted is None:
            return None
        try:
            return self.fernet.decrypt(encrypted.encode()).decode()
        except Exception as e:
            print(f"Vault retrieve error: {e}")
            return None

    def delete(self, name: str) -> bool:
        """Removes a secret from the vault."""
        secrets = self._load_secrets()
        if name not in secrets:
            return False
        del secrets[name]
        self._save_secrets(secrets)
        self.audit_logger.log_event("VAULT_DELETE", f"Secret deleted: {name}")
        return True

    def list_secrets(self) -> list:
        """Returns the names of all stored secrets (never values)."""
        return list(self._load_secrets().keys())
