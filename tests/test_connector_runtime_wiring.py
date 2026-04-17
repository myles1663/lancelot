import os
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from src.connectors.base import ConnectorStatus
from src.connectors.connectors.test_echo import EchoConnector
from src.connectors.runtime import ConnectorRuntime
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault
from src.core import feature_flags
from src.core.governance.config import RiskClassificationConfig
from src.core.governance.risk_classifier import RiskClassifier


def _make_vault(tmp_path):
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
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f)
    return CredentialVault(config_path=str(cfg_path))


def test_connector_runtime_executes_capability_through_governed_proxy(tmp_path):
    old = os.environ.get("FEATURE_CONNECTORS")
    os.environ["FEATURE_CONNECTORS"] = "true"
    feature_flags.reload_flags()
    try:
        registry = ConnectorRegistry("config/connectors.yaml")
        vault = _make_vault(tmp_path)
        connector = EchoConnector()
        registry.register(connector)
        connector.set_status(ConnectorStatus.ACTIVE)

        classifier = RiskClassifier(
            RiskClassificationConfig(
                defaults={
                    "connector.read": 0,
                    "connector.write": 2,
                    "connector.delete": 3,
                }
            )
        )
        runtime = ConnectorRuntime(registry, vault, classifier)
        runtime.register_connector("echo")

        with patch("src.connectors.proxy.ConnectorProxy.execute") as mock_execute:
            mock_execute.return_value = MagicMock(
                success=True,
                connector_id="echo",
                operation_id="get_anything",
                status_code=200,
                body={"ok": True},
                headers={},
                receipt_id="receipt-1",
            )
            response = runtime.execute_capability("connector.echo.get_anything", {})
            assert response.success is True
            assert response.connector_id == "echo"
            assert response.operation_id == "get_anything"
            assert mock_execute.called
    finally:
        if old is None:
            os.environ.pop("FEATURE_CONNECTORS", None)
        else:
            os.environ["FEATURE_CONNECTORS"] = old
        feature_flags.reload_flags()
        os.environ.pop("LANCELOT_VAULT_KEY", None)


def test_connector_runtime_builds_fallback_classifier_when_missing(tmp_path):
    old = os.environ.get("FEATURE_CONNECTORS")
    os.environ["FEATURE_CONNECTORS"] = "true"
    feature_flags.reload_flags()
    try:
        registry = ConnectorRegistry("config/connectors.yaml")
        vault = _make_vault(tmp_path)
        connector = EchoConnector()
        registry.register(connector)
        connector.set_status(ConnectorStatus.ACTIVE)

        runtime = ConnectorRuntime(registry, vault, risk_classifier=None)
        runtime.register_connector("echo")

        assert "connector.echo.get_anything" in runtime.governed_proxy._classifier.known_capabilities
    finally:
        if old is None:
            os.environ.pop("FEATURE_CONNECTORS", None)
        else:
            os.environ["FEATURE_CONNECTORS"] = old
        feature_flags.reload_flags()
        os.environ.pop("LANCELOT_VAULT_KEY", None)
