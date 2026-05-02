from __future__ import annotations

import sys
import types

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.connectors.base import ConnectorManifest, CredentialSpec
from src.core import api_auth, auth_api, connectors_api
from src.core.operator_identity import OperatorIdentity


class _FakeConnector:
    def __init__(self, backend=None):
        self.backend = backend
        self.manifest = ConnectorManifest(
            id="fake",
            name="Fake Connector",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Test connector",
            target_domains=["api.example.com"],
            required_credentials=[
                CredentialSpec(name="Token", type="api_key", vault_key="fake.token"),
            ],
            data_reads=["records"],
            data_writes=["records"],
            does_not_access=["secrets"],
        )
        self.status = None

    def attach_vault(self, vault):
        self.vault = vault

    def validate_credentials(self):
        return bool(self.vault.exists("fake.token"))

    def set_status(self, status):
        self.status = status

    def get_operations(self):
        return [types.SimpleNamespace(id="op")]


class _Registry:
    def __init__(self):
        self.items = {}
        self.unregistered = []

    def register(self, connector):
        self.items[connector.manifest.id] = connector

    def get(self, connector_id):
        return self.items.get(connector_id)

    def unregister(self, connector_id):
        self.unregistered.append(connector_id)
        self.items.pop(connector_id, None)


class _Vault:
    def __init__(self, present=True):
        self.present = present
        self.grants = []
        self.revoked = []

    def exists(self, key):
        return self.present and key == "fake.token"

    def grant_connector_access(self, connector_id, manifest):
        self.grants.append((connector_id, manifest.id))

    def revoke_connector_access(self, connector_id):
        self.revoked.append(connector_id)


def _client(tmp_path, monkeypatch, *, config=None, registry=None, vault=None):
    auth_api._sessions.clear()
    api_auth.init_api_auth(lambda request: True)
    auth_api._sessions["connectors-admin"] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-20T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": ["warroom.login", "connectors.admin"],
        "groups": [],
    }
    config_path = tmp_path / "connectors.yaml"
    config_path.write_text(yaml.safe_dump(config or {"connectors": {}}), encoding="utf-8")

    fake_module = types.ModuleType("fake_connector")
    fake_module.FakeConnector = _FakeConnector
    monkeypatch.setitem(sys.modules, "fake_connector", fake_module)
    monkeypatch.setattr(
        connectors_api,
        "_CONNECTOR_CLASSES",
        {"fake": ("fake_connector", "FakeConnector", {})},
    )
    monkeypatch.setattr(connectors_api, "_BACKEND_OPTIONS", {"fake": ["a", "b"]})
    monkeypatch.setattr(connectors_api, "is_google_connector_enabled", lambda *args: True)
    monkeypatch.setattr(connectors_api, "google_connector_disabled_reason", lambda *args: "")
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    connectors_api.init_connectors_api(
        registry or _Registry(),
        vault or _Vault(),
        config_path=str(config_path),
    )
    app = FastAPI()
    app.include_router(connectors_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "connectors-admin")
    return client, config_path


def test_list_connectors_reports_credentials_backend_and_counts(tmp_path, monkeypatch):
    client, _ = _client(
        tmp_path,
        monkeypatch,
        config={"connectors": {"fake": {"enabled": True, "backend": "a"}}},
    )

    response = client.get("/api/connectors")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["enabled_count"] == 1
    assert body["configured_count"] == 1
    connector = body["connectors"][0]
    assert connector["id"] == "fake"
    assert connector["backend"] == "a"
    assert connector["available_backends"] == ["a", "b"]
    assert connector["credentials"][0]["present"] is True
    assert connector["operation_count"] == 1


def test_enable_and_disable_connector_update_config_registry_and_vault(tmp_path, monkeypatch):
    registry = _Registry()
    vault = _Vault()
    client, config_path = _client(tmp_path, monkeypatch, registry=registry, vault=vault)

    enabled = client.post("/api/connectors/fake/enable")
    disabled = client.post("/api/connectors/fake/disable")

    assert enabled.json() == {"id": "fake", "enabled": True}
    assert disabled.json() == {"id": "fake", "enabled": False}
    assert vault.grants == [("fake", "fake")]
    assert vault.revoked == ["fake"]
    assert "fake" in registry.unregistered
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["connectors"]["fake"]["enabled"] is False


def test_connector_routes_reject_unknown_connector_ids(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    assert client.post("/api/connectors/missing/enable").status_code == 404
    assert client.post("/api/connectors/missing/disable").status_code == 404


def test_set_backend_validates_options_and_reregisters(tmp_path, monkeypatch):
    registry = _Registry()
    registry.items["fake"] = object()
    client, config_path = _client(
        tmp_path,
        monkeypatch,
        config={"connectors": {"fake": {"enabled": True, "backend": "a"}}},
        registry=registry,
    )

    invalid_connector = client.post("/api/connectors/other/backend", json={"backend": "a"})
    invalid_backend = client.post("/api/connectors/fake/backend", json={"backend": "z"})
    valid = client.post("/api/connectors/fake/backend", json={"backend": "b"})

    assert invalid_connector.status_code == 400
    assert invalid_backend.status_code == 400
    assert valid.json() == {"connector_id": "fake", "backend": "b"}
    assert "fake" in registry.unregistered
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["connectors"]["fake"]["backend"] == "b"


def test_instantiate_connector_returns_none_for_unknown_or_failed_import(monkeypatch):
    monkeypatch.setattr(connectors_api, "_CONNECTOR_CLASSES", {"broken": ("missing.module", "Nope", {})})

    assert connectors_api._instantiate_connector("unknown", {}) is None
    assert connectors_api._instantiate_connector("broken", {}) is None
