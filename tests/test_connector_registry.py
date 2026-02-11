"""
Tests for Prompt 27: ConnectorRegistry + connectors.yaml.
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
from src.connectors.models import ConnectorOperation
from src.connectors.registry import ConnectorRegistry, ConnectorEntry
from src.core import feature_flags


# ── Test Connector ────────────────────────────────────────────────

class TestConnector(ConnectorBase):
    """Concrete connector for testing."""

    def __init__(self, manifest=None, ops=None):
        if manifest is None:
            manifest = ConnectorManifest(
                id="test",
                name="Test Connector",
                version="1.0.0",
                author="lancelot",
                source="first-party",
                target_domains=["api.test.com"],
            )
        super().__init__(manifest)
        self._ops = ops or [
            ConnectorOperation(
                id="read_data",
                connector_id=manifest.id,
                capability="connector.read",
                name="Read Data",
            ),
        ]

    def get_operations(self) -> list:
        return self._ops

    def execute(self, operation_id: str, params: dict) -> Any:
        return {"method": "GET", "url": "https://api.test.com/v1"}

    def validate_credentials(self) -> bool:
        return True


def _make_connector(id: str = "test", **overrides) -> TestConnector:
    """Create a TestConnector with custom manifest fields."""
    defaults = dict(
        id=id,
        name=f"{id.title()} Connector",
        version="1.0.0",
        author="lancelot",
        source="first-party",
        target_domains=[f"api.{id}.com"],
    )
    defaults.update(overrides)
    manifest = ConnectorManifest(**defaults)
    return TestConnector(manifest)


@pytest.fixture(autouse=True)
def enable_connectors():
    """Enable FEATURE_CONNECTORS for all tests, restore after."""
    old = os.environ.get("FEATURE_CONNECTORS")
    os.environ["FEATURE_CONNECTORS"] = "true"
    feature_flags.reload_flags()
    yield
    if old is None:
        os.environ.pop("FEATURE_CONNECTORS", None)
    else:
        os.environ["FEATURE_CONNECTORS"] = old
    feature_flags.reload_flags()


# ── Config Loading ────────────────────────────────────────────────

class TestRegistryConfig:
    def test_loads_config(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        assert reg.settings.get("max_concurrent_requests") == 10
        assert reg.settings.get("default_timeout_seconds") == 30

    def test_settings_has_retry(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        assert reg.settings.get("retry_max_attempts") == 3
        assert reg.settings.get("retry_backoff_seconds") == 1

    def test_rate_limits_loaded(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        assert reg.rate_limits.get("default", {}).get("max_requests_per_minute") == 60

    def test_missing_config_still_works(self):
        reg = ConnectorRegistry("config/nonexistent.yaml")
        assert reg.settings == {}


# ── Registration ──────────────────────────────────────────────────

class TestRegistration:
    def test_register_and_get(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        conn = _make_connector("slack")
        entry = reg.register(conn)
        assert isinstance(entry, ConnectorEntry)
        assert reg.get("slack") is entry

    def test_register_same_twice_raises(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_make_connector("slack"))

    def test_register_disabled_raises(self):
        os.environ["FEATURE_CONNECTORS"] = "false"
        feature_flags.reload_flags()

        reg = ConnectorRegistry("config/connectors.yaml")
        with pytest.raises(RuntimeError, match="FEATURE_CONNECTORS is disabled"):
            reg.register(_make_connector("slack"))

    def test_register_enabled_succeeds(self):
        os.environ["FEATURE_CONNECTORS"] = "true"
        feature_flags.reload_flags()

        reg = ConnectorRegistry("config/connectors.yaml")
        entry = reg.register(_make_connector("slack"))
        assert entry.manifest.id == "slack"


# ── Unregister ────────────────────────────────────────────────────

class TestUnregister:
    def test_unregister_removes(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        assert reg.unregister("slack") is True
        assert reg.get("slack") is None

    def test_unregister_unknown_returns_false(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        assert reg.unregister("nonexistent") is False


# ── Listing ───────────────────────────────────────────────────────

class TestListing:
    def test_list_connectors(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        reg.register(_make_connector("email"))
        assert len(reg.list_connectors()) == 2

    def test_list_active_only(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        reg.register(_make_connector("email"))
        reg.update_status("slack", ConnectorStatus.ACTIVE)
        # email stays REGISTERED

        active = reg.list_active()
        assert len(active) == 1
        assert active[0].manifest.id == "slack"


# ── Operations ────────────────────────────────────────────────────

class TestOperations:
    def test_get_operations(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        ops = reg.get_operations("slack")
        assert len(ops) == 1
        assert ops[0].id == "read_data"

    def test_get_operations_unknown_raises(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        with pytest.raises(KeyError, match="not found"):
            reg.get_operations("nonexistent")

    def test_get_operation_specific(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        op = reg.get_operation("slack", "read_data")
        assert op.id == "read_data"

    def test_get_operation_unknown_raises(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        with pytest.raises(KeyError, match="Operation.*not found"):
            reg.get_operation("slack", "nonexistent_op")


# ── Status ────────────────────────────────────────────────────────

class TestStatus:
    def test_update_status(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        reg.register(_make_connector("slack"))
        reg.update_status("slack", ConnectorStatus.ACTIVE)
        assert reg.get("slack").connector.status == ConnectorStatus.ACTIVE

    def test_update_status_unknown_raises(self):
        reg = ConnectorRegistry("config/connectors.yaml")
        with pytest.raises(KeyError, match="not found"):
            reg.update_status("nonexistent", ConnectorStatus.ACTIVE)
