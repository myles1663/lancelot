import importlib
import threading
from unittest.mock import MagicMock

import pytest
import yaml
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.auth_api as auth_api
import src.core.setup_api as setup_api_module
from src.connectors.vault import CredentialVault
from src.core.operator_identity import OperatorIdentity


def _build_app(
    tmp_data_dir,
    verify_request,
    *,
    connector_vault=None,
    connector_vault_error=None,
    connector_vault_config_path="config/vault.yaml",
):
    importlib.reload(setup_api_module)
    app = FastAPI()
    setup_api_module.init_setup_api(
        data_dir=str(tmp_data_dir),
        startup_time=0.0,
        audit_logger=MagicMock(),
        connector_vault=connector_vault,
        connector_vault_error=connector_vault_error,
        connector_vault_config_path=connector_vault_config_path,
        receipt_service=None,
        verify_request=verify_request,
    )
    app.include_router(setup_api_module.router)
    return app


def _make_vault_config(tmp_path):
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
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return str(config_path)


def _insert_session(token, capabilities):
    identity = OperatorIdentity(
        operator_id="op-123",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-10T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": identity,
        "capabilities": sorted(capabilities),
        "groups": [],
    }
    return identity


def _authenticate_client(client, token):
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), token)
    return client


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/api/setup/system-info", None),
        ("post", "/api/setup/restart", {"confirm": True}),
        ("get", "/api/setup/vault/status", None),
        ("get", "/api/setup/vault/keys", None),
        ("get", "/api/setup/export", None),
        ("post", "/api/setup/factory-reset", {"confirm": True, "confirmation_text": "RESET"}),
    ],
)
def test_setup_api_rejects_unauthenticated_requests(tmp_data_dir, method, path, json_body):
    app = _build_app(tmp_data_dir, verify_request=lambda request: False)
    client = TestClient(app)

    if json_body is None:
        response = getattr(client, method)(path)
    else:
        response = getattr(client, method)(path, json=json_body)

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def test_setup_api_fails_closed_when_auth_callback_missing(tmp_data_dir):
    importlib.reload(setup_api_module)
    app = FastAPI()
    setup_api_module.init_setup_api(
        data_dir=str(tmp_data_dir),
        startup_time=0.0,
        audit_logger=MagicMock(),
        connector_vault=None,
        receipt_service=None,
        verify_request=None,
    )
    app.include_router(setup_api_module.router)
    client = TestClient(app)

    response = client.get("/api/setup/system-info")

    assert response.status_code == 503
    assert response.json()["detail"] == "Setup API auth not configured"


def test_setup_api_audits_authenticated_operator(tmp_data_dir, monkeypatch):
    audit_logger = MagicMock()
    importlib.reload(setup_api_module)
    app = FastAPI()
    setup_api_module.init_setup_api(
        data_dir=str(tmp_data_dir),
        startup_time=0.0,
        audit_logger=audit_logger,
        connector_vault=None,
        receipt_service=None,
        verify_request=lambda request: request.headers.get("authorization") == "Bearer good-token",
    )
    app.include_router(setup_api_module.router)

    identity = OperatorIdentity(
        operator_id="op-123",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-10T00:00:00Z",
        auth_method="api_key",
        ip_address="127.0.0.1",
    )
    monkeypatch.setattr(auth_api, "resolve_operator_identity", lambda request: None)
    monkeypatch.setattr(auth_api, "get_api_key_identity", lambda request: identity)

    client = TestClient(app)
    response = client.post(
        "/api/setup/flags/reset",
        headers={"Authorization": "Bearer good-token"},
        json={"confirm": True},
    )

    assert response.status_code == 200
    audit_logger.log_event.assert_called_once_with(
        "SETUP_FLAGS_RESET",
        "Feature flags reset to defaults",
        user="Arthur",
    )


def test_setup_restart_rejects_unexpected_fields(tmp_data_dir):
    app = _build_app(tmp_data_dir, verify_request=lambda request: True)
    client = TestClient(app)
    token = "setup-admin"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "setup.admin"})
    _authenticate_client(client, token)

    response = client.post("/api/setup/restart", json={"confirm": True, "scope": "all"})

    assert response.status_code == 422


def test_setup_flags_reset_rejects_unexpected_fields(tmp_data_dir):
    app = _build_app(tmp_data_dir, verify_request=lambda request: True)
    client = TestClient(app)
    token = "setup-admin"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "setup.admin"})
    _authenticate_client(client, token)

    response = client.post("/api/setup/flags/reset", json={"confirm": True, "operator_id": "Mallory"})

    assert response.status_code == 422


