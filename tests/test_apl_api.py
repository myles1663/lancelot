from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, apl_api, auth_api
from src.core.operator_identity import OperatorIdentity


class _FakeRuleEngine:
    def __init__(self):
        self.revoked = []
        self.declined = []
        self.activated = []
        self.paused = []
        self.resumed = []
        self.fail = None

    def list_rules(self, status=None):
        if self.fail == "list":
            raise RuntimeError("list failed")
        return [
            SimpleNamespace(
                id="rule-1",
                name="safe pattern",
                description="demo",
                pattern_type="capability",
                status="proposed",
                created_at="2026-04-20T00:00:00Z",
                conditions={},
                auto_decisions_today=0,
                auto_decisions_total=0,
                max_auto_decisions_per_day=10,
                max_auto_decisions_total=100,
                activated_at=None,
            )
        ]

    def check_circuit_breakers(self):
        if self.fail == "circuit":
            raise RuntimeError("circuit failed")
        return self.list_rules()

    def revoke_rule(self, rule_id, reason=None):
        if self.fail == "revoke":
            raise RuntimeError("revoke failed")
        self.revoked.append((rule_id, reason))

    def decline_rule(self, rule_id, reason=None):
        if self.fail == "decline":
            raise RuntimeError("decline failed")
        self.declined.append((rule_id, reason))

    def activate_rule(self, rule_id):
        if self.fail == "activate":
            raise RuntimeError("activate failed")
        self.activated.append(rule_id)
        return None

    def pause_rule(self, rule_id):
        if self.fail == "pause":
            raise RuntimeError("pause failed")
        self.paused.append(rule_id)
        return None

    def resume_rule(self, rule_id):
        if self.fail == "resume":
            raise RuntimeError("resume failed")
        self.resumed.append(rule_id)
        return None


class _FakeDecisionLog:
    total_decisions = 1
    auto_approved_count = 1

    def __init__(self):
        self.fail = False

    def get_recent(self, limit):
        if self.fail:
            raise RuntimeError("decision failed")
        return [
            SimpleNamespace(
                id="decision-1",
                context=SimpleNamespace(
                    capability="connector.write",
                    target="ticket",
                    risk_tier=2,
                ),
                decision="approved",
                rule_id="rule-1",
                reason="known safe pattern",
                recorded_at="2026-04-20T00:00:00Z",
            )
        ][:limit]


def _insert_session(token: str) -> None:
    auth_api._sessions[token] = {
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
        "capabilities": ["warroom.login", "apl.admin"],
        "groups": [],
    }


def _client() -> TestClient:
    auth_api._sessions.clear()
    _insert_session("apl-admin")
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(apl_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "apl-admin")
    return client


def _init(rule_engine=None, decision_log=None):
    rule_engine = rule_engine if rule_engine is not None else _FakeRuleEngine()
    decision_log = decision_log if decision_log is not None else _FakeDecisionLog()
    apl_api.init_apl_api(rule_engine=rule_engine, decision_log=decision_log)
    return rule_engine, decision_log


def test_apl_read_endpoints_serialize_rules_and_decisions():
    _init()
    client = _client()

    rules = client.get("/api/apl/rules?status=proposed")
    proposals = client.get("/api/apl/proposals")
    decisions = client.get("/api/apl/decisions?limit=1")
    circuit_breakers = client.get("/api/apl/circuit-breakers")

    assert rules.status_code == 200
    assert rules.json()["rules"][0]["id"] == "rule-1"
    assert proposals.json()["proposals"][0]["conditions"] == {}
    assert decisions.json()["decisions"][0]["capability"] == "connector.write"
    assert decisions.json()["auto_approved"] == 1
    assert circuit_breakers.json()["circuit_breakers"][0]["daily_usage"] == 0


