"""
Tests for Prompt 33: Credential Onboarding API.

Uses FastAPI TestClient. No real network calls.
"""

import os
import logging
import pytest
import types
from typing import Any
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.connectors.base import ConnectorBase, ConnectorManifest, ConnectorStatus, CredentialSpec
from src.connectors.credential_api import router, init_credential_api
from src.connectors.models import ConnectorOperation, ConnectorResult, HTTPMethod
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault
from src.core.api_auth import init_api_auth
from src.connectors import credential_api as credential_api_module
import src.core.connectors_api as connectors_api
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
    init_api_auth(lambda request: True)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    yield client, vault, registry

    os.environ.pop("LANCELOT_VAULT_KEY", None)
    init_credential_api(None, None)
    init_api_auth(None)


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

    def test_store_rejects_unexpected_fields(self, setup):
        client, _, _ = setup
        resp = client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_key",
            "value": "sk-12345",
            "unexpected": "deny-me",
        })
        assert resp.status_code == 422

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

    def test_validate_logs_connector_validation_failure(self, setup, caplog):
        client, _, registry = setup
        entry = registry.get("apitest")
        entry.connector.validate_credentials = lambda: (_ for _ in ()).throw(RuntimeError("validation exploded"))

        client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_key", "value": "v1",
        })
        client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_secret", "value": "v2",
        })

        with caplog.at_level(logging.WARNING):
            resp = client.post("/connectors/apitest/credentials/validate")

        assert resp.status_code == 200
        assert resp.json()["valid"] is False
        assert resp.json()["error"] == "validation exploded"
        assert "Credential validation failed for connector apitest" in caplog.text

    def test_status_never_returns_values(self, setup):
        client, _, _ = setup
        client.post("/connectors/apitest/credentials", json={
            "vault_key": "apitest_key", "value": "SUPER_SECRET_VALUE",
        })
        resp = client.get("/connectors/apitest/credentials/status")
        # The response should NOT contain the actual value
        assert "SUPER_SECRET_VALUE" not in resp.text

    def test_disabled_connector_can_onboard_credentials_via_config_lazy_load(self, tmp_path):
        import yaml

        key = Fernet.generate_key().decode()
        os.environ["LANCELOT_VAULT_KEY"] = key

        vault_cfg = {
            "version": "1.0",
            "storage": {"path": str(tmp_path / "cred.enc"), "backup_path": str(tmp_path / "cred.bak")},
            "encryption": {"key_env_var": "LANCELOT_VAULT_KEY"},
            "audit": {"log_access": False},
        }
        vault_cfg_path = tmp_path / "vault.yaml"
        with open(vault_cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(vault_cfg, f)
        vault = CredentialVault(config_path=str(vault_cfg_path))

        connector_cfg = {
            "version": "1.0",
            "connectors": {
                "generic_rest": {
                    "enabled": False,
                    "base_url": "https://api.example.com",
                    "endpoints": [{"path": "/v1/users", "method": "GET", "name": "List Users"}],
                    "auth_vault_key": "generic_rest.token",
                    "auth_type": "bearer",
                },
            },
        }
        connector_cfg_path = tmp_path / "connectors.yaml"
        with open(connector_cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(connector_cfg, f)

        registry = ConnectorRegistry(str(connector_cfg_path))
        init_credential_api(registry, vault)
        init_api_auth(lambda request: True)

        old_connectors_api_config = connectors_api._config_path
        connectors_api._config_path = str(connector_cfg_path)
        try:
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            resp = client.get("/connectors/generic_rest/credentials/status")
            assert resp.status_code == 200
            creds = resp.json()["credentials"]
            assert len(creds) == 1
            assert creds[0]["vault_key"] == "generic_rest.token"

            resp = client.post("/connectors/generic_rest/credentials", json={
                "vault_key": "generic_rest.token",
                "value": "secret-token",
                "type": "api_key",
            })
            assert resp.status_code == 200
            assert vault.retrieve("generic_rest.token", accessor_id="generic_rest") == "secret-token"
        finally:
            connectors_api._config_path = old_connectors_api_config
            os.environ.pop("LANCELOT_VAULT_KEY", None)
            init_credential_api(None, None)
            init_api_auth(None)

    def test_uninitialized_and_lazy_registration_failure_paths(self, monkeypatch):
        init_credential_api(None, None)
        with pytest.raises(HTTPException) as uninitialized:
            credential_api_module._resolve_connector_entry("missing")
        assert uninitialized.value.status_code == 500

        registry = types.SimpleNamespace(
            _config={"connectors": {"lazy": {"enabled": False}}},
            get=lambda connector_id: None,
        )
        init_credential_api(registry, object())

        monkeypatch.setattr(
            "src.core.connectors_api.register_connector_with_vault_access",
            lambda *args, **kwargs: None,
        )
        with pytest.raises(HTTPException) as not_found:
            credential_api_module._resolve_connector_entry("lazy")
        assert not_found.value.status_code == 404

        monkeypatch.setattr(
            "src.core.connectors_api.register_connector_with_vault_access",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad config")),
        )
        with pytest.raises(HTTPException) as failed:
            credential_api_module._resolve_connector_entry("lazy")
        assert failed.value.status_code == 500

        with pytest.raises(HTTPException) as missing:
            credential_api_module._resolve_connector_entry("absent")
        assert missing.value.status_code == 404

    def test_workspace_path_hot_swap_updates_compose_and_survives_restart_errors(self, tmp_path, monkeypatch):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  lancelot-core:\n"
            "    volumes:\n"
            "      - /old/workspace:/home/lancelot/workspace\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(credential_api_module, "_COMPOSE_PATH", compose)

        started = []

        class ImmediateTimer:
            def __init__(self, delay, target):
                self.delay = delay
                self.target = target

            def start(self):
                started.append(self.delay)
                self.target()

        monkeypatch.setattr(credential_api_module.threading, "Timer", ImmediateTimer)
        monkeypatch.setattr(
            credential_api_module.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("docker unavailable")),
        )

        credential_api_module._apply_workspace_path("/new/workspace")

        assert "/new/workspace:/home/lancelot/workspace" in compose.read_text(encoding="utf-8")
        assert started == [1.0]

    def test_workspace_path_hot_swap_noops_without_compose_or_mount(self, tmp_path, monkeypatch):
        missing = tmp_path / "missing-compose.yml"
        monkeypatch.setattr(credential_api_module, "_COMPOSE_PATH", missing)
        credential_api_module._apply_workspace_path("/new/workspace")

        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  lancelot-core:\n    image: lancelot\n", encoding="utf-8")
        monkeypatch.setattr(credential_api_module, "_COMPOSE_PATH", compose)
        credential_api_module._apply_workspace_path("/new/workspace")

        assert "/new/workspace" not in compose.read_text(encoding="utf-8")

    def test_endpoint_functions_fail_when_runtime_dependencies_are_missing(self):
        init_credential_api(None, None)
        body = credential_api_module.StoreCredentialRequest(vault_key="key", value="value")
        request = types.SimpleNamespace()

        for call in (
            lambda: credential_api_module.store_credential("missing", body, request),
            lambda: credential_api_module.credential_status("missing"),
            lambda: credential_api_module.delete_credential("missing", "key", request),
            lambda: credential_api_module.validate_credentials("missing"),
        ):
            with pytest.raises(HTTPException) as exc:
                call()
            assert exc.value.status_code == 500

    def test_workspace_path_preserves_mount_line_newline_and_store_survives_hotswap_failure(self, setup, tmp_path, monkeypatch):
        client, vault, _registry = setup
        compose = tmp_path / "docker-compose.yml"
        compose.write_text('services:\n  lancelot-core:\n    volumes:\n      - "/old:/home/lancelot/workspace"', encoding="utf-8")
        monkeypatch.setattr(credential_api_module, "_COMPOSE_PATH", compose)
        monkeypatch.setattr(
            credential_api_module,
            "_apply_workspace_path",
            lambda value: (_ for _ in ()).throw(RuntimeError("compose locked")),
        )

        entry = _registry.get("apitest")
        entry.manifest.required_credentials.append(
            CredentialSpec(name="Workspace", type="path", vault_key="shared_workspace.host_path")
        )

        resp = client.post("/connectors/apitest/credentials", json={
            "vault_key": "shared_workspace.host_path",
            "value": str(tmp_path / "workspace"),
            "type": "path",
        })

        assert resp.status_code == 200
        assert vault.exists("shared_workspace.host_path") is True
