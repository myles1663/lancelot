import importlib
import os
import sys
import types
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.observability.config import WebhookEndpoint


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
    monkeypatch.setattr(module, "_get_span_export_enabled_status", lambda: (False, "span exploded"))

    response = client.get("/api/observability/status")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_degraded"] is True
    assert "OTel status unavailable" in body["degraded_reasons"]
    assert "Receipt bridge status unavailable" in body["degraded_reasons"]
    assert "OTel span export status unavailable" in body["degraded_reasons"]
    assert any("otel exploded" in err for err in body["runtime_errors"])
    assert any("bridge exploded" in err for err in body["runtime_errors"])
    assert any("span exploded" in err for err in body["runtime_errors"])


def test_otel_status_reports_disabled_export_as_operator_state(monkeypatch):
    client, module = _build_client()

    config = module.ObservabilityConfig()
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "_get_otel_initialized_status", lambda: (False, None))
    monkeypatch.setattr(module, "_get_bridge_enabled_status", lambda: (False, None))
    monkeypatch.setattr(module, "_get_span_export_enabled_status", lambda: (False, None))

    response = client.get("/api/observability/status")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_degraded"] is False
    assert body["otel"]["state"] == "disabled_by_config"
    assert body["otel"]["spans_exported"] is False
    assert body["otel"]["export_destination"] is None


def test_otel_status_flags_enabled_export_without_endpoint(monkeypatch):
    client, module = _build_client()

    config = module.ObservabilityConfig()
    config.otel.enabled = True
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "_get_otel_initialized_status", lambda: (False, None))
    monkeypatch.setattr(module, "_get_bridge_enabled_status", lambda: (False, None))
    monkeypatch.setattr(module, "_get_span_export_enabled_status", lambda: (False, None))

    response = client.get("/api/observability/status")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_degraded"] is True
    assert body["otel"]["state"] == "missing_endpoint"
    assert "OTLP/HTTP endpoint" in body["otel"]["message"]


def test_configure_bridge_keeps_webhook_receipts_active_without_otel(monkeypatch):
    client, module = _build_client()

    config = module.ObservabilityConfig()
    config.webhooks.enabled = True
    config.webhooks.endpoints = [WebhookEndpoint(id="ops", url="https://hooks.example.test")]
    calls = []
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "save_config", lambda updated: None)
    monkeypatch.setattr(module, "_emit_governance_receipt_safe", lambda request, cfg: ([], []))
    monkeypatch.setattr(module, "_get_incident_response_flag", lambda: False)

    def configure_bridge(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "src.observability.receipt_bridge.configure_bridge",
        configure_bridge,
    )
    monkeypatch.setattr(
        "src.observability.otel_provider.is_initialized",
        lambda: False,
    )

    response = client.patch("/api/observability/config/otel", json={"enabled": False})

    assert response.status_code == 200
    assert calls == [{"enabled": True, "otel_enabled": False, "sampling_rate": 0.1}]


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


def test_update_otel_config_applies_full_operator_supplied_fields(monkeypatch):
    client, module = _build_client()

    config = module.ObservabilityConfig()
    saved = []
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "save_config", lambda updated: saved.append(updated))
    monkeypatch.setattr(module, "_emit_governance_receipt_safe", lambda request, cfg: ([], []))
    monkeypatch.setattr(module, "_configure_bridge_safe", lambda cfg: ([], []))

    response = client.patch(
        "/api/observability/config/otel",
        json={
            "auth_header": "Bearer token",
            "export_interval_s": 0,
            "sampling_rate_t0_t1": 2,
            "resource_attributes": {"service.name": "lancelot"},
        },
    )

    assert response.status_code == 200
    assert config.otel.auth_header == "Bearer token"
    assert config.otel.export_interval_s == 1
    assert config.otel.sampling_rate_t0_t1 == 1.0
    assert config.otel.resource_attributes == {"service.name": "lancelot"}
    assert saved == [config]


