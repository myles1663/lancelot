"""Tests for S7: Vault Key Isolation.

Covers: env key loading, file key fallback, audit logging, key consistency.
"""
import unittest
import sys
import os
import tempfile
import json
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet

sys.path.insert(0, os.path.dirname(__file__))


class TestEnvKeyLoading(unittest.TestCase):
    """Verify vault loads key from VAULT_ENCRYPTION_KEY env var."""

    def test_env_key_used_when_set(self):
        key = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"VAULT_ENCRYPTION_KEY": key}):
                from vault import SecretVault
                vault = SecretVault(data_dir=tmpdir)
                # The fernet should work with the env key
                test_val = "secret_data"
                encrypted = vault.fernet.encrypt(test_val.encode())
                decrypted = vault.fernet.decrypt(encrypted).decode()
                self.assertEqual(decrypted, test_val)

    def test_env_key_takes_priority_over_file(self):
        env_key = Fernet.generate_key().decode()
        file_key = Fernet.generate_key()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a different key to file
            key_file = os.path.join(tmpdir, "vault.key")
            with open(key_file, "wb") as f:
                f.write(file_key)

            with patch.dict(os.environ, {"VAULT_ENCRYPTION_KEY": env_key}):
                from vault import SecretVault
                vault = SecretVault(data_dir=tmpdir)
                # Vault should use env key, not file key
                env_fernet = Fernet(env_key.encode())
                test_val = "priority_test"
                encrypted = env_fernet.encrypt(test_val.encode())
                # Should be decryptable with vault's fernet (same key)
                decrypted = vault.fernet.decrypt(encrypted).decode()
                self.assertEqual(decrypted, test_val)


class TestFileKeyFallback(unittest.TestCase):
    """Verify vault falls back to file key when env var is not set."""

    def test_file_key_loaded_when_no_env(self):
        file_key = Fernet.generate_key()
        with tempfile.TemporaryDirectory() as tmpdir:
            key_file = os.path.join(tmpdir, "vault.key")
            with open(key_file, "wb") as f:
                f.write(file_key)

            with patch.dict(os.environ, {}, clear=False):
                # Ensure VAULT_ENCRYPTION_KEY is not set
                os.environ.pop("VAULT_ENCRYPTION_KEY", None)
                from vault import SecretVault
                vault = SecretVault(data_dir=tmpdir)
                # Should use file key
                file_fernet = Fernet(file_key)
                test_val = "file_fallback"
                encrypted = file_fernet.encrypt(test_val.encode())
                decrypted = vault.fernet.decrypt(encrypted).decode()
                self.assertEqual(decrypted, test_val)

    def test_new_key_generated_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VAULT_ENCRYPTION_KEY", None)
                from vault import SecretVault
                vault = SecretVault(data_dir=tmpdir)
                # Key file should now exist
                key_file = os.path.join(tmpdir, "vault.key")
                self.assertTrue(os.path.exists(key_file))
                # Fernet should be functional
                encrypted = vault.fernet.encrypt(b"test")
                self.assertEqual(vault.fernet.decrypt(encrypted), b"test")

    def test_warning_printed_on_file_fallback(self):
        file_key = Fernet.generate_key()
        with tempfile.TemporaryDirectory() as tmpdir:
            key_file = os.path.join(tmpdir, "vault.key")
            with open(key_file, "wb") as f:
                f.write(file_key)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VAULT_ENCRYPTION_KEY", None)
                with patch("builtins.print") as mock_print:
                    from vault import SecretVault
                    vault = SecretVault(data_dir=tmpdir)
                    # Should have printed a warning
                    printed = " ".join(str(c) for c in mock_print.call_args_list)
                    self.assertIn("WARNING", printed)


class TestAuditLogging(unittest.TestCase):
    """Verify vault operations produce audit log events."""

    def _make_vault(self, tmpdir):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"VAULT_ENCRYPTION_KEY": key}):
            from vault import SecretVault
            vault = SecretVault(data_dir=tmpdir)
        return vault

    def test_store_logs_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = self._make_vault(tmpdir)
            with patch.object(vault.audit_logger, "log_event") as mock_log:
                vault.store("api_key", "secret123")
                mock_log.assert_called_once()
                args = mock_log.call_args
                self.assertEqual(args[0][0], "VAULT_STORE")
                self.assertIn("api_key", args[0][1])

    def test_retrieve_logs_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = self._make_vault(tmpdir)
            with patch.object(vault.audit_logger, "log_event") as mock_log:
                vault.retrieve("api_key")
                mock_log.assert_called_once()
                args = mock_log.call_args
                self.assertEqual(args[0][0], "VAULT_RETRIEVE")
                self.assertIn("api_key", args[0][1])

    def test_delete_logs_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = self._make_vault(tmpdir)
            # First store something to delete
            vault.store("to_delete", "value")
            with patch.object(vault.audit_logger, "log_event") as mock_log:
                vault.delete("to_delete")
                mock_log.assert_called_once()
                args = mock_log.call_args
                self.assertEqual(args[0][0], "VAULT_DELETE")
                self.assertIn("to_delete", args[0][1])


class TestKeyConsistency(unittest.TestCase):
    """Verify key consistency across vault operations."""

    def test_store_and_retrieve_roundtrip(self):
        key = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"VAULT_ENCRYPTION_KEY": key}):
                from vault import SecretVault
                vault = SecretVault(data_dir=tmpdir)
                vault.store("my_secret", "top_secret_value")
                result = vault.retrieve("my_secret")
                self.assertEqual(result, "top_secret_value")

    def test_same_env_key_across_instances(self):
        key = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"VAULT_ENCRYPTION_KEY": key}):
                from vault import SecretVault
                vault1 = SecretVault(data_dir=tmpdir)
                vault1.store("shared", "value123")
                vault2 = SecretVault(data_dir=tmpdir)
                result = vault2.retrieve("shared")
                self.assertEqual(result, "value123")


if __name__ == "__main__":
    unittest.main()
