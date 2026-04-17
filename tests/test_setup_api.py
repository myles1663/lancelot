import importlib
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.auth_api as auth_api
import src.core.setup_api as setup_api_module
from src.core.operator_identity import OperatorIdentity


def _build_app(tmp_data_dir, verify_request):
    importlib.reload(setup_api_module)
    app = FastAPI()
    setup_api_module.init_setup_api(
        data_dir=str(tmp_data_dir),
        startup_time=0.0,
        audit_logger=MagicMock(),
        connector_vault=None,
        receipt_service=None,
        verify_request=verify_request,
    )
    app.include_router(setup_api_module.router)
    return app


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


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/api/setup/system-info", None),
        ("post", "/api/setup/restart", {"confirm": True}),
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


def test_setup_api_requires_admin_capability(tmp_data_dir, monkeypatch):
    app = _build_app(tmp_data_dir, verify_request=lambda request: True)
    client = TestClient(app)
    token = "limited-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})

    response = client.get(
        "/api/setup/system-info",
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: setup.admin"