def test_governance_receipt_helper_records_success_and_failures(monkeypatch):
    _client, module = _build_client()

    emitted = []
    monkeypatch.setitem(
        sys.modules,
        "src.core.governance_receipts",
        types.SimpleNamespace(
            emit_governance_receipt=lambda request, action_type, **kwargs: emitted.append((action_type, kwargs))
        ),
    )
    config = module.ObservabilityConfig()
    config.otel.endpoint = "https://otel.example.test"
    config.otel.enabled = True

    reasons, errors = module._emit_governance_receipt_safe(types.SimpleNamespace(), config)

    assert reasons == []
    assert errors == []
    assert emitted[0][1]["action_name"] == "observability_otel_config_updated"
    assert emitted[0][1]["inputs"] == {"endpoint": "https://otel.example.test", "enabled": True}

    monkeypatch.setitem(
        sys.modules,
        "src.core.governance_receipts",
        types.SimpleNamespace(
            emit_governance_receipt=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("receipt down"))
        ),
    )

    reasons, errors = module._emit_governance_receipt_safe(types.SimpleNamespace(), config)

    assert reasons == ["Governance receipt emission unavailable"]
    assert errors == ["receipt down"]


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


def test_get_config_redacts_otel_auth_header(monkeypatch):
    client, module = _build_client()
    config = module.ObservabilityConfig()
    config.otel.auth_header = "Bearer secret"
    monkeypatch.setattr(module, "load_config", lambda: config)

    response = client.get("/api/observability/config")

    assert response.status_code == 200
    assert response.json()["config"]["otel"]["auth_header"] == "***configured***"


def test_update_webhook_and_metrics_config_clamp_runtime_values(monkeypatch):
    client, module = _build_client()
    config = module.ObservabilityConfig()
    saved = []
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "save_config", lambda updated: saved.append(updated))

    webhooks = client.patch(
        "/api/observability/config/webhooks",
        json={"enabled": True, "delivery_timeout_s": 999, "max_retries": 99},
    )
    metrics = client.patch(
        "/api/observability/config/metrics-api",
        json={"enabled": True, "rate_limit_per_minute": 9999, "receipt_queries": False},
    )

    assert webhooks.status_code == 200
    assert metrics.status_code == 200
    assert config.webhooks.delivery_timeout_s == 30
    assert config.webhooks.max_retries == 10
    assert config.metrics_api.rate_limit_per_minute == 600
    assert config.metrics_api.receipt_queries is False
    assert len(saved) == 2


def test_list_register_and_remove_webhook_endpoints_hot_updates_engine(monkeypatch):
    client, module = _build_client()
    config = module.ObservabilityConfig()
    config.webhooks.endpoints = [
        WebhookEndpoint(
            id="existing",
            url="https://hooks.example.test/old",
            categories=["COST_THRESHOLD"],
            secret_vault_key="vault-key",
            enabled=True,
        )
    ]
    saved = []
    updates = []
    engine = types.SimpleNamespace(update_endpoints=lambda endpoints: updates.append([ep.id for ep in endpoints]))

    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "save_config", lambda updated: saved.append(len(updated.webhooks.endpoints)))
    monkeypatch.setattr(module, "_get_webhook_engine_safe", lambda: (engine, None))
    class FakeUUID:
        def __str__(self):
            return "abcdef123456"

    monkeypatch.setattr(uuid, "uuid4", lambda: FakeUUID())
    monkeypatch.setitem(
        __import__("sys").modules,
        "secret_cache",
        types.SimpleNamespace(is_bootstrapped=lambda: True),
    )

    listed = client.get("/api/observability/webhooks/endpoints")
    assert listed.status_code == 200
    assert listed.json()["endpoints"][0]["has_secret"] is True

    invalid_scheme = client.post(
        "/api/observability/webhooks/endpoints",
        json={"url": "http://hooks.example.test", "categories": []},
    )
    assert invalid_scheme.status_code == 400

    invalid_category = client.post(
        "/api/observability/webhooks/endpoints",
        json={"url": "https://hooks.example.test", "categories": ["not-real"]},
    )
    assert invalid_category.status_code == 400

    registered = client.post(
        "/api/observability/webhooks/endpoints",
        json={
            "url": "https://hooks.example.test/new",
            "categories": ["COST_THRESHOLD"],
            "secret": "shared-secret",
            "enabled": False,
        },
    )
    assert registered.status_code == 200
    endpoint_id = registered.json()["endpoint_id"]
    assert endpoint_id
    vault_key = f"webhook_secret_{endpoint_id}"
    assert os.environ[vault_key] == "shared-secret"
    assert updates[-1] == ["existing", endpoint_id]

    removed = client.delete(f"/api/observability/webhooks/endpoints/{endpoint_id}")
    assert removed.status_code == 200
    assert removed.json()["status"] == "removed"
    assert updates[-1] == ["existing"]
    assert saved[-2:] == [2, 1]

    missing = client.delete("/api/observability/webhooks/endpoints/missing")
    assert missing.status_code == 404
    os.environ.pop(vault_key, None)