def test_apl_read_endpoints_return_not_initialized_messages():
    apl_api.init_apl_api(rule_engine=None, decision_log=None)
    client = _client()

    assert client.get("/api/apl/rules").json()["rules"] == []
    assert client.get("/api/apl/proposals").json()["proposals"] == []
    assert client.get("/api/apl/decisions").json()["decisions"] == []
    assert client.get("/api/apl/circuit-breakers").json()["circuit_breakers"] == []


def test_apl_read_endpoints_return_safe_errors():
    rule_engine, decision_log = _init()
    client = _client()

    rule_engine.fail = "list"
    assert client.get("/api/apl/rules").status_code == 500
    assert client.get("/api/apl/proposals").json()["error"] == "Failed to get APL proposals"

    rule_engine.fail = "circuit"
    assert client.get("/api/apl/circuit-breakers").json()["status"] == 500

    rule_engine.fail = None
    decision_log.fail = True
    assert client.get("/api/apl/decisions").json()["error"] == "Failed to get APL decisions"


def test_apl_mutation_endpoints_update_rule_engine(monkeypatch):
    rule_engine, _ = _init()
    emitted = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )
    client = _client()

    assert client.post("/api/apl/rules/rule-1/pause").json() == {
        "status": "paused",
        "rule_id": "rule-1",
    }
    assert client.post("/api/apl/rules/rule-1/resume").json()["status"] == "active"
    assert client.post("/api/apl/rules/rule-1/revoke", json={"reason": "too broad"}).json()["status"] == "revoked"
    assert client.post("/api/apl/proposals/rule-1/activate").json()["status"] == "active"
    assert client.post("/api/apl/proposals/rule-1/decline", json={"reason": "not now"}).json()["status"] == "declined"

    assert rule_engine.paused == ["rule-1"]
    assert rule_engine.resumed == ["rule-1"]
    assert rule_engine.revoked == [("rule-1", "too broad")]
    assert rule_engine.activated == ["rule-1"]
    assert rule_engine.declined == [("rule-1", "not now")]
    assert len(emitted) == 3


def test_apl_mutation_endpoints_handle_uninitialized_and_failures(monkeypatch):
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )
    client = _client()

    apl_api.init_apl_api(rule_engine=None, decision_log=_FakeDecisionLog())
    assert client.post("/api/apl/rules/rule-1/pause").json()["error"] == "Rule engine not initialised"
    assert client.post("/api/apl/rules/rule-1/resume").json()["status"] == 400
    assert client.post("/api/apl/rules/rule-1/revoke").json()["status"] == 400
    assert client.post("/api/apl/proposals/rule-1/activate").json()["status"] == 400
    assert client.post("/api/apl/proposals/rule-1/decline").json()["status"] == 400

    rule_engine, _ = _init()
    for action, path in (
        ("pause", "/api/apl/rules/rule-1/pause"),
        ("resume", "/api/apl/rules/rule-1/resume"),
        ("revoke", "/api/apl/rules/rule-1/revoke"),
        ("activate", "/api/apl/proposals/rule-1/activate"),
        ("decline", "/api/apl/proposals/rule-1/decline"),
    ):
        rule_engine.fail = action
        assert client.post(path).status_code == 500


def test_apl_revoke_rejects_unexpected_fields(monkeypatch):
    apl_api.init_apl_api(rule_engine=_FakeRuleEngine(), decision_log=SimpleNamespace(get_recent=lambda limit: [], total_decisions=0, auto_approved_count=0))
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    client = _client()
    response = client.post(
        "/api/apl/rules/rule-1/revoke",
        json={"reason": "Too broad", "operator_id": "Mallory"},
    )

    assert response.status_code == 422


def test_apl_decline_rejects_unexpected_fields(monkeypatch):
    apl_api.init_apl_api(rule_engine=_FakeRuleEngine(), decision_log=SimpleNamespace(get_recent=lambda limit: [], total_decisions=0, auto_approved_count=0))
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    client = _client()
    response = client.post(
        "/api/apl/proposals/rule-1/decline",
        json={"reason": "Too broad", "debug": True},
    )

    assert response.status_code == 422
