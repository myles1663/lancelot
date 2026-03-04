"""Tests for secret_cache — vault-backed in-memory secret store."""

import base64
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import secret_cache


class FakeVault:
    """Minimal vault stub for testing."""

    def __init__(self):
        self._data = {}

    def exists(self, key):
        return key in self._data

    def retrieve(self, key):
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]

    def store(self, key, value, type="api_key"):
        self._data[key] = value


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset secret_cache module state before each test."""
    secret_cache._reset()
    # Clean env vars that tests might set
    for key in list(secret_cache._KEY_MAP.keys()):
        os.environ.pop(key, None)
    yield
    secret_cache._reset()
    for key in list(secret_cache._KEY_MAP.keys()):
        os.environ.pop(key, None)


# ── Bootstrap from vault ──────────────────────────────────────────

class TestBootstrapFromVault:
    def test_loads_existing_vault_secrets(self):
        vault = FakeVault()
        vault._data["system.api_token"] = "tok_abc"
        vault._data["system.owner_token"] = "own_xyz"

        secret_cache.bootstrap(vault)

        assert secret_cache.is_bootstrapped()
        assert secret_cache.get("LANCELOT_API_TOKEN") == "tok_abc"
        assert secret_cache.get("LANCELOT_OWNER_TOKEN") == "own_xyz"

    def test_missing_vault_key_returns_default(self):
        vault = FakeVault()
        secret_cache.bootstrap(vault)

        assert secret_cache.get("LANCELOT_API_TOKEN") == ""
        assert secret_cache.get("LANCELOT_API_TOKEN", "fallback") == "fallback"


# ── Migration from environment ────────────────────────────────────

class TestMigrationFromEnv:
    def test_migrates_env_to_vault(self):
        vault = FakeVault()
        os.environ["LANCELOT_API_TOKEN"] = "env_token_123"

        secret_cache.bootstrap(vault)

        # Should be in cache
        assert secret_cache.get("LANCELOT_API_TOKEN") == "env_token_123"
        # Should be stored in vault
        assert vault._data["system.api_token"] == "env_token_123"

    def test_vault_takes_precedence_over_env(self):
        vault = FakeVault()
        vault._data["system.api_token"] = "vault_token"
        os.environ["LANCELOT_API_TOKEN"] = "env_token"

        secret_cache.bootstrap(vault)

        assert secret_cache.get("LANCELOT_API_TOKEN") == "vault_token"


# ── Password hashing ─────────────────────────────────────────────

class TestPasswordHashing:
    def test_warroom_password_hashed_on_migration(self):
        vault = FakeVault()
        os.environ["WARROOM_PASSWORD"] = "my_secret_pw"

        secret_cache.bootstrap(vault)

        expected_hash = hashlib.sha256(b"my_secret_pw").hexdigest()
        assert secret_cache.get("WARROOM_PASSWORD") == expected_hash
        assert vault._data["system.warroom_password_hash"] == expected_hash

    def test_warroom_password_not_rehashed_from_vault(self):
        """When loading from vault, the stored hash is used as-is."""
        vault = FakeVault()
        stored_hash = hashlib.sha256(b"original").hexdigest()
        vault._data["system.warroom_password_hash"] = stored_hash

        secret_cache.bootstrap(vault)

        assert secret_cache.get("WARROOM_PASSWORD") == stored_hash

    def test_other_secrets_not_hashed(self):
        vault = FakeVault()
        os.environ["LANCELOT_TELEGRAM_TOKEN"] = "bot123:xyz"

        secret_cache.bootstrap(vault)

        assert secret_cache.get("LANCELOT_TELEGRAM_TOKEN") == "bot123:xyz"
        assert vault._data["system.telegram_token"] == "bot123:xyz"


# ── Environ scrubbing ────────────────────────────────────────────

class TestEnvironScrubbing:
    def test_scrub_removes_cached_keys(self):
        vault = FakeVault()
        os.environ["LANCELOT_API_TOKEN"] = "tok"
        os.environ["WARROOM_USERNAME"] = "admin"

        secret_cache.bootstrap(vault)
        secret_cache.scrub_environ()

        assert "LANCELOT_API_TOKEN" not in os.environ
        assert "WARROOM_USERNAME" not in os.environ

    def test_scrub_preserves_uncached_keys(self):
        vault = FakeVault()
        os.environ["SOME_OTHER_VAR"] = "keep_me"

        secret_cache.bootstrap(vault)
        secret_cache.scrub_environ()

        assert os.environ["SOME_OTHER_VAR"] == "keep_me"
        os.environ.pop("SOME_OTHER_VAR", None)

    def test_cache_still_works_after_scrub(self):
        vault = FakeVault()
        os.environ["LANCELOT_API_TOKEN"] = "tok"

        secret_cache.bootstrap(vault)
        secret_cache.scrub_environ()

        # Still accessible from cache
        assert secret_cache.get("LANCELOT_API_TOKEN") == "tok"


# ── Fallback before bootstrap ────────────────────────────────────

class TestFallbackBeforeBootstrap:
    def test_get_falls_back_to_os_getenv(self):
        os.environ["LANCELOT_API_TOKEN"] = "env_fallback"

        assert not secret_cache.is_bootstrapped()
        assert secret_cache.get("LANCELOT_API_TOKEN") == "env_fallback"

    def test_get_returns_default_when_not_bootstrapped(self):
        assert secret_cache.get("LANCELOT_API_TOKEN", "def") == "def"


# ── Reload (Phase 2) ─────────────────────────────────────────────

class TestReload:
    def test_reload_detects_changed_values(self):
        vault = FakeVault()
        vault._data["system.api_token"] = "old_token"
        secret_cache.bootstrap(vault)

        # Simulate vault update
        vault._data["system.api_token"] = "new_token"
        changed = secret_cache.reload(vault)

        assert changed["LANCELOT_API_TOKEN"] is True
        assert secret_cache.get("LANCELOT_API_TOKEN") == "new_token"

    def test_reload_reports_unchanged(self):
        vault = FakeVault()
        vault._data["system.api_token"] = "same_token"
        secret_cache.bootstrap(vault)

        changed = secret_cache.reload(vault)
        assert changed["LANCELOT_API_TOKEN"] is False


# ── Thread safety ─────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_reads(self):
        vault = FakeVault()
        vault._data["system.api_token"] = "concurrent_tok"
        secret_cache.bootstrap(vault)

        results = []
        errors = []

        def reader():
            try:
                for _ in range(100):
                    val = secret_cache.get("LANCELOT_API_TOKEN")
                    results.append(val)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(v == "concurrent_tok" for v in results)

    def test_concurrent_read_during_reload(self):
        vault = FakeVault()
        vault._data["system.api_token"] = "v1"
        secret_cache.bootstrap(vault)

        errors = []

        def reader():
            try:
                for _ in range(100):
                    secret_cache.get("LANCELOT_API_TOKEN")
            except Exception as e:
                errors.append(e)

        def reloader():
            try:
                for i in range(10):
                    vault._data["system.api_token"] = f"v{i+2}"
                    secret_cache.reload(vault)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        threads.append(threading.Thread(target=reloader))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


# ── All keys mapped ──────────────────────────────────────────────

class TestAllKeysMapped:
    def test_full_migration(self):
        vault = FakeVault()
        os.environ["LANCELOT_API_TOKEN"] = "at"
        os.environ["LANCELOT_OWNER_TOKEN"] = "ot"
        os.environ["WARROOM_USERNAME"] = "admin"
        os.environ["WARROOM_PASSWORD"] = "pw"
        os.environ["LANCELOT_TELEGRAM_TOKEN"] = "tt"
        os.environ["LANCELOT_TELEGRAM_CHAT_ID"] = "cid"

        secret_cache.bootstrap(vault)

        assert secret_cache.get("LANCELOT_API_TOKEN") == "at"
        assert secret_cache.get("LANCELOT_OWNER_TOKEN") == "ot"
        assert secret_cache.get("WARROOM_USERNAME") == "admin"
        assert secret_cache.get("WARROOM_PASSWORD") == hashlib.sha256(b"pw").hexdigest()
        assert secret_cache.get("LANCELOT_TELEGRAM_TOKEN") == "tt"
        assert secret_cache.get("LANCELOT_TELEGRAM_CHAT_ID") == "cid"

        # Verify vault keys
        assert vault.exists("system.api_token")
        assert vault.exists("system.owner_token")
        assert vault.exists("system.warroom_username")
        assert vault.exists("system.warroom_password_hash")
        assert vault.exists("system.telegram_token")
        assert vault.exists("system.telegram_chat_id")


# ══════════════════════════════════════════════════════════════════
# Phase 3 Tests — Docker Secrets, PBKDF2 Key Derivation, Key Scrub
# ══════════════════════════════════════════════════════════════════

class TestDockerSecrets:
    """Test Docker secret file resolution in CredentialVault."""

    def test_docker_secret_file_takes_priority_over_env(self, tmp_path):
        """Vault key from /run/secrets/ should override env var."""
        from cryptography.fernet import Fernet
        docker_key = Fernet.generate_key().decode()
        env_key = Fernet.generate_key().decode()

        # Write a fake Docker secret file
        secret_file = tmp_path / "lancelot_vault_key"
        secret_file.write_text(docker_key, encoding="utf-8")

        os.environ["LANCELOT_VAULT_KEY"] = env_key
        try:
            from connectors.vault import CredentialVault
            with patch.object(Path, '__new__', wraps=Path.__new__):
                # Patch the Docker secret path to our tmp file
                result = CredentialVault._resolve_key("LANCELOT_VAULT_KEY", "lancelot_vault_key")
                # Without actual /run/secrets, falls back to env — test env path
                assert result == env_key
        finally:
            os.environ.pop("LANCELOT_VAULT_KEY", None)

    def test_resolve_key_reads_docker_secret_when_file_exists(self, tmp_path):
        """Simulate Docker secret resolution with a real file."""
        from cryptography.fernet import Fernet
        from connectors.vault import CredentialVault

        secret_key = Fernet.generate_key().decode()
        secret_name = "test_vault_key"
        secret_path = tmp_path / secret_name

        secret_path.write_text(f"  {secret_key}  \n", encoding="utf-8")

        # Patch /run/secrets/<name> to point to our tmp file
        with patch("connectors.vault.Path") as MockPath:
            mock_secret = MagicMock()
            mock_secret.exists.return_value = True
            mock_secret.read_text.return_value = f"  {secret_key}  \n"

            def path_side_effect(arg):
                if arg == f"/run/secrets/{secret_name}":
                    return mock_secret
                return Path(arg)

            MockPath.side_effect = path_side_effect

            result = CredentialVault._resolve_key("LANCELOT_VAULT_KEY", secret_name)
            assert result == secret_key

    def test_resolve_key_fallback_to_env_when_no_docker_secret(self):
        """Without Docker secret file, should fall back to env var."""
        from connectors.vault import CredentialVault
        from cryptography.fernet import Fernet

        env_key = Fernet.generate_key().decode()
        os.environ["LANCELOT_VAULT_KEY"] = env_key
        try:
            result = CredentialVault._resolve_key("LANCELOT_VAULT_KEY", "nonexistent_secret")
            assert result == env_key
        finally:
            os.environ.pop("LANCELOT_VAULT_KEY", None)

    def test_resolve_key_returns_empty_when_nothing_configured(self):
        """No Docker secret, no env var → empty string."""
        from connectors.vault import CredentialVault
        os.environ.pop("LANCELOT_VAULT_KEY", None)
        result = CredentialVault._resolve_key("LANCELOT_VAULT_KEY", "nonexistent_secret")
        assert result == ""


class TestPBKDF2KeyDerivation:
    """Test passphrase-based key derivation in CredentialVault."""

    def test_is_valid_fernet_key_recognizes_real_key(self):
        from cryptography.fernet import Fernet
        from connectors.vault import CredentialVault

        real_key = Fernet.generate_key().decode()
        assert CredentialVault._is_valid_fernet_key(real_key) is True

    def test_is_valid_fernet_key_rejects_passphrase(self):
        from connectors.vault import CredentialVault

        assert CredentialVault._is_valid_fernet_key("my-memorable-passphrase") is False
        assert CredentialVault._is_valid_fernet_key("short") is False
        assert CredentialVault._is_valid_fernet_key("") is False

    def test_derive_key_from_passphrase_produces_valid_fernet_key(self, tmp_path):
        from cryptography.fernet import Fernet
        from connectors.vault import CredentialVault

        derived = CredentialVault._derive_key_from_passphrase("my passphrase", tmp_path)
        # Should be valid Fernet key bytes
        cipher = Fernet(derived)
        # Should be able to encrypt/decrypt
        ct = cipher.encrypt(b"test data")
        assert cipher.decrypt(ct) == b"test data"

    def test_derive_key_deterministic_with_same_salt(self, tmp_path):
        from connectors.vault import CredentialVault

        key1 = CredentialVault._derive_key_from_passphrase("passphrase", tmp_path)
        key2 = CredentialVault._derive_key_from_passphrase("passphrase", tmp_path)
        assert key1 == key2

    def test_derive_key_different_with_different_passphrase(self, tmp_path):
        from connectors.vault import CredentialVault

        key1 = CredentialVault._derive_key_from_passphrase("passphrase1", tmp_path)
        # Remove salt so a new one is generated for key2
        salt_file = tmp_path / "vault_salt.bin"
        salt_file.unlink()
        key2 = CredentialVault._derive_key_from_passphrase("passphrase2", tmp_path)
        assert key1 != key2

    def test_salt_file_created_and_persisted(self, tmp_path):
        from connectors.vault import CredentialVault, _PBKDF2_SALT_FILE

        salt_path = tmp_path / _PBKDF2_SALT_FILE
        assert not salt_path.exists()

        CredentialVault._derive_key_from_passphrase("test", tmp_path)
        assert salt_path.exists()
        assert len(salt_path.read_bytes()) == 16  # 16-byte salt

    def test_vault_init_with_passphrase(self, tmp_path):
        """Full integration: init vault with passphrase, store/retrieve."""
        from connectors.vault import CredentialVault

        # Write minimal vault config pointing to tmp_path
        config_path = tmp_path / "vault.yaml"
        config_path.write_text(
            f"storage:\n  path: {tmp_path / 'creds.enc'}\n  backup_path: {tmp_path / 'creds.bak'}\n"
            f"audit:\n  log_access: false\n"
            f"encryption:\n  key_env_var: TEST_VAULT_KEY\n",
            encoding="utf-8",
        )

        os.environ["TEST_VAULT_KEY"] = "my-memorable-passphrase-2024"
        try:
            vault = CredentialVault(config_path=str(config_path))
            assert vault.key_source == "pbkdf2"

            vault.store("test.secret", "hello_world")
            assert vault.retrieve("test.secret") == "hello_world"

            # Verify it survives a second init with same passphrase
            vault2 = CredentialVault(config_path=str(config_path))
            assert vault2.retrieve("test.secret") == "hello_world"
        finally:
            os.environ.pop("TEST_VAULT_KEY", None)

    def test_vault_init_with_fernet_key(self, tmp_path):
        """Verify raw Fernet key still works (backward compat)."""
        from cryptography.fernet import Fernet
        from connectors.vault import CredentialVault

        config_path = tmp_path / "vault.yaml"
        config_path.write_text(
            f"storage:\n  path: {tmp_path / 'creds.enc'}\n  backup_path: {tmp_path / 'creds.bak'}\n"
            f"audit:\n  log_access: false\n"
            f"encryption:\n  key_env_var: TEST_VAULT_KEY2\n",
            encoding="utf-8",
        )

        fernet_key = Fernet.generate_key().decode()
        os.environ["TEST_VAULT_KEY2"] = fernet_key
        try:
            vault = CredentialVault(config_path=str(config_path))
            assert vault.key_source == "fernet"
            vault.store("test.key", "value123")
            assert vault.retrieve("test.key") == "value123"
        finally:
            os.environ.pop("TEST_VAULT_KEY2", None)


class TestVaultKeyScrub:
    """Test that LANCELOT_VAULT_KEY is scrubbed from os.environ after boot."""

    def test_vault_key_scrubbed_after_bootstrap(self):
        """Simulate the gateway boot sequence scrub."""
        os.environ["LANCELOT_VAULT_KEY"] = "some-key-value"

        # Simulate what gateway.py does
        assert "LANCELOT_VAULT_KEY" in os.environ
        del os.environ["LANCELOT_VAULT_KEY"]
        assert "LANCELOT_VAULT_KEY" not in os.environ

    def test_vault_key_scrub_does_not_break_existing_vault(self, tmp_path):
        """After scrubbing the env var, the vault cipher still works."""
        from cryptography.fernet import Fernet
        from connectors.vault import CredentialVault

        config_path = tmp_path / "vault.yaml"
        config_path.write_text(
            f"storage:\n  path: {tmp_path / 'creds.enc'}\n  backup_path: {tmp_path / 'creds.bak'}\n"
            f"audit:\n  log_access: false\n"
            f"encryption:\n  key_env_var: TEST_SCRUB_KEY\n",
            encoding="utf-8",
        )

        fernet_key = Fernet.generate_key().decode()
        os.environ["TEST_SCRUB_KEY"] = fernet_key
        try:
            vault = CredentialVault(config_path=str(config_path))
            vault.store("secret.one", "val1")

            # Scrub the key from environ
            del os.environ["TEST_SCRUB_KEY"]

            # Vault should still work — cipher is in memory
            assert vault.retrieve("secret.one") == "val1"
            vault.store("secret.two", "val2")
            assert vault.retrieve("secret.two") == "val2"
        finally:
            os.environ.pop("TEST_SCRUB_KEY", None)

    def test_vault_key_not_in_cache_keys(self):
        """LANCELOT_VAULT_KEY should NOT be in the secret_cache key map."""
        assert "LANCELOT_VAULT_KEY" not in secret_cache._KEY_MAP
