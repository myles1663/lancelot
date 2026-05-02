import importlib
import threading
import zipfile
from types import SimpleNamespace
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


def _admin_client(tmp_data_dir, *, connector_vault=None, receipt_service=None):
    importlib.reload(setup_api_module)
    app = FastAPI()
    setup_api_module.init_setup_api(
        data_dir=str(tmp_data_dir),
        startup_time=0.0,
        audit_logger=MagicMock(),
        connector_vault=connector_vault,
        receipt_service=receipt_service,
        verify_request=lambda request: True,
    )
    app.include_router(setup_api_module.router)
    client = TestClient(app)
    auth_api._sessions.clear()
    _insert_session("setup-admin", {"warroom.login", "setup.admin"})
    return _authenticate_client(client, "setup-admin")


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


def test_setup_logs_vault_keys_masked_and_delete_paths(tmp_data_dir, tmp_path):
    class Vault:
        def __init__(self):
            self.values = {"short": "abcd", "long": "abcdef123456"}

        def health_snapshot(self, last_error=None):
            return SimpleNamespace(to_dict=lambda: {"status": "ok", "last_error": last_error})

        def list_entry_metadata(self):
            return [
                {"key": "short", "type": "secret", "created_at": "now"},
                {"key": "long", "type": "token", "created_at": "later"},
            ]

        def retrieve(self, key):
            return self.values.get(key)

        def delete(self, key):
            return self.values.pop(key, None) is not None

    audit_log = tmp_data_dir / "audit.log"
    audit_log.write_text("a\nb\nc\n", encoding="utf-8")
    client = _admin_client(tmp_data_dir, connector_vault=Vault())

    assert client.get("/api/setup/logs?file=audit&lines=2").json()["lines"] == ["b", "c"]
    assert client.get("/api/setup/logs?file=missing").status_code == 400
    assert client.get("/api/setup/logs?file=vault").json()["file"] == "vault"

    keys = client.get("/api/setup/vault/keys").json()
    assert keys["total"] == 2
    masked = client.get("/api/setup/vault/masked").json()
    assert masked["keys"][0]["masked_value"] == "****"
    assert masked["keys"][1]["masked_value"] == "abcd****3456"

    deleted = client.delete("/api/setup/vault/keys/short")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "key": "short"}
    assert client.delete("/api/setup/vault/keys/missing").status_code == 404


def test_setup_vault_and_log_error_paths(tmp_data_dir, monkeypatch):
    class BrokenVault:
        def health_snapshot(self, last_error=None):
            raise RuntimeError("vault health failed")

        def list_entry_metadata(self):
            raise RuntimeError("metadata failed")

        def delete(self, key):
            raise RuntimeError("delete failed")

    client = _admin_client(tmp_data_dir, connector_vault=BrokenVault())
    monkeypatch.setattr(setup_api_module.Path, "read_text", lambda *_, **__: (_ for _ in ()).throw(OSError("read failed")))
    (tmp_data_dir / "audit.log").write_text("x", encoding="utf-8")

    assert client.get("/api/setup/logs?file=audit").status_code == 500
    assert client.get("/api/setup/vault/status").status_code == 500
    assert client.get("/api/setup/vault/keys").status_code == 500
    assert client.get("/api/setup/vault/masked").status_code == 500
    assert client.delete("/api/setup/vault/keys/key").status_code == 500


def test_setup_receipt_clear_paths(tmp_data_dir):
    assert _admin_client(tmp_data_dir).post("/api/setup/receipts/clear", json={"confirm": False}).status_code == 400
    assert _admin_client(tmp_data_dir).post("/api/setup/receipts/clear", json={"confirm": True}).status_code == 400

    unsupported = _admin_client(tmp_data_dir, receipt_service=object())
    assert unsupported.post("/api/setup/receipts/clear", json={"confirm": True}).status_code == 501

    cleared = []
    service = SimpleNamespace(clear=lambda: cleared.append("cleared"))
    response = _admin_client(tmp_data_dir, receipt_service=service).post(
        "/api/setup/receipts/clear",
        json={"confirm": True},
    )
    assert response.json()["status"] == "cleared"
    assert cleared == ["cleared"]

    broken = SimpleNamespace(clear=lambda: (_ for _ in ()).throw(RuntimeError("clear failed")))
    assert _admin_client(tmp_data_dir, receipt_service=broken).post(
        "/api/setup/receipts/clear",
        json={"confirm": True},
    ).status_code == 500


def test_setup_reload_export_purge_and_flags_reset_paths(tmp_data_dir, tmp_path, monkeypatch):
    client = _admin_client(tmp_data_dir)

    # Config reload should report per-subsystem failures without failing the endpoint.
    response = client.post("/api/setup/config/reload")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "reloaded"
    assert set(body["results"]) == {"feature_flags", "scheduler", "connectors"}

    (tmp_data_dir / "soul").mkdir()
    (tmp_data_dir / "soul" / "active.yaml").write_text("soul: true", encoding="utf-8")
    (tmp_data_dir / "core_blocks.json").write_text("[]", encoding="utf-8")
    (tmp_data_dir / ".flag_state.json").write_text("{}", encoding="utf-8")
    (tmp_data_dir / "scheduler").mkdir()
    (tmp_data_dir / "scheduler" / "jobs.json").write_text("[]", encoding="utf-8")

    export = client.get("/api/setup/export")
    assert export.status_code == 200
    archive = tmp_path / "backup.zip"
    archive.write_bytes(export.content)
    with zipfile.ZipFile(archive) as zf:
        assert {"soul/active.yaml", "memory/core_blocks.json", "flags/.flag_state.json", "scheduler/jobs.json"}.issubset(
            set(zf.namelist())
        )

    (tmp_data_dir / "memory.db").write_text("db", encoding="utf-8")
    (tmp_data_dir / "memory.sqlite").write_text("db", encoding="utf-8")
    purge = client.post("/api/setup/memory/purge", json={"confirm": True})
    assert set(purge.json()["purged_files"]) == {"core_blocks.json", "memory.db", "memory.sqlite"}

    reset_flags = client.post("/api/setup/flags/reset", json={"confirm": True})
    assert reset_flags.status_code == 200
    assert "state file deleted" in reset_flags.json()["message"]


def test_setup_factory_reset_handles_partial_delete_failure(tmp_data_dir, monkeypatch):
    (tmp_data_dir / "file.txt").write_text("data", encoding="utf-8")
    keep_dir = tmp_data_dir / "keep"
    keep_dir.mkdir()
    (keep_dir / "child.txt").write_text("data", encoding="utf-8")

    monkeypatch.setattr(setup_api_module.shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("busy")))
    client = _admin_client(tmp_data_dir)

    response = client.post(
        "/api/setup/factory-reset",
        json={"confirm": True, "confirmation_text": "RESET"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reset_complete"
    assert keep_dir.exists()
    assert not (tmp_data_dir / "file.txt").exists()
