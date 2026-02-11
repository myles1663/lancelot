"""
Tests for Prompt 34: Connector Integration Test + EchoConnector.

Full pipeline test: registry → classifier → proxy → governed proxy.
Network tests skipped if unavailable.
"""

import os
import pytest
from cryptography.fernet import Fernet
from unittest.mock import MagicMock, patch

from src.connectors.base import ConnectorStatus
from src.connectors.connectors.test_echo import EchoConnector
from src.connectors.governed_proxy import GovernedConnectorProxy
from src.connectors.models import ConnectorResult, HTTPMethod
from src.connectors.proxy import ConnectorProxy, DomainValidator
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault
from src.core import feature_flags
from src.core.governance.config import RiskClassificationConfig
from src.core.governance.models import RiskTier
from src.core.governance.risk_classifier import RiskClassifier


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
def full_pipeline(vault):
    """Build the complete connector pipeline."""
    registry = ConnectorRegistry("config/connectors.yaml")
    echo = EchoConnector()
    registry.register(echo)
    echo.set_status(ConnectorStatus.ACTIVE)

    classifier_config = RiskClassificationConfig(defaults={
        "connector.read": 0,
        "connector.write": 2,
        "connector.delete": 3,
    })
    classifier = RiskClassifier(classifier_config)

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
    governed.register_connector_tiers("echo")

    return {
        "registry": registry,
        "classifier": classifier,
        "proxy": proxy,
        "governed": governed,
        "echo": echo,
        "receipts": receipt_store,
        "batch": batch_buffer,
    }


# ── Component Initialization ─────────────────────────────────────

class TestComponentInit:
    def test_all_components_initialize(self, full_pipeline):
        assert full_pipeline["registry"] is not None
        assert full_pipeline["classifier"] is not None
        assert full_pipeline["proxy"] is not None
        assert full_pipeline["governed"] is not None

    def test_echo_manifest_validates(self):
        echo = EchoConnector()
        echo.manifest.validate()  # should not raise

    def test_echo_has_three_operations(self):
        echo = EchoConnector()
        assert len(echo.get_operations()) == 3

    def test_registry_accepts_registration(self, full_pipeline):
        entry = full_pipeline["registry"].get("echo")
        assert entry is not None
        assert entry.manifest.id == "echo"


# ── Risk Classification ──────────────────────────────────────────

class TestRiskClassification:
    def test_get_anything_is_t0(self, full_pipeline):
        classifier = full_pipeline["classifier"]
        profile = classifier.classify("connector.echo.get_anything")
        assert profile.tier == RiskTier.T0_INERT

    def test_post_data_is_t2(self, full_pipeline):
        classifier = full_pipeline["classifier"]
        profile = classifier.classify("connector.echo.post_data")
        assert profile.tier == RiskTier.T2_CONTROLLED


# ── Domain Validation ─────────────────────────────────────────────

class TestDomainValidation:
    def test_httpbin_allowed(self):
        assert DomainValidator.is_domain_allowed(
            "https://httpbin.org/anything", ["httpbin.org"]
        ) is True

    def test_evil_domain_blocked(self):
        assert DomainValidator.is_domain_allowed(
            "https://evil.com/steal", ["httpbin.org"]
        ) is False


# ── Governed Execution (mocked HTTP) ─────────────────────────────

class TestGovernedExecution:
    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_get_anything_succeeds(self, mock_execute, full_pipeline):
        mock_execute.return_value = MagicMock(
            operation_id="get_anything",
            connector_id="echo",
            status_code=200,
            success=True,
            receipt_id="",
        )
        governed = full_pipeline["governed"]
        resp = governed.execute_governed("echo", "get_anything", {})
        assert resp.success is True

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_post_data_succeeds(self, mock_execute, full_pipeline):
        mock_execute.return_value = MagicMock(
            operation_id="post_data",
            connector_id="echo",
            status_code=200,
            success=True,
            receipt_id="",
        )
        governed = full_pipeline["governed"]
        resp = governed.execute_governed("echo", "post_data", {"data": {"key": "val"}})
        assert resp.success is True

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_receipt_emitted(self, mock_execute, full_pipeline):
        mock_execute.return_value = MagicMock(
            operation_id="post_data",
            connector_id="echo",
            status_code=200,
            success=True,
            receipt_id="",
        )
        governed = full_pipeline["governed"]
        governed.execute_governed("echo", "post_data", {"data": {}})
        assert len(full_pipeline["receipts"]) == 1

    @patch("src.connectors.proxy.ConnectorProxy.execute")
    def test_t0_receipt_batch_buffer(self, mock_execute, full_pipeline):
        mock_execute.return_value = MagicMock(
            operation_id="get_anything",
            connector_id="echo",
            status_code=200,
            success=True,
            receipt_id="",
        )
        governed = full_pipeline["governed"]
        governed.execute_governed("echo", "get_anything", {})
        # T0 → batch buffer, not receipt store
        assert len(full_pipeline["batch"]) == 1
        assert len(full_pipeline["receipts"]) == 0
