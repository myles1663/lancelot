import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.observability import metrics_api


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


def _authenticate_client(client, token):
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), token)
    return client


class _Receipt:
    def __init__(self, receipt_id="r-1"):
        self.receipt_id = receipt_id

    def to_dict(self):
        return {"id": self.receipt_id, "action_type": "system"}


class _Conn:
    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return []


class _ReceiptService:
    def __init__(self):
        self.created = []

    def _get_connection(self):
        return _Conn()

    def get(self, receipt_id):
        return _Receipt(receipt_id)

    def create(self, receipt):
        self.created.append(receipt)


def _client(monkeypatch, enabled=True, rate_limit=60, receipt_queries=False):
    api_auth.init_api_auth(lambda request: True)
    service = _ReceiptService()
    metrics_api._receipt_service = service
    metrics_api._rate_buckets.clear()
    monkeypatch.setattr(
        metrics_api,
        "load_config",
        lambda: type(
            "Cfg",
            (),
            {
                "metrics_api": type(
                    "MetricsCfg",
                    (),
                    {
                        "enabled": enabled,
                        "rate_limit_per_minute": rate_limit,
                        "receipt_queries": receipt_queries,
                    },
                )()
            },
        )(),
    )
    app = FastAPI()
    app.include_router(metrics_api.router)
    return TestClient(app), service


def test_metrics_api_returns_503_when_disabled(monkeypatch):
    client, _service = _client(monkeypatch, enabled=False)
    token = "metrics-admin-disabled"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "observability.admin"})
    _authenticate_client(client, token)

    response = client.get("/api/metrics/summary")

    assert response.status_code == 503
    assert response.json()["detail"] == "Metrics API disabled"


def test_metrics_api_rate_limits_per_operator(monkeypatch):
    client, _service = _client(monkeypatch, enabled=True, rate_limit=1)
    token = "metrics-admin-rate"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "observability.admin"})
    _authenticate_client(client, token)

    first = client.get("/api/metrics/summary")
    second = client.get("/api/metrics/summary")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == "Metrics API rate limit exceeded"


def test_metrics_receipt_detail_emits_query_receipt_when_enabled(monkeypatch):
    client, service = _client(monkeypatch, enabled=True, receipt_queries=True)
    token = "metrics-admin-receipts"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "observability.admin"})
    _authenticate_client(client, token)

    response = client.get("/api/metrics/receipts/receipt-123")

    assert response.status_code == 200
    assert response.json()["data"]["receipt"]["id"] == "receipt-123"
    assert len(service.created) == 1
    assert service.created[0].action_type == "metrics_api_query"
    assert service.created[0].operator_id == "op-123"


def test_metrics_summary_uses_live_subsystem_module_paths(monkeypatch):
    client, _service = _client(monkeypatch, enabled=True)
    token = "metrics-admin-summary"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "observability.admin"})
    _authenticate_client(client, token)

    fake_flags = types.SimpleNamespace(get_all_flags=lambda: {"FEATURE_ALPHA": False, "FEATURE_BETA": True})
    fake_governance = types.SimpleNamespace(_get_pending_approvals_count=lambda: 7)
    fake_runtime = types.SimpleNamespace(
        get_runtime=lambda: types.SimpleNamespace(active_agents=lambda: ["a1", "a2", "a3"])
    )

    class _FakeSoul:
        def dict(self):
            return {"version": "v1", "mission": "Test mission"}

    monkeypatch.setitem(sys.modules, "src.core.feature_flags", fake_flags)
    monkeypatch.setitem(sys.modules, "src.core.governance_api", fake_governance)
    monkeypatch.setitem(sys.modules, "src.hive.runtime", fake_runtime)
    monkeypatch.setitem(sys.modules, "src.core.soul.store", types.SimpleNamespace(load_active_soul=lambda: _FakeSoul()))

    response = client.get("/api/metrics/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["active_kill_switches"] == 1
    assert data["pending_t3_approvals"] == 7
    assert data["active_hive_agents"] == 3
    assert data["soul_version"] != "unknown"


def test_metrics_trust_ledger_uses_namespaced_module_path(monkeypatch):
    client, _service = _client(monkeypatch, enabled=True)
    token = "metrics-admin-trust"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "observability.admin"})
    _authenticate_client(client, token)

    class _Entry:
        def to_dict(self):
            return {"capability": "deploy", "tier": 3}

    class _Ledger:
        def get_all_entries(self):
            return [_Entry()]

    monkeypatch.setitem(sys.modules, "src.core.trust_api", types.SimpleNamespace(_trust_ledger=_Ledger()))

    response = client.get("/api/metrics/trust-ledger")

    assert response.status_code == 200
    entries = response.json()["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["capability"] == "deploy"
