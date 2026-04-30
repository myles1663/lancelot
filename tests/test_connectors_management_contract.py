import os

import pytest
import yaml
from cryptography.fernet import Fernet

from src.connectors.vault import CredentialVault
from src.connectors.registry import ConnectorRegistry
from src.core.connectors_api import (
    _instantiate_connector,
    register_connector_with_vault_access,
)


@pytest.fixture
def enable_connectors(monkeypatch):
    import src.core.feature_flags as ff

    monkeypatch.setattr(ff, "FEATURE_CONNECTORS", True)


@pytest.fixture
def vault(tmp_path):
    key = Fernet.generate_key().decode()
    os.environ["LANCELOT_VAULT_KEY"] = key
    config = {
        "version": "1.0",
        "storage": {
            "path": str(tmp_path / "credentials.enc"),
            "backup_path": str(tmp_path / "credentials.enc.bak"),
        },
        "encryption": {
            "key_env_var": "LANCELOT_VAULT_KEY",
        },
    }
    config_path = tmp_path / "vault.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    try:
        yield CredentialVault(config_path=str(config_path))
    finally:
        os.environ.pop("LANCELOT_VAULT_KEY", None)


@pytest.fixture
def registry(tmp_path, enable_connectors):
    config = {
        "version": "1.0",
        "connectors": {
            "slack": {"enabled": True},
            "generic_rest": {
                "enabled": True,
                "base_url": "https://api.example.com",
                "endpoints": [
                    {"path": "/v1/users", "method": "GET", "name": "List Users"},
                ],
                "auth_vault_key": "generic_rest.token",
                "auth_type": "bearer",
            },
        },
    }
    config_path = tmp_path / "connectors.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return ConnectorRegistry(str(config_path))


def test_register_connector_with_vault_access_restores_scoped_grants(registry, vault):
    vault.store("slack.bot_token", "xoxb-secret", type="oauth_token")

    connector = register_connector_with_vault_access(
        registry,
        vault,
        "slack",
        {"enabled": True},
    )

    assert connector is not None
    assert vault.access_policy.is_allowed("slack", "slack.bot_token") is True
    assert vault.retrieve("slack.bot_token", accessor_id="slack") == "xoxb-secret"


def test_generic_rest_instantiates_from_management_config():
    connector = _instantiate_connector(
        "generic_rest",
        {
            "base_url": "https://api.example.com",
            "endpoints": [
                {"path": "/v1/users", "method": "GET", "name": "List Users"},
            ],
            "auth_vault_key": "generic_rest.token",
            "auth_type": "bearer",
        },
    )

    assert connector is not None
    assert connector.manifest.id == "generic_rest"
    assert connector.manifest.target_domains == ["api.example.com"]
