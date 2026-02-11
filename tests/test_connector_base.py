"""
Tests for Prompt 25: Connector Module Scaffold + Base Classes.

Tests connector status enum, credential specs, connector manifests,
connector base class, and the new feature flags + capability enum values.
"""

import os
import pytest
from typing import Any

from src.connectors.base import (
    ConnectorBase,
    ConnectorManifest,
    ConnectorStatus,
    CredentialSpec,
)
from src.tools.contracts import Capability


# ── Helpers ────────────────────────────────────────────────────────

def _valid_manifest(**overrides) -> ConnectorManifest:
    """Create a valid ConnectorManifest with optional overrides."""
    defaults = dict(
        id="test",
        name="Test Connector",
        version="1.0.0",
        author="lancelot",
        source="first-party",
        target_domains=["api.test.com"],
    )
    defaults.update(overrides)
    return ConnectorManifest(**defaults)


class StubConnector(ConnectorBase):
    """Minimal concrete connector for testing the abstract base."""

    def get_operations(self) -> list:
        return [{"id": "op1", "name": "Test Op"}]

    def execute(self, operation_id: str, params: dict) -> Any:
        return {"method": "GET", "url": "https://api.test.com/v1"}

    def validate_credentials(self) -> bool:
        return True


# ── ConnectorStatus ───────────────────────────────────────────────

class TestConnectorStatus:
    def test_has_five_values(self):
        assert len(ConnectorStatus) == 5

    def test_values(self):
        assert ConnectorStatus.REGISTERED == "registered"
        assert ConnectorStatus.CONFIGURED == "configured"
        assert ConnectorStatus.ACTIVE == "active"
        assert ConnectorStatus.SUSPENDED == "suspended"
        assert ConnectorStatus.ERROR == "error"


# ── CredentialSpec ────────────────────────────────────────────────

class TestCredentialSpec:
    def test_is_frozen(self):
        spec = CredentialSpec(name="token", type="api_key", vault_key="slack_token")
        with pytest.raises(AttributeError):
            spec.name = "changed"  # type: ignore

    def test_stores_all_fields(self):
        spec = CredentialSpec(
            name="slack_bot_token",
            type="oauth_token",
            vault_key="slack_oauth",
            required=False,
            scopes=["channels:read", "chat:write"],
        )
        assert spec.name == "slack_bot_token"
        assert spec.type == "oauth_token"
        assert spec.vault_key == "slack_oauth"
        assert spec.required is False
        assert spec.scopes == ["channels:read", "chat:write"]

    def test_defaults(self):
        spec = CredentialSpec(name="key", type="api_key", vault_key="k")
        assert spec.required is True
        assert spec.scopes == []


# ── ConnectorManifest ─────────────────────────────────────────────

class TestConnectorManifest:
    def test_validate_passes_with_valid_data(self):
        manifest = _valid_manifest()
        manifest.validate()  # should not raise

    def test_validate_raises_for_empty_id(self):
        with pytest.raises(ValueError, match="id must not be empty"):
            _valid_manifest(id="").validate()

    def test_validate_raises_for_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            _valid_manifest(name="").validate()

    def test_validate_raises_for_empty_version(self):
        with pytest.raises(ValueError, match="version must not be empty"):
            _valid_manifest(version="").validate()

    def test_validate_raises_for_empty_target_domains(self):
        with pytest.raises(ValueError, match="target_domains must not be empty"):
            _valid_manifest(target_domains=[]).validate()

    def test_validate_raises_for_invalid_source(self):
        with pytest.raises(ValueError, match="source must be"):
            _valid_manifest(source="unknown").validate()

    def test_is_frozen(self):
        manifest = _valid_manifest()
        with pytest.raises(AttributeError):
            manifest.id = "changed"  # type: ignore


# ── ConnectorBase ─────────────────────────────────────────────────

class TestConnectorBase:
    def test_initializes_with_registered_status(self):
        conn = StubConnector(_valid_manifest())
        assert conn.status == ConnectorStatus.REGISTERED

    def test_id_returns_manifest_id(self):
        conn = StubConnector(_valid_manifest(id="slack"))
        assert conn.id == "slack"

    def test_manifest_property(self):
        manifest = _valid_manifest()
        conn = StubConnector(manifest)
        assert conn.manifest is manifest

    def test_set_status(self):
        conn = StubConnector(_valid_manifest())
        conn.set_status(ConnectorStatus.ACTIVE)
        assert conn.status == ConnectorStatus.ACTIVE

    def test_get_operations(self):
        conn = StubConnector(_valid_manifest())
        ops = conn.get_operations()
        assert len(ops) == 1
        assert ops[0]["id"] == "op1"

    def test_execute(self):
        conn = StubConnector(_valid_manifest())
        result = conn.execute("op1", {})
        assert result["method"] == "GET"

    def test_validate_credentials(self):
        conn = StubConnector(_valid_manifest())
        assert conn.validate_credentials() is True


# ── Feature Flags ─────────────────────────────────────────────────

class TestFeatureFlags:
    def test_connector_flags_default_false(self):
        # Clear env vars to test defaults
        for key in ("FEATURE_CONNECTORS", "FEATURE_TRUST_LEDGER", "FEATURE_SKILL_SECURITY_PIPELINE"):
            os.environ.pop(key, None)

        from src.core import feature_flags
        feature_flags.reload_flags()

        assert feature_flags.FEATURE_CONNECTORS is False
        assert feature_flags.FEATURE_TRUST_LEDGER is False
        assert feature_flags.FEATURE_SKILL_SECURITY_PIPELINE is False

    def test_connector_flags_enable(self):
        os.environ["FEATURE_CONNECTORS"] = "true"
        os.environ["FEATURE_TRUST_LEDGER"] = "1"
        os.environ["FEATURE_SKILL_SECURITY_PIPELINE"] = "yes"

        from src.core import feature_flags
        feature_flags.reload_flags()

        assert feature_flags.FEATURE_CONNECTORS is True
        assert feature_flags.FEATURE_TRUST_LEDGER is True
        assert feature_flags.FEATURE_SKILL_SECURITY_PIPELINE is True

        # Cleanup
        for key in ("FEATURE_CONNECTORS", "FEATURE_TRUST_LEDGER", "FEATURE_SKILL_SECURITY_PIPELINE"):
            os.environ.pop(key, None)
        feature_flags.reload_flags()


# ── Capability Enum ───────────────────────────────────────────────

class TestCapabilityEnum:
    def test_has_connector_read(self):
        assert Capability.CONNECTOR_READ == "connector.read"

    def test_has_connector_write(self):
        assert Capability.CONNECTOR_WRITE == "connector.write"

    def test_has_connector_delete(self):
        assert Capability.CONNECTOR_DELETE == "connector.delete"

    def test_has_credential_read(self):
        assert Capability.CREDENTIAL_READ == "credential.read"

    def test_has_credential_write(self):
        assert Capability.CREDENTIAL_WRITE == "credential.write"

    def test_total_capabilities(self):
        # 7 original + 5 connector = 12
        assert len(Capability) == 12
