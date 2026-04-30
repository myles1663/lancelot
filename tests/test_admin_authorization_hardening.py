import os
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import actioncard_api
from src.core import api_auth
from src.core import auth_api
from src.core import scheduler_api
from src.mcp import api as mcp_api
from src.a2a import api as a2a_api
from src.timetravel import api as timetravel_api
from src.core.providers import api as providers_api
from src.core.soul import api as soul_api
from src.core import update_api
from src.incidents import playbook_api
from src.observability import metrics_api
from src.core.operator_identity import OperatorIdentity


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


def _provider_client():
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(providers_api.router)
    return TestClient(app)


def _soul_client(tmp_path):
    soul_dir = tmp_path / "soul"
    versions_dir = soul_dir / "soul_versions"
    versions_dir.mkdir(parents=True)
    (versions_dir / "soul_v1.yaml").write_text(
        "\n".join(
            [
                "version: v1",
                "mission: Serve faithfully.",
                "allegiance: Owner",
                "autonomy_posture:",
                "  level: supervised",
                "  description: Supervised autonomy.",
                "  allowed_autonomous: [classify_intent]",
                "  requires_approval: [deploy]",
                "risk_rules:",
                "  - name: destructive_actions_require_approval",
                "    description: Destructive actions need approval",
                "    enforced: true",
                "approval_rules:",
                "  default_timeout_seconds: 3600",
                "  escalation_on_timeout: skip_and_log",
                "  channels: [war_room]",
                "tone_invariants: ['Never mislead the owner']",
                "memory_ethics: ['Do not store PII without consent']",
                "scheduling_boundaries:",
                "  max_concurrent_jobs: 5",
                "  max_job_duration_seconds: 300",
                "  no_autonomous_irreversible: true",
                "  require_ready_state: true",
                "  description: Safe scheduling.",
            ]
        ),
        encoding="utf-8",
    )
    (soul_dir / "ACTIVE").write_text("v1", encoding="utf-8")

    api_auth.init_api_auth(lambda request: True)
    soul_api._set_soul_dir(str(soul_dir))
    app = FastAPI()
    app.include_router(soul_api.router)
    return TestClient(app)


def _actioncard_client():
    api_auth.init_api_auth(lambda request: True)

    class _Resolver:
        def __init__(self):
            self.calls = []
            self.archives = []

        def resolve(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"status": "approved", "message": "ok"}

        def archive(self, *args, **kwargs):
            self.archives.append((args, kwargs))
            return {"status": "archived", "message": "archived"}

    resolver = _Resolver()
    actioncard_api.init_actioncard_api(card_store=object(), card_resolver=resolver)

    app = FastAPI()
    app.include_router(actioncard_api.router)
    return TestClient(app), resolver


def _scheduler_client(executor=None):
    api_auth.init_api_auth(lambda request: True)

    class _Service:
        def list_jobs(self):
            return []

        def get_job(self, job_id):
            return type("Job", (), {"id": job_id})()

    scheduler_api.init_scheduler_api(service=_Service(), executor=executor)

    app = FastAPI()
    app.include_router(scheduler_api.router)
    return TestClient(app)


def _simple_router_client(router):
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _metrics_client():
    api_auth.init_api_auth(lambda request: True)

    class _Receipt:
        def to_dict(self):
            return {"id": "r-1"}

    class _ReceiptService:
        def get(self, receipt_id):
            return _Receipt()

    metrics_api._receipt_service = _ReceiptService()
    app = FastAPI()
    app.include_router(metrics_api.router)
    return TestClient(app)


def _update_client():
    api_auth.init_api_auth(lambda request: True)

    class _Checker:
        def force_check(self):
            return {"checked": True}

        def dismiss(self):
            return True

    update_api.init_update_api(_Checker())
    app = FastAPI()
    app.include_router(update_api.router)
    return TestClient(app)


