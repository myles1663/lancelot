import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity


def _insert_session(token: str, capabilities: set[str]) -> None:
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-10T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": sorted(capabilities),
        "groups": [],
    }


def _build_client():
    module = importlib.import_module("src.observability.api")
    module = importlib.reload(module)
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)
    auth_api._sessions.clear()
    _insert_session("observability-admin", {"warroom.login", "observability.admin"})
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "observability-admin")
    return client, module


def test_otel_status_surfaces_runtime_degradation(monkeypatch):
    client, module = _build_client()

    class _Config:
        otel = type(
            "OTel",
            (),
            {
                "endpoint": "https://otel.example.test",
                "enabled": True,
                "sampling_rate_t0_t1": 0.1,
                "export_interval_s": 5,
            },
        )()
        webhooks = type("Webhooks", (), {"enabled": False, "endpoints": []})()
        metrics_api = type("MetricsApi", (), {"enabled": False, "rate_limit_per_minute": 60})()

    monkeypatch.setattr(module, "load_config", lambda: _Config())
    monkeypatch.setattr(module, "_get_otel_initialized_status", lambda: (False, "otel exploded"))
    monkeypatch.setattr(module, "_get_bridge_enabled_status", lambda: (False, "bridge exploded"))

    response = client.get("/api/observability/status")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_degraded"] is True
    assert "OTel status unavailable" in body["degraded_reasons"]
    assert "Receipt bridge status unavailable" in body["degraded_reasons"]
    assert any("otel exploded" in err for err in body["runtime_errors"])
    assert any("bridge exploded" in err for err in body["runtime_errors"])


def test_update_otel_config_surfaces_receipt_and_bridge_failures(monkeypatch):
    client, module = _build_client()

    config = module.ObservabilityConfig()
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "save_config", lambda updated: None)
    monkeypatch.setattr(
        module,
        "_emit_governance_receipt_safe",
        lambda request, cfg: (["Governance receipt emission unavailable"], ["receipt exploded"]),
    )
    monkeypatch.setattr(
        module,
        "_configure_bridge_safe",
        lambda cfg: (["Receipt bridge live apply unavailable"], ["bridge exploded"]),
    )

    response = client.patch(
        "/api/observability/config/otel",
        json={"enabled": True, "endpoint": "https://otel.example.test"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_degraded"] is True
    assert "Governance receipt emission unavailable" in body["degraded_reasons"]
    assert "Receipt bridge live apply unavailable" in body["degraded_reasons"]
    assert any("receipt exploded" in err for err in body["runtime_errors"])
    assert any("bridge exploded" in err for err in body["runtime_errors"])


def test_webhook_stats_surfaces_engine_failure(monkeypatch):
    client, module = _build_client()

    monkeypatch.setattr(module, "_get_webhook_engine_safe", lambda: (None, "engine exploded"))

    response = client.get("/api/observability/webhooks/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["stats"] == {}
    assert body["runtime_degraded"] is True
    assert "Webhook engine status unavailable" in body["degraded_reasons"]
    assert any("engine exploded" in err for err in body["runtime_errors"])


def test_otel_update_rejects_unexpected_fields():
    client, _module = _build_client()

    response = client.patch(
        "/api/observability/config/otel",
        json={
            "enabled": True,
            "endpoint": "https://otel.example.test",
            "unexpected": "deny-me",
        },
    )

    assert response.status_code == 422
