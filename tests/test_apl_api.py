from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, apl_api, auth_api
from src.core.operator_identity import OperatorIdentity


class _FakeRuleEngine:
    def __init__(self):
        self.revoked = []
        self.declined = []

    def list_rules(self, status=None):
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
        return []

    def revoke_rule(self, rule_id, reason=None):
        self.revoked.append((rule_id, reason))

    def decline_rule(self, rule_id, reason=None):
        self.declined.append((rule_id, reason))

    def activate_rule(self, rule_id):
        return None

    def pause_rule(self, rule_id):
        return None

    def resume_rule(self, rule_id):
        return None


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
