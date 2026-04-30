from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api, trust_api
from src.core.operator_identity import OperatorIdentity


class _FakeTrustLedger:
    def __init__(self):
        self.applied = []

    def list_records(self):
        return []

    def pending_proposals(self):
        return [
            SimpleNamespace(
                id="proposal-1",
                capability="connector.email.send",
                scope="workspace",
                current_tier=3,
                proposed_tier=2,
                consecutive_successes=50,
                status="pending",
                created_at="2026-04-20T00:00:00Z",
            )
        ]

    def apply_graduation(self, proposal_id, approved, reason):
        self.applied.append((proposal_id, approved, reason))


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
        "capabilities": ["warroom.login", "trust.admin"],
        "groups": [],
    }


def _client() -> TestClient:
    auth_api._sessions.clear()
    _insert_session("trust-admin")
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(trust_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "trust-admin")
    return client


def test_trust_approve_rejects_unexpected_fields(monkeypatch):
    trust_api.init_trust_api(_FakeTrustLedger())
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    client = _client()
    response = client.post(
        "/api/trust/proposals/proposal-1/approve",
        json={"reason": "Looks good", "operator_id": "Mallory"},
    )

    assert response.status_code == 422


def test_trust_decline_rejects_unexpected_fields(monkeypatch):
    trust_api.init_trust_api(_FakeTrustLedger())
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )

    client = _client()
    response = client.post(
        "/api/trust/proposals/proposal-1/decline",
        json={"reason": "Need more proof", "debug": True},
    )

    assert response.status_code == 422
