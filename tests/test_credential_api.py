"""
Tests for Prompt 33: Credential Onboarding API.

Uses FastAPI TestClient. No real network calls.
"""

import os
import pytest
from typing import Any
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.connectors.base import ConnectorBase, ConnectorManifest, ConnectorStatus, CredentialSpec
from src.connectors.credential_api import router, init_credential_api
from src.connectors.models import ConnectorOperation, ConnectorResult, HTTPMethod
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault
from src.core import feature_flags


# ── Test Connector ────────────────────────────────────────────────

class _ApiTestConnector(ConnectorBase):
    def __init__(self):
        manifest = ConnectorManifest(
            id="apitest",
            name="API Test Connector",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            target_domains=["api.test.com"],
            required_credentials=[
                CredentialSpec(name="API Key", type="api_key", vault_key="apitest_key"),
                CredentialSpec(name="Secret", type="oauth_token", vault_key="apitest_secret"),
            ],
        )
        super().__init__(manifest)

    def get_operations(self):
        return []

    def execute(self, operation_id, params):
        return {}

    def validate_credentials(self):
        return True


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
def setup(tmp_path):
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

    vault = CredentialVault(config_path=str(cfg_path))
    registry = ConnectorRegistry("config/connectors.yaml")

    connector = _ApiTestConnector()
    registry.register(connector)

    init_credential_api(registry, vault)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    yield client, vault, registry

    os.environ.pop("LANCELOT_VAULT_KEY", None)
    init_credential_api(None, None)


# ── Tests ─────────────────────────────────────────────────────────

class TestCredentialAPI:
    def test_store_and_status_shows_present(self, setup):
        client, vault, _ = setup
        # Store
        resp = client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_key",
            "value": "sk-12345",
            "type": "api_key",
        })
        assert resp.status_code == 200
        assert resp.json()["stored"] is True

        # Status
        resp = client.get("/connectors/apitest/credentials/status")
        assert resp.status_code == 200
        creds = resp.json()["credentials"]
        key_cred = next(c for c in creds if c["vault_key"] == "apitest_key")
        assert key_cred["present"] is True

    def test_store_unknown_connector_404(self, setup):
        client, _, _ = setup
        resp = client.post("/connectors/nonexistent/credentials", json={
            "vault_key": "key",
            "value": "val",
        })
        assert resp.status_code == 404

    def test_store_undeclared_vault_key_400(self, setup):
        client, _, _ = setup
        resp = client.post("/connectors/apitest/credentials", json={
            "vault_key": "not_declared_key",
            "value": "val",
        })
        assert resp.status_code == 400

    def test_status_shows_missing(self, setup):
        client, _, _ = setup
        resp = client.get("/connectors/apitest/credentials/status")
        assert resp.status_code == 200
        creds = resp.json()["credentials"]
        assert all(c["present"] is False for c in creds)

    def test_delete_removes_credential(self, setup):
        client, _, _ = setup
        # Store first
        client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_key",
            "value": "sk-12345",
        })
        # Delete
        resp = client.delete("/connectors/apitest/credentials/apitest_key")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify gone
        resp = client.get("/connectors/apitest/credentials/status")
        key_cred = next(c for c in resp.json()["credentials"] if c["vault_key"] == "apitest_key")
        assert key_cred["present"] is False

    def test_validate_all_present(self, setup):
        client, _, _ = setup
        client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_key", "value": "v1",
        })
        client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_secret", "value": "v2",
        })
        resp = client.post("/connectors/apitest/credentials/validate")
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_validate_missing(self, setup):
        client, _, _ = setup
        resp = client.post("/connectors/apitest/credentials/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert "apitest_key" in data["missing"]

    def test_status_never_returns_values(self, setup):
        client, _, _ = setup
        client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_key", "value": "SUPER_SECRET_VALUE",
        })
        resp = client.get("/connectors/apitest/credentials/status")
        # The response should NOT contain the actual value
        assert "SUPER_SECRET_VALUE" not in resp.text
