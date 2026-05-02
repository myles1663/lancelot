from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api, trust_api
from src.core.operator_identity import OperatorIdentity


class _FakeTrustLedger:
    def __init__(self):
        self.applied = []
        self.fail = None

    def list_records(self):
        if self.fail == "records":
            raise RuntimeError("records failed")
        return [
            SimpleNamespace(
                capability="connector.email.send",
                scope="workspace",
                current_tier=2,
                default_tier=3,
                is_graduated=True,
                consecutive_successes=51,
                total_successes=100,
                total_failures=2,
                total_rollbacks=1,
                success_rate=0.98,
                can_graduate=False,
                last_success="2026-04-20T00:00:00Z",
                last_failure=None,
                graduation_history=[
                    SimpleNamespace(
                        timestamp="2026-04-20T00:00:00Z",
                        from_tier=3,
                        to_tier=2,
                        trigger="threshold",
                        owner_approved=True,
                    )
                ],
            )
        ]

    def pending_proposals(self):
        if self.fail == "proposals":
            raise RuntimeError("proposals failed")
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
        if self.fail == "apply":
            raise RuntimeError("apply failed")
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


def test_trust_read_endpoints_serialize_records_proposals_and_timeline():
    trust_api.init_trust_api(_FakeTrustLedger())
    client = _client()

    records = client.get("/api/trust/records")
    proposals = client.get("/api/trust/proposals")
    timeline = client.get("/api/trust/timeline")

    assert records.status_code == 200
    assert records.json()["records"][0]["capability"] == "connector.email.send"
    assert records.json()["records"][0]["current_tier"] == 2
    assert proposals.json()["proposals"][0]["id"] == "proposal-1"
    assert timeline.json()["events"][0]["from_tier"] == 3


def test_trust_read_endpoints_handle_uninitialized_and_errors():
    client = _client()
    trust_api.init_trust_api(None)

    assert client.get("/api/trust/records").json()["records"] == []
    assert client.get("/api/trust/proposals").json()["proposals"] == []
    assert client.get("/api/trust/timeline").json()["events"] == []

    ledger = _FakeTrustLedger()
    trust_api.init_trust_api(ledger)

    ledger.fail = "records"
    assert client.get("/api/trust/records").json()["error"] == "Failed to get trust records"
    assert client.get("/api/trust/timeline").json()["error"] == "Failed to get trust timeline"

    ledger.fail = "proposals"
    assert client.get("/api/trust/proposals").json()["status"] == 500


def test_trust_mutation_endpoints_apply_graduation_and_emit_receipts(monkeypatch):
    ledger = _FakeTrustLedger()
    trust_api.init_trust_api(ledger)
    emitted = []
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )
    client = _client()

    approve = client.post("/api/trust/proposals/proposal-1/approve", json={"reason": "enough proof"})
    decline = client.post("/api/trust/proposals/proposal-1/decline", json={"reason": "not enough proof"})

    assert approve.json() == {"status": "approved", "proposal_id": "proposal-1"}
    assert decline.json() == {"status": "declined", "proposal_id": "proposal-1"}
    assert ledger.applied == [
        ("proposal-1", True, "enough proof"),
        ("proposal-1", False, "not enough proof"),
    ]
    assert len(emitted) == 2


def test_trust_mutation_endpoints_handle_uninitialized_and_apply_failures(monkeypatch):
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: None,
    )
    client = _client()
    trust_api.init_trust_api(None)

    assert client.post("/api/trust/proposals/proposal-1/approve").json()["status"] == 400
    assert client.post("/api/trust/proposals/proposal-1/decline").json()["status"] == 400

    ledger = _FakeTrustLedger()
    ledger.fail = "apply"
    trust_api.init_trust_api(ledger)

    assert client.post("/api/trust/proposals/proposal-1/approve").json()["status"] == 500
    assert client.post("/api/trust/proposals/proposal-1/decline").json()["error"] == "Failed to decline proposal"
