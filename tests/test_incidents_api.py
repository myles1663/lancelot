import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth
from src.core import auth_api
from src.core.operator_identity import OperatorIdentity


class _FakeIncident:
    def __init__(self):
        self.status = "OPEN"
        self.severity = "LOW"
        self.responder_id = None
        self.acknowledged_at = None
        self.timeline = []
        self.remediation_receipts = []
        self.board_report_generated = False
        self.root_cause = None
        self.closed_at = None
        self.closed_by = None

    def add_timeline_entry(self, entry):
        self.timeline.append(entry)


class _FakeStore:
    def update(self, incident):
        self.last_updated = incident


@pytest.fixture
def incident_setup(monkeypatch):
    from src.incidents import api as incidents_api

    auth_api._sessions.clear()
    api_auth.init_api_auth(lambda request: True)
    identity = OperatorIdentity(
        operator_id="op-arthur",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-20T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )
    auth_api._sessions["incident-admin"] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": identity,
        "capabilities": sorted({"warroom.login", "incidents.admin"}),
        "groups": [],
    }

    incident = _FakeIncident()
    store = _FakeStore()
    emitted = []

    monkeypatch.setattr(incidents_api, "get_incident_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(incidents_api, "_get_or_404", lambda _store, incident_id: incident)
    monkeypatch.setattr(
        incidents_api,
        "_emit_receipt",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )

    app = FastAPI()
    app.include_router(incidents_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "incident-admin")
    return client, incident, emitted


def test_acknowledge_rejects_undeclared_fields(incident_setup):
    client, _, _ = incident_setup
    response = client.post(
        "/api/incidents/inc-1/acknowledge",
        json={"operator_id": "Mallory", "debug": True},
    )
    assert response.status_code == 422


def test_status_rejects_undeclared_fields(incident_setup):
    client, _, _ = incident_setup
    response = client.post(
        "/api/incidents/inc-1/status",
        json={"status": "INVESTIGATING", "note": "tracking", "operator_id": "Mallory"},
    )
    assert response.status_code == 422


def test_status_rejects_invalid_enum_value(incident_setup):
    client, _, _ = incident_setup
    response = client.post(
        "/api/incidents/inc-1/status",
        json={"status": "TOTALLY_BROKEN", "note": "tracking"},
    )
    assert response.status_code == 400
    assert "Invalid status" in response.json()["detail"]


def test_link_receipt_rejects_undeclared_fields(incident_setup):
    client, _, _ = incident_setup
    response = client.post(
        "/api/incidents/inc-1/link-receipt",
        json={"receipt_id": "rcpt-1", "force": True},
    )
    assert response.status_code == 422


def test_escalate_rejects_invalid_severity(incident_setup):
    client, _, _ = incident_setup
    response = client.post(
        "/api/incidents/inc-1/escalate",
        json={"new_severity": "BROKEN", "reason": "bad input"},
    )
    assert response.status_code == 400
    assert "Invalid severity" in response.json()["detail"]


def test_close_rejects_undeclared_fields(incident_setup):
    client, _, _ = incident_setup
    response = client.post(
        "/api/incidents/inc-1/close",
        json={"root_cause": "done", "generate_report": False, "operator_id": "Mallory"},
    )
    assert response.status_code == 422