def test_provider_admin_routes_require_admin_capability():
    client = _provider_client()
    token = "limited-provider-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.post(
        "/api/v1/providers/switch",
        json={"provider": "openai"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: provider.admin"


def test_soul_mutation_routes_require_soul_admin_capability(tmp_path):
    client = _soul_client(tmp_path)
    token = "limited-soul-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    with patch.dict(os.environ, {"LANCELOT_API_TOKEN": "owner-token"}):
        response = client.post(
            "/soul/propose",
            json={"proposed_yaml": "version: v2"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Owner identity required"


def test_actioncard_resolution_requires_platform_admin_and_uses_authenticated_identity():
    client, resolver = _actioncard_client()
    token = "limited-actioncard-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.post(
        "/api/actioncards/card-1/resolve/approve",
        json={},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"
    assert resolver.calls == []

    admin_token = "admin-actioncard-session"
    auth_api._sessions.clear()
    _insert_session(admin_token, {"warroom.login", "platform.admin"})
    _authenticate_client(client, admin_token)

    response = client.post(
        "/api/actioncards/card-2/resolve/approve",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert len(resolver.calls) == 1
    args, kwargs = resolver.calls[0]
    assert args == ("card-2", "approve")
    assert kwargs["channel"] == "warroom"
    assert kwargs["operator_id"] == "op-123"
    assert kwargs["session_id"] == "session-1"
    assert kwargs["actor"] == "Arthur"


def test_actioncard_archive_requires_platform_admin_and_uses_authenticated_identity():
    client, resolver = _actioncard_client()
    token = "limited-actioncard-archive-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.post(
        "/api/actioncards/card-1/archive",
        json={"reason": "stale card"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"
    assert resolver.archives == []

    admin_token = "admin-actioncard-archive-session"
    auth_api._sessions.clear()
    _insert_session(admin_token, {"warroom.login", "platform.admin"})
    _authenticate_client(client, admin_token)

    response = client.post(
        "/api/actioncards/card-2/archive",
        json={"reason": "stale card"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert len(resolver.archives) == 1
    args, kwargs = resolver.archives[0]
    assert args == ("card-2",)
    assert kwargs["channel"] == "warroom_archive"
    assert kwargs["operator_id"] == "op-123"
    assert kwargs["session_id"] == "session-1"
    assert kwargs["actor"] == "Arthur"
    assert kwargs["reason"] == "stale card"


def test_scheduler_routes_require_scheduler_admin_capability():
    client = _scheduler_client()
    token = "limited-scheduler-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.get("/api/scheduler/jobs")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: scheduler.admin"


def test_scheduler_approval_uses_authenticated_identity():
    class _Executor:
        def __init__(self):
            self.calls = []

        def approve_job(self, job_id, **kwargs):
            self.calls.append((job_id, kwargs))
            return True

    executor = _Executor()
    client = _scheduler_client(executor=executor)

    token = "admin-scheduler-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "scheduler.admin"})
    _authenticate_client(client, token)

    response = client.post("/api/scheduler/jobs/job-123/approve")

    assert response.status_code == 200
    assert response.json()["approved"] is True
    assert len(executor.calls) == 1
    job_id, kwargs = executor.calls[0]
    assert job_id == "job-123"
    assert kwargs["operator_id"] == "op-123"
    assert kwargs["session_id"] == "session-1"
    assert kwargs["actor"] == "Arthur"


def test_timetravel_routes_require_timetravel_admin_capability():
    client = _simple_router_client(timetravel_api.router)
    token = "limited-timetravel-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.get("/api/timetravel/status")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: timetravel.admin"


def test_a2a_routes_require_a2a_admin_capability():
    client = _simple_router_client(a2a_api.router)
    token = "limited-a2a-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.get("/api/a2a/status")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: a2a.admin"


def test_mcp_routes_require_mcp_admin_capability():
    client = _simple_router_client(mcp_api.router)
    token = "limited-mcp-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.get("/api/mcp/servers")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: mcp.admin"


def test_metrics_routes_require_observability_admin_capability(monkeypatch):
    client = _metrics_client()
    token = "limited-metrics-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)
    monkeypatch.setattr(
        metrics_api,
        "load_config",
        lambda: type(
            "Cfg",
            (),
            {"metrics_api": type("MetricsCfg", (), {"enabled": True, "rate_limit_per_minute": 60, "receipt_queries": False})()},
        )(),
    )

    response = client.get("/api/metrics/summary")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: observability.admin"


def test_playbook_reload_requires_incidents_admin_capability():
    client = _simple_router_client(playbook_api.router)
    token = "limited-playbook-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    response = client.post("/api/playbooks/reload")

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: incidents.admin"


def test_playbook_reload_emits_receipt_for_admin(monkeypatch):
    client = _simple_router_client(playbook_api.router)
    token = "admin-playbook-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login", "incidents.admin"})
    _authenticate_client(client, token)

    monkeypatch.setattr(playbook_api, "_playbooks_dir", "playbooks")
    monkeypatch.setattr(playbook_api, "invalidate_cache", lambda: None)
    monkeypatch.setattr(playbook_api, "load_playbooks", lambda _dir: {"a": object(), "b": object()})

    calls = []

    def _emit(request, action_type, action_name, inputs=None, outputs=None, metadata=None, quest_id=None):
        calls.append(
            {
                "action_type": action_type.value if hasattr(action_type, "value") else str(action_type),
                "action_name": action_name,
                "inputs": inputs,
                "outputs": outputs,
                "metadata": metadata,
            }
        )

    monkeypatch.setattr("src.core.governance_receipts.emit_governance_receipt", _emit)

    response = client.post("/api/playbooks/reload")

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert len(calls) == 1
    assert calls[0]["action_type"] == "playbook_updated"
    assert calls[0]["action_name"] == "reload_playbooks"
    assert calls[0]["outputs"]["playbook_count"] == 2


def test_update_mutation_routes_require_platform_admin_capability():
    client = _update_client()
    token = "limited-update-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})
    _authenticate_client(client, token)

    check_response = client.post("/api/updates/check")
    dismiss_response = client.post("/api/updates/dismiss")

    assert check_response.status_code == 403
    assert check_response.json()["detail"] == "Missing capability: platform.admin"
    assert dismiss_response.status_code == 403
    assert dismiss_response.json()["detail"] == "Missing capability: platform.admin"
