"""
Tests for Prompt 32: GovernedConnectorProxy.

Uses real GovernanceConfig and RiskClassifier. No mocks for governance.
Mocks HTTP layer only (no real network calls).
"""

import os
import json
import pytest
from typing import Any
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet

from src.connectors.base import ConnectorBase, ConnectorManifest, ConnectorStatus, CredentialSpec
from src.connectors.governed_proxy import GovernedConnectorProxy
from src.connectors.models import ConnectorOperation, ConnectorResult, HTTPMethod
from src.connectors.proxy import ConnectorProxy
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault
from src.core import feature_flags
from src.core.governance.config import RiskClassificationConfig
from src.core.governance.models import RiskTier
from src.core.governance.risk_classifier import RiskClassifier


# ── Test Connector ────────────────────────────────────────────────

class _GovTestConnector(ConnectorBase):
    def __init__(self, manifest=None):
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

    def get_operations(self):
        return [
            ConnectorOperation(
                id="read_data",
                connector_id=self.manifest.id,
                capability="connector.read",
                name="Read Data",
                default_tier=RiskTier.T0_INERT,
            ),
            ConnectorOperation(
                id="write_data",
                connector_id=self.manifest.id,
                capability="connector.write",
                name="Write Data",
                default_tier=RiskTier.T2_CONTROLLED,
            ),
            ConnectorOperation(
                id="send_message",
                connector_id=self.manifest.id,
                capability="connector.write",
                name="Send Message",
                default_tier=RiskTier.T2_CONTROLLED,
            ),
        ]

    def execute(self, operation_id, params):
        return ConnectorResult(
            operation_id=operation_id,
            connector_id=self.manifest.id,
            method=HTTPMethod.GET if operation_id == "read_data" else HTTPMethod.POST,
            url=f"https://api.test.com/v1/{operation_id}",
            body=params if operation_id != "read_data" else None,
        )

    def validate_credentials(self):
        return True


class _FailingConnector(_GovTestConnector):
    def execute(self, operation_id, params):
        raise RuntimeError("connector execution blocked")


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def enable_connectors():
    old = os.environ.get("FEATURE_CONNECTORS")
    os.environ["FEATURE_CONNECTORS"] = "true"
    feature_flags.reload_flags()
    yield
    if old is None:
        os.environ.pop("FEATURE_CONNECTORS", None)
    else:
        os.environ["FEATURE_CONNECTORS"] = old
    feature_flags.reload_flags()


@pytest.fixture
def vault(tmp_path):
    import yaml
    key = Fernet.generate_key().decode()
    os.environ["LANCELOT_VAULT_KEY"] = key
    config = {
        "version": "1.0",
        "storage": {"path": str(tmp_path / "cred.enc"), "backup_path": str(tmp_path / "cred.bak")},
        "encryption": {"key_env_var": "LANCELOT_VAULT_KEY"},
        "audit": {"log_access": False},
    }
    cfg_path = tmp_path / "vault.yaml"
    with open(cfg_path, "w") as f:
        yaml.dump(config, f)
    v = CredentialVault(config_path=str(cfg_path))
    yield v
    os.environ.pop("LANCELOT_VAULT_KEY", None)


@pytest.fixture
def registry():
    return ConnectorRegistry("config/connectors.yaml")


@pytest.fixture
def classifier():
    config = RiskClassificationConfig(
        defaults={
            "connector.read": 0,
            "connector.write": 2,
            "connector.delete": 3,
        }
    )
    return RiskClassifier(config)


@pytest.fixture
def governed_setup(registry, vault, classifier, tmp_path, monkeypatch):
    """Create full governed proxy stack."""
    monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path / "data"))
    connector = _GovTestConnector()
    registry.register(connector)
    connector.set_status(ConnectorStatus.ACTIVE)

    proxy = ConnectorProxy(registry, vault)
    receipt_store = []
    batch_buffer = []

    governed = GovernedConnectorProxy(
        proxy=proxy,
        registry=registry,
        risk_classifier=classifier,
        receipt_store=receipt_store,
        batch_buffer=batch_buffer,
    )
    governed.register_connector_tiers("test")

    return governed, receipt_store, batch_buffer


# ── Tests ─────────────────────────────────────────────────────────

