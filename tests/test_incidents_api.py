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

    def to_dict(self):
        return {
            "incident_id": "inc-1",
            "status": self.status,
            "severity": self.severity,
            "timeline": [entry.__dict__ for entry in self.timeline],
        }


class _FakeStore:
    def __init__(self, incident=None):
        self.incident = incident
        self.last_updated = None

    def list_incidents(self, **kwargs):
        return [self.incident.to_dict()] if self.incident else []

    def count_open(self):
        return {self.incident.severity: 1} if self.incident else {}

    def get(self, incident_id):
        return self.incident if incident_id == "inc-1" else None

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
    store = _FakeStore(incident)
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


def test_incident_read_endpoints_return_list_stats_and_detail(incident_setup):
    client, _, _ = incident_setup

    listing = client.get("/api/incidents?status=OPEN&limit=10")
    stats = client.get("/api/incidents/stats")
    detail = client.get("/api/incidents/inc-1")

    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert stats.json()["open"] == 1
    assert stats.json()["by_severity"] == {"LOW": 1}
    assert detail.json()["incident_id"] == "inc-1"


def test_incident_lifecycle_mutations_update_state_and_emit_receipts(incident_setup):
    client, incident, emitted = incident_setup

    assert client.post("/api/incidents/inc-1/acknowledge", json={}).json()["status"] == "acknowledged"
    assert incident.status == "INVESTIGATING"
    assert incident.responder_id == "op-arthur"

    status = client.post(
        "/api/incidents/inc-1/status",
        json={"status": "CONTAINED", "note": "patched"},
    )
    assert status.json() == {"status": "updated", "previous": "INVESTIGATING", "new": "CONTAINED"}

    timeline = client.post("/api/incidents/inc-1/timeline", json={"entry_text": "added evidence"})
    assert timeline.json()["status"] == "added"

    link = client.post("/api/incidents/inc-1/link-receipt", json={"receipt_id": "receipt-1"})
    duplicate_link = client.post("/api/incidents/inc-1/link-receipt", json={"receipt_id": "receipt-1"})
    assert link.json()["receipt_id"] == "receipt-1"
    assert duplicate_link.status_code == 200
    assert incident.remediation_receipts == ["receipt-1"]

    escalation = client.post(
        "/api/incidents/inc-1/escalate",
        json={"new_severity": "MEDIUM", "reason": "customer impact"},
    )
    assert escalation.json() == {"status": "escalated", "previous": "LOW", "new": "MEDIUM"}
    assert incident.severity == "MEDIUM"
    assert len(emitted) >= 5


def test_close_requires_root_cause_for_high_severity(incident_setup):
    client, incident, _ = incident_setup
    incident.severity = "HIGH"

    response = client.post("/api/incidents/inc-1/close", json={"generate_report": False})

    assert response.status_code == 400
    assert "Root cause is required" in response.json()["detail"]


def test_close_incident_as_false_positive_or_closed_with_report(incident_setup, monkeypatch, tmp_path):
    from src.incidents import api as incidents_api

    client, incident, emitted = incident_setup
    generated = []
    monkeypatch.setattr(incidents_api, "_data_dir", str(tmp_path))
    monkeypatch.setattr(
        "src.incidents.report_generator.generate_incident_report",
        lambda incident, output_dir=None: generated.append((incident, output_dir)) or b"%PDF",
    )

    false_positive = client.post(
        "/api/incidents/inc-1/close",
        json={"false_positive": True, "false_positive_reason": "test signal"},
    )
    assert false_positive.json()["status"] == "false_positive"
    assert incident.status == "FALSE_POSITIVE"

    incident.status = "CONTAINED"
    incident.severity = "LOW"
    closed = client.post(
        "/api/incidents/inc-1/close",
        json={"root_cause": "dependency outage", "generate_report": True},
    )

    assert closed.json()["status"] == "closed"
    assert closed.json()["board_report_generated"] is True
    assert generated[0][1] == str(tmp_path / "incident_reports")
    assert emitted[-1][0][0].value == "incident_closed"


def test_generate_and_download_report(incident_setup, monkeypatch, tmp_path):
    from src.incidents import api as incidents_api

    client, incident, _ = incident_setup
    monkeypatch.setattr(incidents_api, "_data_dir", str(tmp_path))
    monkeypatch.setattr(
        "src.incidents.report_generator.generate_incident_report",
        lambda incident, output_dir=None: b"PDF-BYTES",
    )

    generated = client.post("/api/incidents/inc-1/report")
    assert generated.json() == {"status": "generated", "size_bytes": 9}
    assert incident.board_report_generated is True

    missing = client.get("/api/incidents/inc-1/report/download")
    assert missing.status_code == 404

    report_dir = tmp_path / "incident_reports"
    report_dir.mkdir()
    (report_dir / "inc-1.pdf").write_bytes(b"PDF")
    downloaded = client.get("/api/incidents/inc-1/report/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"PDF"


def test_incident_helpers_handle_missing_store_missing_incident_and_receipt_failures(monkeypatch):
    from src.incidents import api as incidents_api
    from src.shared.receipts import ActionType

    with pytest.raises(Exception) as missing_store:
        incidents_api._get_or_404(None, "inc-1")
    assert missing_store.value.status_code == 503

    with pytest.raises(Exception) as missing_incident:
        incidents_api._get_or_404(_FakeStore(None), "inc-1")
    assert missing_incident.value.status_code == 404

    monkeypatch.setattr(
        incidents_api,
        "create_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("receipt failed")),
    )
    incidents_api._emit_receipt(ActionType.INCIDENT_ACKNOWLEDGED, {"incident_id": "inc-1"})
