"""
Tests for Prompts 28-29: CredentialVault Core + Scoped Access.

Uses real Fernet encryption and temp files. No mocks.
"""

import os
import pytest
from cryptography.fernet import Fernet

from src.connectors.base import ConnectorManifest, CredentialSpec
from src.connectors.vault import (
    CredentialVault,
    VaultAccessPolicy,
    VaultConfigurationError,
    VaultEntry,
)


@pytest.fixture
def vault_key():
    """Generate and set a Fernet key for testing."""
    key = Fernet.generate_key().decode()
    os.environ["LANCELOT_VAULT_KEY"] = key
    yield key
    os.environ.pop("LANCELOT_VAULT_KEY", None)


@pytest.fixture
def vault_config(tmp_path):
    """Create a vault config pointing to tmp_path."""
    import yaml
    config = {
        "version": "1.0",
        "storage": {
            "path": str(tmp_path / "credentials.enc"),
            "backup_path": str(tmp_path / "credentials.enc.bak"),
        },
        "encryption": {
            "method": "fernet",
            "key_source": "env",
            "key_env_var": "LANCELOT_VAULT_KEY",
        },
        "audit": {
            "log_access": True,
            "log_path": str(tmp_path / "access.log"),
        },
    }
    config_path = tmp_path / "vault.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return str(config_path)


@pytest.fixture
def vault(vault_key, vault_config):
    """Create a fresh CredentialVault for testing."""
    return CredentialVault(config_path=vault_config)


# ── Initialization ────────────────────────────────────────────────

class TestInit:
    def test_initializes_with_env_key(self, vault):
        assert vault is not None
        assert vault.list_keys() == []

    def test_fails_closed_when_env_not_set(self, vault_config):
        os.environ.pop("LANCELOT_VAULT_KEY", None)
        os.environ.pop("LANCELOT_ALLOW_EPHEMERAL_VAULT", None)
        with pytest.raises(VaultConfigurationError, match="no vault key configured"):
            CredentialVault(config_path=vault_config)

    def test_allows_explicit_ephemeral_override(self, vault_config):
        os.environ.pop("LANCELOT_VAULT_KEY", None)
        os.environ["LANCELOT_ALLOW_EPHEMERAL_VAULT"] = "true"
        try:
            v = CredentialVault(config_path=vault_config)
            assert v.key_source == "ephemeral"
            v.store("test", "value")
            assert v.retrieve("test") == "value"
        finally:
            os.environ.pop("LANCELOT_ALLOW_EPHEMERAL_VAULT", None)


# ── Store and Retrieve ────────────────────────────────────────────

class TestStoreRetrieve:
    def test_roundtrip(self, vault):
        vault.store("slack_token", "xoxb-12345", type="oauth_token")
        assert vault.retrieve("slack_token") == "xoxb-12345"

    def test_store_updates_value(self, vault):
        vault.store("api_key", "old_value")
        vault.store("api_key", "new_value")
        assert vault.retrieve("api_key") == "new_value"

    def test_retrieve_unknown_raises(self, vault):
        with pytest.raises(KeyError, match="not found"):
            vault.retrieve("nonexistent")

    def test_retrieve_records_accessor(self, vault):
        vault.store("token", "secret")
        # Grant access first (P29 scoped access)
        vault.access_policy.grant("slack_connector", "token")
        vault.retrieve("token", accessor_id="slack_connector")
        vault.retrieve("token", accessor_id="slack_connector")
        entry = vault._entries["token"]
        assert "slack_connector" in entry.accessed_by
        assert entry.accessed_by.count("slack_connector") == 1


# ── Delete ────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_removes(self, vault):
        vault.store("token", "secret")
        assert vault.delete("token") is True
        assert vault.exists("token") is False

    def test_delete_unknown_returns_false(self, vault):
        assert vault.delete("nonexistent") is False


# ── List and Exists ───────────────────────────────────────────────

class TestListExists:
    def test_list_keys(self, vault):
        vault.store("a", "1")
        vault.store("b", "2")
        keys = vault.list_keys()
        assert sorted(keys) == ["a", "b"]

    def test_exists(self, vault):
        vault.store("token", "secret")
        assert vault.exists("token") is True
        assert vault.exists("nonexistent") is False


# ── Check Requirements ────────────────────────────────────────────

class TestCheckRequirements:
    def test_check_requirements(self, vault):
        vault.store("slack_token", "xoxb-123")
        specs = [
            CredentialSpec(name="Slack Token", type="oauth_token", vault_key="slack_token"),
            CredentialSpec(name="Missing Key", type="api_key", vault_key="missing_key"),
        ]
        result = vault.check_requirements(specs)
        assert result == {"slack_token": True, "missing_key": False}


