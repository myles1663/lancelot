from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api, governance_api
from src.core.operator_identity import OperatorIdentity


def _insert_session(token: str) -> None:
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-19T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": ["warroom.login", "governance.admin"],
        "groups": [],
    }


def _client() -> TestClient:
    auth_api._sessions.clear()
    _insert_session("governance-admin")
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(governance_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "governance-admin")
    return client


def _make_decision(decision_id: str, capability: str, decision: str = "approved"):
    return SimpleNamespace(
        id=decision_id,
        context=SimpleNamespace(capability=capability, target="workspace", risk_tier=2),
        decision=decision,
        reason="because",
        rule_id=None,
        recorded_at="2026-04-19T00:00:00Z",
    )


class _FakeDecisionLog:
    def __init__(self, records=None):
        self.records = records or []
        self.total_decisions = len(self.records)
        self.logged = []

    def get_recent(self, limit):
        return self.records[:limit]

    def get_by_capability(self, capability):
        return [record for record in self.records if record.context.capability == capability]

    def record(self, *, context, decision, reason):
        self.logged.append((context, decision, reason))


class _FakeSentry:
    def __init__(self, pending_requests=None):
        self.pending_requests = pending_requests or {}
        self.approved = []
        self.denied = []
        self.cleaned = False

    def _cleanup_expired(self):
        self.cleaned = True

    def cleanup_expired(self):
        self._cleanup_expired()

    def approve_request(self, approval_id):
        self.approved.append(approval_id)
        return approval_id in self.pending_requests

    def deny_request(self, approval_id):
        self.denied.append(approval_id)
        return approval_id in self.pending_requests


class _FakeTrustLedger:
    def __init__(self, proposals=None):
        self._proposals = proposals or []
        self.applied = []

    def pending_proposals(self):
        return list(self._proposals)

    def apply_graduation(self, approval_id, approved, reason):
        self.applied.append((approval_id, approved, reason))


class _FakeRuleEngine:
    def __init__(self, rules=None):
        self._rules = rules or []
        self.activated = []
        self.declined = []

    def list_rules(self, status=None):
        if status is None:
            return list(self._rules)
        return [rule for rule in self._rules if rule.status == status]

    def activate_rule(self, rule_id):
        self.activated.append(rule_id)

    def decline_rule(self, rule_id, reason=None):
        self.declined.append((rule_id, reason))


def test_governance_stats_returns_empty_when_subsystems_uninitialized():
    governance_api.init_governance_api()

    client = _client()
    response = client.get("/api/governance/stats")

    assert response.status_code == 200
    assert response.json() == {"stats": {"trust": {}, "apl": {}}}


def test_governance_stats_renders_trust_and_apl_panels(monkeypatch):
    trust = object()
    decisions = _FakeDecisionLog()
    rules = _FakeRuleEngine()
    governance_api.init_governance_api(
        trust_ledger=trust,
        rule_engine=rules,
        decision_log=decisions,
    )
    monkeypatch.setattr(
        "governance.war_room_panel.render_trust_panel",
        lambda ledger: {"summary": {"pending_proposals": 1}},
    )
    monkeypatch.setattr(
        "governance.approval_learning.war_room_panel.render_apl_panel",
        lambda rule_engine, decision_log: {"summary": {"proposed_rules": 2}},
    )

    client = _client()
    response = client.get("/api/governance/stats")

    assert response.status_code == 200
    assert response.json()["stats"] == {
        "trust": {"pending_proposals": 1},
        "apl": {"proposed_rules": 2},
    }


def test_governance_decisions_returns_message_when_log_missing():
    governance_api.init_governance_api(decision_log=None)

    client = _client()
    response = client.get("/api/governance/decisions")

    assert response.status_code == 200
    assert response.json()["message"] == "Decision log not initialised"


def test_governance_decisions_filters_by_capability():
    decisions = _FakeDecisionLog(
        [
            _make_decision("d1", "fs.read"),
            _make_decision("d2", "shell.exec", decision="denied"),
        ]
    )
    governance_api.init_governance_api(decision_log=decisions)

    client = _client()
    response = client.get("/api/governance/decisions", params={"capability": "shell.exec"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["decisions"]] == ["d2"]
    assert body["decisions"][0]["decision"] == "denied"


def test_governance_approvals_aggregates_sentry_trust_and_apl():
    sentry = _FakeSentry(
        {
            "sentry-1": {
                "tool": "mcp.lookup",
                "params": {"query": "status"},
                "status": "PENDING",
                "timestamp": "2026-04-19T00:00:00Z",
            }
        }
    )
    trust = _FakeTrustLedger(
        [
            SimpleNamespace(
                id="grad-1",
                capability="connector.slack.post",
                scope="workspace",
                current_tier=3,
                proposed_tier=2,
                consecutive_successes=50,
                status="pending",
                created_at="2026-04-19T00:00:00Z",
            )
        ]
    )
    rules = _FakeRuleEngine(
        [
            SimpleNamespace(
                id="rule-1",
                name="safe pattern",
                description="demo",
                pattern_type="capability",
                status="proposed",
                created_at="2026-04-19T00:00:00Z",
            )
        ]
    )
    governance_api.init_governance_api(trust_ledger=trust, rule_engine=rules, mcp_sentry=sentry)

    client = _client()
    response = client.get("/api/governance/approvals")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {item["type"] for item in body["approvals"]} == {"sentry", "graduation", "apl_rule"}
    assert sentry.cleaned is True


def test_approve_item_direct_handles_sentry_and_emits_receipt(monkeypatch):
    sentry = _FakeSentry(
        {"sentry-1": {"tool": "mcp.lookup", "params": {"query": "status"}, "status": "PENDING"}}
    )
    decisions = _FakeDecisionLog()
    governance_api.init_governance_api(decision_log=decisions, mcp_sentry=sentry)
    emitted = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt_for_identity",
        lambda identity, action_type, action_name, inputs=None, **kwargs: emitted.append(
            (identity.operator_id, action_type.value, action_name, inputs)
        ),
    )

    identity = OperatorIdentity(
        operator_id="op-arthur",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-19T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )

    result = governance_api._approve_item_direct("sentry-1", reason="Looks safe", identity=identity)

    assert result == {"status": "approved", "id": "sentry-1", "type": "sentry"}
    assert sentry.approved == ["sentry-1"]
    assert decisions.logged and decisions.logged[0][1] == "approved"
    assert emitted == [("op-arthur", "mcp_t3_approved", "approve_sentry", {"approval_id": "sentry-1", "tool": "mcp.lookup", "reason": "Looks safe"})]