def test_setup_factory_reset_rejects_unexpected_fields(tmp_data_dir):
    app = _build_app(tmp_data_dir, verify_request=lambda request: True)
    client = TestClient(app)
    token = "setup-admin"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "setup.admin"})
    _authenticate_client(client, token)

    response = client.post(
        "/api/setup/factory-reset",
        json={"confirm": True, "confirmation_text": "RESET", "debug": True},
    )

    assert response.status_code == 422


def test_setup_api_requires_admin_capability(tmp_data_dir, monkeypatch):
    app = _build_app(tmp_data_dir, verify_request=lambda request: True)
    client = TestClient(app)
    token = "limited-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.get("/api/setup/system-info")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: setup.admin"


def test_setup_system_info_surfaces_runtime_degradation(tmp_data_dir, monkeypatch):
    app = _build_app(tmp_data_dir, verify_request=lambda request: True)
    client = TestClient(app)
    token = "setup-admin"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "setup.admin"})
    _authenticate_client(client, token)

    def _raise_disk_usage(_path):
        raise RuntimeError("disk exploded")

    def _raise_platform_node():
        raise RuntimeError("hostname exploded")

    monkeypatch.setattr(setup_api_module.shutil, "disk_usage", _raise_disk_usage)
    monkeypatch.setattr(setup_api_module.platform, "node", _raise_platform_node)

    response = client.get("/api/setup/system-info")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_degraded"] is True
    assert "Disk usage unavailable" in body["degraded_reasons"]
    assert "Hostname unavailable" in body["degraded_reasons"]
    assert any("disk exploded" in err for err in body["runtime_errors"])
    assert any("hostname exploded" in err for err in body["runtime_errors"])


def test_setup_vault_status_reports_key_mismatch(tmp_data_dir, tmp_path, monkeypatch):
    original_key = Fernet.generate_key().decode()
    replacement_key = Fernet.generate_key().decode()
    config_path = _make_vault_config(tmp_path)

    monkeypatch.setenv("LANCELOT_VAULT_KEY", original_key)
    vault = CredentialVault(config_path=config_path)
    vault.store("persistent_key", "persistent_value")
    monkeypatch.setenv("LANCELOT_VAULT_KEY", replacement_key)

    app = _build_app(
        tmp_data_dir,
        verify_request=lambda request: True,
        connector_vault=None,
        connector_vault_error="Credential vault initialization failed: encrypted vault contents could not be decrypted from the primary or backup file.",
        connector_vault_config_path=config_path,
    )
    client = TestClient(app)
    token = "setup-admin"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "setup.admin"})
    _authenticate_client(client, token)

    response = client.get("/api/setup/vault/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "key_mismatch"
    assert body["suspected_key_mismatch"] is True
    assert body["metadata_present"] is True
    assert body["has_primary"] is True


def test_setup_vault_reset_archives_files_and_restarts(tmp_data_dir, tmp_path, monkeypatch):
    config_path = _make_vault_config(tmp_path)
    monkeypatch.setenv("LANCELOT_VAULT_KEY", Fernet.generate_key().decode())
    vault = CredentialVault(config_path=config_path)
    vault.store("first", "value1")
    vault.store("second", "value2")

    class _FakeTimer:
        def __init__(self, _delay, _callback):
            self.delay = _delay
            self.callback = _callback

        def start(self):
            return None

    app = _build_app(
        tmp_data_dir,
        verify_request=lambda request: True,
        connector_vault=vault,
        connector_vault_config_path=config_path,
    )
    client = TestClient(app)
    token = "setup-admin"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "setup.admin"})
    _authenticate_client(client, token)
    monkeypatch.setattr(threading, "Timer", _FakeTimer)

    exit_calls: list[int] = []
    monkeypatch.setattr(setup_api_module.os, "_exit", lambda code: exit_calls.append(code))

    response = client.post(
        "/api/setup/vault/reset",
        json={"confirm": True, "confirmation_text": "RESET CONNECTOR VAULT"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resetting"
    assert body["restart_required"] is True
    assert body["archived_files"]
    assert not (tmp_path / "credentials.enc").exists()
    assert not (tmp_path / "credentials.enc.bak").exists()
    assert not (tmp_path / "credentials.meta.json").exists()
    assert exit_calls == []


def test_setup_vault_reset_rejects_unexpected_fields(tmp_data_dir):
    app = _build_app(tmp_data_dir, verify_request=lambda request: True)
    client = TestClient(app)
    token = "setup-admin"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "setup.admin"})
    _authenticate_client(client, token)

    response = client.post(
        "/api/setup/vault/reset",
        json={"confirm": True, "confirmation_text": "RESET CONNECTOR VAULT", "debug": True},
    )

    assert response.status_code == 422