# ── Encryption on Disk ────────────────────────────────────────────

class TestEncryption:
    def test_data_encrypted_on_disk(self, vault, tmp_path):
        vault.store("secret_key", "my_super_secret_value")
        raw = (tmp_path / "credentials.enc").read_bytes()
        # Raw bytes should NOT contain the plaintext value
        assert b"my_super_secret_value" not in raw


# ── Persistence ───────────────────────────────────────────────────

class TestPersistence:
    def test_survives_restart(self, vault_key, vault_config):
        # Store with first instance
        v1 = CredentialVault(config_path=vault_config)
        v1.store("persistent_key", "persistent_value")

        # Create new instance — should load from disk
        v2 = CredentialVault(config_path=vault_config)
        assert v2.retrieve("persistent_key") == "persistent_value"

    def test_backup_created(self, vault, tmp_path):
        vault.store("first", "value1")
        # First save creates the file but no backup yet (no pre-existing file)
        vault.store("second", "value2")
        # Second save should backup the first file
        assert (tmp_path / "credentials.enc.bak").exists()

    def test_corrupted_primary_uses_backup(self, vault_key, vault_config, tmp_path):
        v1 = CredentialVault(config_path=vault_config)
        v1.store("persistent_key", "persistent_value")

        primary = tmp_path / "credentials.enc"
        backup = tmp_path / "credentials.enc.bak"
        backup.write_bytes(primary.read_bytes())
        backup_bytes = backup.read_bytes()
        primary.write_bytes(b"not-valid-fernet")

        v2 = CredentialVault(config_path=vault_config)
        assert v2.retrieve("persistent_key") == "persistent_value"
        assert primary.read_bytes() == backup_bytes

    def test_corrupted_primary_without_backup_raises(self, vault_key, vault_config, tmp_path):
        v1 = CredentialVault(config_path=vault_config)
        v1.store("persistent_key", "persistent_value")

        primary = tmp_path / "credentials.enc"
        backup = tmp_path / "credentials.enc.bak"
        if backup.exists():
            backup.unlink()
        primary.write_bytes(b"not-valid-fernet")

        with pytest.raises(VaultConfigurationError, match="could not be decrypted"):
            CredentialVault(config_path=vault_config)

    def test_atomic_save_failure_preserves_last_good_primary(self, vault, vault_config, tmp_path, monkeypatch):
        vault.store("first", "value1")
        primary = tmp_path / "credentials.enc"
        primary_before = primary.read_bytes()

        import src.connectors.vault as vault_module

        def _raising_replace(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(vault_module.os, "replace", _raising_replace)

        with pytest.raises(OSError, match="disk full"):
            vault.store("second", "value2")

        assert primary.read_bytes() == primary_before
        assert not (tmp_path / "credentials.enc.tmp").exists()

        reloaded = CredentialVault(config_path=vault_config)
        assert reloaded.retrieve("first") == "value1"
        with pytest.raises(KeyError):
            reloaded.retrieve("second")

    def test_inspect_health_reports_key_mismatch_from_metadata(self, vault_config, tmp_path):
        original_key = Fernet.generate_key().decode()
        replacement_key = Fernet.generate_key().decode()
        os.environ["LANCELOT_VAULT_KEY"] = original_key
        vault = CredentialVault(config_path=vault_config)
        vault.store("persistent_key", "persistent_value")

        os.environ["LANCELOT_VAULT_KEY"] = replacement_key
        snapshot = CredentialVault.inspect_health(
            config_path=vault_config,
            last_error="Credential vault initialization failed: encrypted vault contents could not be decrypted from the primary or backup file.",
        )

        assert snapshot.status == "key_mismatch"
        assert snapshot.suspected_key_mismatch is True
        assert snapshot.metadata_present is True
        assert snapshot.has_primary is True
        assert snapshot.key_id != snapshot.metadata_key_id

    def test_reset_storage_archives_encrypted_vault_artifacts(self, vault_config, tmp_path):
        vault = CredentialVault(config_path=vault_config)
        vault.store("first", "value1")
        vault.store("second", "value2")

        result = CredentialVault.reset_storage(config_path=vault_config)

        archive_dir = tmp_path / "reset_backups"
        archived_names = {path.name for path in archive_dir.rglob("*") if path.is_file()}
        assert "credentials.enc" in archived_names
        assert "credentials.enc.bak" in archived_names
        assert "credentials.meta.json" in archived_names
        assert not (tmp_path / "credentials.enc").exists()
        assert not (tmp_path / "credentials.enc.bak").exists()
        assert not (tmp_path / "credentials.meta.json").exists()
        assert result["archived_files"]


# ── Audit Log ─────────────────────────────────────────────────────

class TestAuditLog:
    def test_audit_log_records(self, vault, tmp_path):
        vault.store("token", "secret")
        vault.access_policy.grant("slack", "token")
        vault.retrieve("token", accessor_id="slack")
        vault.delete("token")

        log_path = tmp_path / "access.log"
        assert log_path.exists()
        content = log_path.read_text()
        assert "store" in content
        assert "retrieve" in content
        assert "delete" in content
        assert "slack" in content


# ── Vault Access Policy (Prompt 29) ──────────────────────────────

class TestVaultAccessPolicy:
    def test_grant_and_is_allowed(self):
        policy = VaultAccessPolicy()
        policy.grant("slack", "slack_token")
        assert policy.is_allowed("slack", "slack_token") is True

    def test_is_allowed_returns_false_for_ungranted(self):
        policy = VaultAccessPolicy()
        assert policy.is_allowed("slack", "slack_token") is False

    def test_revoke_removes_specific(self):
        policy = VaultAccessPolicy()
        policy.grant("slack", "slack_token")
        policy.grant("slack", "another_key")
        policy.revoke("slack", "slack_token")
        assert policy.is_allowed("slack", "slack_token") is False
        assert policy.is_allowed("slack", "another_key") is True

    def test_revoke_all(self):
        policy = VaultAccessPolicy()
        policy.grant("slack", "slack_token")
        policy.grant("slack", "another_key")
        policy.revoke_all("slack")
        assert policy.is_allowed("slack", "slack_token") is False
        assert policy.is_allowed("slack", "another_key") is False

    def test_list_grants(self):
        policy = VaultAccessPolicy()
        policy.grant("slack", "slack_token")
        policy.grant("slack", "slack_oauth")
        grants = policy.list_grants("slack")
        assert sorted(grants) == ["slack_oauth", "slack_token"]


# ── Scoped Access Integration (Prompt 29) ────────────────────────

class TestScopedAccess:
    def test_grant_connector_access_uses_manifest(self, vault):
        manifest = ConnectorManifest(
            id="slack",
            name="Slack",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            target_domains=["slack.com"],
            required_credentials=[
                CredentialSpec(name="Bot Token", type="oauth_token", vault_key="slack_bot_token"),
                CredentialSpec(name="App Token", type="api_key", vault_key="slack_app_token"),
            ],
        )
        vault.grant_connector_access("slack", manifest)
        assert vault.access_policy.is_allowed("slack", "slack_bot_token") is True
        assert vault.access_policy.is_allowed("slack", "slack_app_token") is True

    def test_retrieve_with_granted_accessor_succeeds(self, vault):
        vault.store("slack_token", "xoxb-123")
        vault.access_policy.grant("slack", "slack_token")
        assert vault.retrieve("slack_token", accessor_id="slack") == "xoxb-123"

    def test_retrieve_with_ungranted_accessor_raises(self, vault):
        vault.store("slack_token", "xoxb-123")
        with pytest.raises(PermissionError, match="not granted access"):
            vault.retrieve("slack_token", accessor_id="evil_connector")

    def test_retrieve_with_empty_accessor_always_succeeds(self, vault):
        vault.store("slack_token", "xoxb-123")
        # Empty accessor = admin access, no policy check
        assert vault.retrieve("slack_token") == "xoxb-123"

    def test_revoke_connector_access_prevents_retrieval(self, vault):
        vault.store("slack_token", "xoxb-123")
        vault.access_policy.grant("slack", "slack_token")
        assert vault.retrieve("slack_token", accessor_id="slack") == "xoxb-123"

        vault.revoke_connector_access("slack")
        with pytest.raises(PermissionError):
            vault.retrieve("slack_token", accessor_id="slack")

    def test_two_connectors_same_key_independent(self, vault):
        vault.store("shared_key", "shared_value")
        vault.access_policy.grant("slack", "shared_key")
        vault.access_policy.grant("email", "shared_key")

        assert vault.retrieve("shared_key", accessor_id="slack") == "shared_value"
        assert vault.retrieve("shared_key", accessor_id="email") == "shared_value"

    def test_revoking_one_doesnt_affect_other(self, vault):
        vault.store("shared_key", "shared_value")
        vault.access_policy.grant("slack", "shared_key")
        vault.access_policy.grant("email", "shared_key")

        vault.access_policy.revoke("slack", "shared_key")
        # Slack revoked, email still has access
        with pytest.raises(PermissionError):
            vault.retrieve("shared_key", accessor_id="slack")
        assert vault.retrieve("shared_key", accessor_id="email") == "shared_value"