class TestGovernedConnectorProxy:
    def test_initializes(self, registry, vault, classifier):
        proxy = ConnectorProxy(registry, vault)
        governed = GovernedConnectorProxy(
            proxy=proxy,
            registry=registry,
            risk_classifier=classifier,
        )
        assert governed is not None

    def test_register_connector_tiers(self, governed_setup, classifier):
        governed, _, _ = governed_setup
        # After registration, classifier should know the connector operations
        assert "connector.test.read_data" in classifier.known_capabilities
        assert "connector.test.write_data" in classifier.known_capabilities

    def test_classifier_knows_capability(self, governed_setup, classifier):
        governed, _, _ = governed_setup
        profile = classifier.classify("connector.test.read_data")
        assert profile.tier == RiskTier.T0_INERT

    def test_get_operation_tier(self, governed_setup):
        governed, _, _ = governed_setup
        tier = governed.get_operation_tier("test", "read_data")
        assert tier == RiskTier.T0_INERT
        tier = governed.get_operation_tier("test", "write_data")
        assert tier == RiskTier.T2_CONTROLLED

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_execute_governed_produces_receipt(self, mock_execute, governed_setup):
        governed, receipt_store, batch_buffer = governed_setup
        mock_execute.return_value = MagicMock(
            operation_id="write_data",
            connector_id="test",
            status_code=200,
            success=True,
            receipt_id="",
        )

        resp = governed.execute_governed("test", "write_data", {"key": "value"})
        assert resp.success is True
        # T2 operation → receipt in receipt_store (not batch)
        assert len(receipt_store) == 1
        assert receipt_store[0]["capability"] == "connector.test.write_data"
        assert receipt_store[0]["tier"] == "T2_CONTROLLED"

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_t0_receipts_go_to_batch_buffer(self, mock_execute, governed_setup):
        governed, receipt_store, batch_buffer = governed_setup
        mock_execute.return_value = MagicMock(
            operation_id="read_data",
            connector_id="test",
            status_code=200,
            success=True,
            receipt_id="",
        )

        resp = governed.execute_governed("test", "read_data", {})
        assert resp.success is True
        # T0 → batch buffer
        assert len(batch_buffer) == 1
        assert len(receipt_store) == 0
        assert batch_buffer[0]["tier"] == "T0_INERT"

    def test_execute_governed_unknown_connector(self, governed_setup):
        governed, _, _ = governed_setup
        resp = governed.execute_governed("nonexistent", "read", {})
        assert resp.success is False
        assert "not found" in resp.error

    def test_execute_governed_unknown_operation(self, governed_setup):
        governed, _, _ = governed_setup
        resp = governed.execute_governed("test", "nonexistent_op", {})
        assert resp.success is False

    def test_execute_governed_returns_structured_error_on_connector_exception(
        self, registry, vault, classifier
    ):
        connector = _FailingConnector()
        registry.register(connector)

        proxy = ConnectorProxy(registry, vault)
        governed = GovernedConnectorProxy(
            proxy=proxy,
            registry=registry,
            risk_classifier=classifier,
        )
        governed.register_connector_tiers("test")

        resp = governed.execute_governed("test", "read_data", {})
        assert resp.success is False
        assert resp.error == "connector execution blocked"

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_execute_governed_with_policy_denial(self, mock_execute, registry, vault, classifier):
        """Test that a policy engine denial returns error."""
        connector = _GovTestConnector()
        registry.register(connector)

        proxy = ConnectorProxy(registry, vault)

        # Mock policy engine that denies everything
        mock_policy = MagicMock()
        mock_decision = MagicMock()
        mock_decision.allowed = False
        mock_decision.reasons = ["Denied by test policy"]
        mock_policy.evaluate_intent.return_value = mock_decision

        governed = GovernedConnectorProxy(
            proxy=proxy,
            registry=registry,
            risk_classifier=classifier,
            policy_engine=mock_policy,
        )
        governed.register_connector_tiers("test")

        resp = governed.execute_governed("test", "read_data", {})
        assert resp.success is False
        assert "Policy denied" in resp.error

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_connector_policy_blocks_unverified_recipient(
        self, mock_execute, governed_setup, classifier
    ):
        governed, receipt_store, _ = governed_setup
        classifier.update_soul({
            "connector_policies": {
                "test": {
                    "verified_recipients": ["*@allowed.example"],
                    "max_sends_per_day": 10,
                    "require_content_verification": True,
                    "pii_scrubbing_required": True,
                    "approval_required_for_send": True,
                },
            },
        })

        resp = governed.execute_governed("test", "send_message", {
            "to": "person@blocked.example",
            "content_verified": True,
            "pii_scrubbed": True,
            "approved": True,
        })

        assert resp.success is False
        assert "recipient" in resp.error
        assert len(receipt_store) == 1
        assert receipt_store[0]["success"] is False
        assert "Soul policy denied" in receipt_store[0]["error"]
        mock_execute.assert_not_called()

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_soul_policy_denial_records_trust_failure(
        self, mock_execute, registry, vault, classifier, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LANCELOT_DATA_DIR", str(tmp_path / "data"))
        connector = _GovTestConnector()
        registry.register(connector)
        proxy = ConnectorProxy(registry, vault)
        trust_ledger = MagicMock()
        classifier.update_soul({
            "connector_policies": {
                "test": {
                    "verified_recipients": ["*@allowed.example"],
                },
            },
        })
        governed = GovernedConnectorProxy(
            proxy=proxy,
            registry=registry,
            risk_classifier=classifier,
            trust_ledger=trust_ledger,
        )
        governed.register_connector_tiers("test")

        resp = governed.execute_governed("test", "send_message", {
            "to": "person@blocked.example",
        })

        assert resp.success is False
        trust_ledger.record_failure.assert_called_once_with(
            "connector.test.send_message",
            "external",
        )
        mock_execute.assert_not_called()

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_connector_policy_allows_verified_approved_send(
        self, mock_execute, governed_setup, classifier
    ):
        governed, _, _ = governed_setup
        classifier.update_soul({
            "connector_policies": {
                "test": {
                    "verified_recipients": ["*@allowed.example"],
                    "max_sends_per_day": 10,
                    "require_content_verification": True,
                    "pii_scrubbing_required": True,
                    "approval_required_for_send": True,
                },
            },
        })
        mock_execute.return_value = MagicMock(
            operation_id="send_message",
            connector_id="test",
            status_code=200,
            success=True,
            receipt_id="",
        )

        resp = governed.execute_governed("test", "send_message", {
            "to": "person@allowed.example",
            "content_verified": True,
            "pii_scrubbed": True,
            "approved": True,
        })

        assert resp.success is True
        mock_execute.assert_called_once()

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_connector_policy_enforces_durable_daily_send_cap(
        self, mock_execute, governed_setup, classifier, tmp_path
    ):
        governed, receipt_store, _ = governed_setup
        classifier.update_soul({
            "connector_policies": {
                "test": {
                    "verified_recipients": ["*@allowed.example"],
                    "max_sends_per_day": 1,
                    "require_content_verification": True,
                    "pii_scrubbing_required": True,
                    "approval_required_for_send": True,
                },
            },
        })
        mock_execute.return_value = MagicMock(
            operation_id="send_message",
            connector_id="test",
            status_code=200,
            success=True,
            receipt_id="",
        )
        params = {
            "to": "person@allowed.example",
            "content_verified": True,
            "pii_scrubbed": True,
            "approved": True,
        }

        first = governed.execute_governed("test", "send_message", params)
        second = governed.execute_governed("test", "send_message", params)

        assert first.success is True
        assert second.success is False
        assert "max_sends_per_day exceeded" in second.error
        assert mock_execute.call_count == 1
        assert len(receipt_store) == 2
        assert receipt_store[-1]["success"] is False
        counter_path = (
            tmp_path
            / "data"
            / "connectors"
            / "soul_connector_daily_sends.json"
        )
        counts = json.loads(counter_path.read_text(encoding="utf-8"))
        today_counts = next(iter(counts.values()))
        assert today_counts["test"] == 1

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_external_transmission_rule_blocks_missing_approval(
        self, mock_execute, governed_setup, classifier
    ):
        governed, _, _ = governed_setup
        classifier.update_soul({
            "external_transmission_rules": [
                {
                    "name": "message_send",
                    "applies_to": ["connector.test.send_message"],
                    "requires_approval_tier": "T3",
                    "pii_scrubbing_required": True,
                },
            ],
        })

        resp = governed.execute_governed("test", "send_message", {
            "to": "person@example.com",
            "pii_scrubbed": True,
        })

        assert resp.success is False
        assert "requires approval" in resp.error
        mock_execute.assert_not_called()

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_external_transmission_rule_allows_approved_scrubbed_send(
        self, mock_execute, governed_setup, classifier
    ):
        governed, _, _ = governed_setup
        classifier.update_soul({
            "external_transmission_rules": [
                {
                    "name": "message_send",
                    "applies_to": ["connector.test.send_message"],
                    "requires_approval_tier": "T3",
                    "pii_scrubbing_required": True,
                },
            ],
        })
        mock_execute.return_value = MagicMock(
            operation_id="send_message",
            connector_id="test",
            status_code=200,
            success=True,
            receipt_id="",
        )

        resp = governed.execute_governed("test", "send_message", {
            "to": "person@example.com",
            "approved": True,
            "approval_tier": "T3",
            "pii_scrubbed": True,
        })

        assert resp.success is True
        mock_execute.assert_called_once()