def test_webhook_endpoint_hot_update_failures_are_reported(monkeypatch):
    client, module = _build_client()
    config = module.ObservabilityConfig()
    config.webhooks.endpoints = [
        WebhookEndpoint(id="existing", url="https://hooks.example.test/old")
    ]
    engine = types.SimpleNamespace(
        update_endpoints=lambda endpoints: (_ for _ in ()).throw(RuntimeError("engine down"))
    )
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "save_config", lambda updated: None)
    monkeypatch.setattr(module, "_get_webhook_engine_safe", lambda: (engine, None))

    registered = client.post(
        "/api/observability/webhooks/endpoints",
        json={"url": "https://hooks.example.test/new", "categories": []},
    )
    removed = client.delete("/api/observability/webhooks/endpoints/existing")

    assert registered.status_code == 200
    assert "Webhook engine hot-update failed" in registered.json()["degraded_reasons"]
    assert removed.status_code == 200
    assert "Webhook engine hot-update failed" in removed.json()["degraded_reasons"]

    monkeypatch.setattr(module, "_get_webhook_engine_safe", lambda: (None, "engine import failed"))
    registered = client.post(
        "/api/observability/webhooks/endpoints",
        json={"url": "https://hooks.example.test/another", "categories": []},
    )
    assert "Webhook engine status unavailable" in registered.json()["degraded_reasons"]

    removed = client.delete(f"/api/observability/webhooks/endpoints/{registered.json()['endpoint_id']}")
    assert removed.status_code == 200
    assert "Webhook engine status unavailable" in removed.json()["degraded_reasons"]


def test_webhook_secret_staging_failure_keeps_endpoint_registration_safe(monkeypatch):
    client, module = _build_client()
    config = module.ObservabilityConfig()
    monkeypatch.setattr(module, "load_config", lambda: config)
    monkeypatch.setattr(module, "save_config", lambda updated: None)
    monkeypatch.setattr(module, "_get_webhook_engine_safe", lambda: (None, None))
    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        types.SimpleNamespace(is_bootstrapped=lambda: (_ for _ in ()).throw(RuntimeError("vault down"))),
    )

    response = client.post(
        "/api/observability/webhooks/endpoints",
        json={"url": "https://hooks.example.test/secret", "categories": [], "secret": "shared-secret"},
    )

    assert response.status_code == 200
    endpoint_id = response.json()["endpoint_id"]
    assert config.webhooks.endpoints[-1].secret_vault_key == f"webhook_secret_{endpoint_id}"
    assert os.environ.get(f"webhook_secret_{endpoint_id}") is None


def test_webhook_stats_success_and_runtime_exception(monkeypatch):
    client, module = _build_client()
    engine = types.SimpleNamespace(get_stats=lambda: {"endpoint-1": {"delivered": 3}})
    monkeypatch.setattr(module, "_get_webhook_engine_safe", lambda: (engine, None))

    response = client.get("/api/observability/webhooks/stats")
    assert response.status_code == 200
    assert response.json()["stats"]["endpoint-1"]["delivered"] == 3

    engine.get_stats = lambda: (_ for _ in ()).throw(RuntimeError("stats down"))
    response = client.get("/api/observability/webhooks/stats")
    assert response.status_code == 200
    assert "Webhook delivery stats unavailable" in response.json()["degraded_reasons"]


def test_status_helpers_report_import_errors(monkeypatch):
    _client, module = _build_client()

    monkeypatch.setitem(
        __import__("sys").modules,
        "src.observability.otel_provider",
        types.SimpleNamespace(is_initialized=lambda: (_ for _ in ()).throw(RuntimeError("otel down"))),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.observability.receipt_bridge",
        types.SimpleNamespace(
            _enabled=True,
            _otel_enabled=True,
            configure_bridge=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bridge down")),
        ),
    )

    initialized, init_error = module._get_otel_initialized_status()
    bridge, bridge_error = module._get_bridge_enabled_status()
    span, span_error = module._get_span_export_enabled_status()
    reasons, errors = module._configure_bridge_safe(module.ObservabilityConfig())

    assert initialized is False
    assert "otel down" in init_error
    assert bridge is True
    assert bridge_error is None
    assert span is True
    assert span_error is None
    assert "Receipt bridge live apply unavailable" in reasons
    assert "bridge down" in errors[0]