def test_deny_item_direct_handles_graduation_and_apl(monkeypatch):
    trust = _FakeTrustLedger(
        [
            SimpleNamespace(
                id="grad-1",
                capability="connector.email.send",
                scope="workspace",
                current_tier=3,
                proposed_tier=2,
                consecutive_successes=50,
                status="pending",
                created_at="2026-04-19T00:00:00Z",
            )
        ]
    )
    rules = _FakeRuleEngine(
        [
            SimpleNamespace(
                id="rule-1",
                name="safe pattern",
                description="demo",
                pattern_type="capability",
                status="proposed",
                created_at="2026-04-19T00:00:00Z",
            )
        ]
    )
    governance_api.init_governance_api(trust_ledger=trust, rule_engine=rules)
    emitted = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt_for_identity",
        lambda identity, action_type, action_name, inputs=None, **kwargs: emitted.append(
            (action_type.value, action_name, inputs)
        ),
    )
    identity = OperatorIdentity(
        operator_id="op-arthur",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-19T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )

    grad_result = governance_api._deny_item_direct("grad-1", reason="Need more proof", identity=identity)
    rule_result = governance_api._deny_item_direct("rule-1", reason="Too broad", identity=identity)

    assert grad_result == {"status": "denied", "id": "grad-1", "type": "graduation"}
    assert rule_result == {"status": "denied", "id": "rule-1", "type": "apl_rule"}
    assert trust.applied == [("grad-1", False, "Need more proof")]
    assert rules.declined == [("rule-1", "Too broad")]
    assert emitted == [
        ("t3_rejected", "deny_graduation", {"proposal_id": "grad-1", "capability": "connector.email.send", "reason": "Need more proof"}),
        ("apl_rule_rejected", "deny_apl_rule", {"rule_id": "rule-1", "rule_name": "safe pattern", "reason": "Too broad"}),
    ]


def test_approve_endpoint_approves_sentry_and_emits_receipt(monkeypatch):
    sentry = _FakeSentry(
        {"sentry-1": {"tool": "mcp.lookup", "params": {"query": "status"}, "status": "PENDING"}}
    )
    decisions = _FakeDecisionLog()
    governance_api.init_governance_api(decision_log=decisions, mcp_sentry=sentry)
    emitted = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda request, action_type, action_name, inputs=None, **kwargs: emitted.append(
            (action_type.value, action_name, inputs)
        ),
    )

    client = _client()
    response = client.post("/api/governance/approvals/sentry-1/approve", json={"reason": "Ship it"})

    assert response.status_code == 200
    assert response.json() == {"status": "approved", "id": "sentry-1", "type": "sentry"}
    assert decisions.logged and decisions.logged[0][1] == "approved"
    assert emitted == [("mcp_t3_approved", "approve_sentry", {"approval_id": "sentry-1", "tool": "mcp.lookup", "reason": "Ship it"})]


def test_approve_endpoint_rejects_unexpected_fields(monkeypatch):
    sentry = _FakeSentry(
        {"sentry-1": {"tool": "mcp.lookup", "params": {"query": "status"}, "status": "PENDING"}}
    )
    governance_api.init_governance_api(mcp_sentry=sentry)
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    client = _client()
    response = client.post(
        "/api/governance/approvals/sentry-1/approve",
        json={"reason": "Ship it", "operator_id": "Mallory"},
    )

    assert response.status_code == 422


def test_deny_endpoint_returns_404_for_unknown_item(monkeypatch):
    governance_api.init_governance_api()
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    client = _client()
    response = client.post("/api/governance/approvals/missing/deny", json={"reason": "No"})

    assert response.status_code == 404
    assert response.json()["error"] == "Approval item missing not found"


def test_deny_endpoint_rejects_unexpected_fields(monkeypatch):
    sentry = _FakeSentry(
        {"sentry-1": {"tool": "mcp.lookup", "params": {"query": "status"}, "status": "PENDING"}}
    )
    governance_api.init_governance_api(mcp_sentry=sentry)
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    client = _client()
    response = client.post(
        "/api/governance/approvals/sentry-1/deny",
        json={"reason": "No", "debug": True},
    )

    assert response.status_code == 422
