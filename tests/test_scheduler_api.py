from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.core.scheduler_api import init_scheduler_api, router


def _build_client():
    api_auth.init_api_auth(lambda request: True)
    auth_api._sessions.clear()
    auth_api._sessions["scheduler-test-session"] = {
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
        "capabilities": sorted({"warroom.login", "scheduler.admin"}),
        "groups": [],
    }
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "scheduler-test-session")
    return client


class _Service:
    def get_job(self, job_id):
        if job_id != "job-1":
            return None
        return type("Job", (), {"id": "job-1", "name": "Sync Tickets"})()


class _Executor:
    def __init__(self):
        self.calls = []

    def execute_job_with_identity(self, job_id, **kwargs):
        self.calls.append((job_id, kwargs))
        return type(
            "Result",
            (),
            {
                "executed": True,
                "success": True,
                "skip_reason": None,
                "error": None,
                "duration_ms": 12.34,
            },
        )()


def test_trigger_job_forwards_identity_and_emits_governance_receipt(monkeypatch):
    client = _build_client()
    executor = _Executor()
    init_scheduler_api(service=_Service(), executor=executor)

    receipt_calls = []

    def _fake_emit(identity, action_type, action_name, inputs=None, outputs=None, metadata=None, quest_id=None):
        receipt_calls.append(
            {
                "identity": identity,
                "action_type": action_type.value,
                "action_name": action_name,
                "inputs": inputs,
                "outputs": outputs,
            }
        )
        return object()

    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt_for_identity",
        _fake_emit,
    )

    response = client.post("/api/scheduler/jobs/job-1/trigger")
    assert response.status_code == 200
    assert executor.calls == [
        (
            "job-1",
            {
                "operator_id": "op-arthur",
                "session_id": "session-1",
                "actor": "Arthur",
            },
        )
    ]
    assert receipt_calls[0]["action_type"] == "scheduler_task_triggered"
    assert receipt_calls[0]["inputs"]["job_id"] == "job-1"
    assert receipt_calls[0]["outputs"]["success"] is True


def test_trigger_job_fails_closed_without_executor():
    client = _build_client()
    init_scheduler_api(service=_Service(), executor=None)

    response = client.post("/api/scheduler/jobs/job-1/trigger")
    assert response.status_code == 503
    assert response.json()["error"] == "Job executor not initialized"


class _FullJob:
    id = "job-1"
    name = "Sync Tickets"
    skill = "ticket.sync"
    enabled = True
    trigger_type = "interval"
    trigger_value = "5m"
    timezone = "UTC"
    requires_ready = True
    requires_approvals = ["owner"]
    timeout_s = 30
    description = "Sync enterprise tickets"
    last_run_at = "2026-04-10T00:00:00Z"
    last_run_status = "success"
    last_run_error = None
    run_count = 3
    registered_at = "2026-04-01T00:00:00Z"


class _FullService:
    def __init__(self):
        self.job = _FullJob()
        self.calls = []

    def list_jobs(self):
        self.calls.append(("list",))
        return [self.job]

    def get_job(self, job_id):
        return self.job if job_id == "job-1" else None

    def enable_job(self, job_id):
        self.calls.append(("enable", job_id))
        if job_id == "missing":
            raise ValueError("job not found")

    def disable_job(self, job_id):
        self.calls.append(("disable", job_id))
        if job_id == "explode":
            raise RuntimeError("disable failed")

    def delete_job(self, job_id):
        self.calls.append(("delete", job_id))
        if job_id == "explode":
            raise RuntimeError("delete failed")

    def update_job_timezone(self, job_id, timezone):
        self.calls.append(("timezone", job_id, timezone))
        if job_id == "missing":
            raise ValueError("job not found")
        if job_id == "explode":
            raise RuntimeError("timezone failed")


class _ApprovalExecutor(_Executor):
    pending_approvals = {"job-1": {"requested_by": "scheduler"}}

    def __init__(self, approved=True):
        super().__init__()
        self.approved = approved

    def approve_job(self, job_id, **kwargs):
        self.calls.append((job_id, kwargs))
        return self.approved


def test_scheduler_jobs_crud_and_error_paths(monkeypatch):
    client = _build_client()
    service = _FullService()
    init_scheduler_api(service=service, executor=_ApprovalExecutor())

    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt",
        lambda *args, **kwargs: object(),
    )

    listed = client.get("/api/scheduler/jobs").json()
    assert listed["total"] == 1
    assert listed["enabled_count"] == 1
    assert listed["jobs"][0]["skill"] == "ticket.sync"

    assert client.get("/api/scheduler/jobs/job-1").json()["name"] == "Sync Tickets"
    assert client.get("/api/scheduler/jobs/missing").status_code == 404

    assert client.post("/api/scheduler/jobs/job-1/enable").json() == {"id": "job-1", "enabled": True}
    assert client.post("/api/scheduler/jobs/job-1/disable").json() == {"id": "job-1", "enabled": False}
    assert client.post("/api/scheduler/jobs/missing/enable").status_code == 404
    assert client.post("/api/scheduler/jobs/explode/disable").status_code == 500

    deleted = client.delete("/api/scheduler/jobs/job-1").json()
    assert deleted == {"id": "job-1", "deleted": True}
    assert ("delete", "job-1") in service.calls
    assert client.delete("/api/scheduler/jobs/missing").status_code == 404


def test_scheduler_trigger_approve_pending_and_timezone_paths(monkeypatch):
    client = _build_client()
    service = _FullService()
    executor = _ApprovalExecutor(approved=True)
    init_scheduler_api(service=service, executor=executor)

    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt_for_identity",
        lambda *args, **kwargs: object(),
    )

    assert client.post("/api/scheduler/jobs/missing/trigger").status_code == 404

    approved = client.post("/api/scheduler/jobs/job-1/approve").json()
    assert approved["approved"] is True
    assert executor.calls[-1][1]["operator_id"] == "op-arthur"

    executor.approved = False
    not_pending = client.post("/api/scheduler/jobs/job-1/approve").json()
    assert not_pending["approved"] is False

    pending = client.get("/api/scheduler/approvals/pending").json()
    assert pending["count"] == 1
    assert pending["pending"]["job-1"]["requested_by"] == "scheduler"

    updated = client.patch("/api/scheduler/jobs/job-1/timezone", json={"timezone": "America/New_York"}).json()
    assert updated == {"id": "job-1", "timezone": "America/New_York"}
    assert client.patch("/api/scheduler/jobs/job-1/timezone", json={"timezone": "No/Such_Zone"}).status_code == 400
    assert client.patch("/api/scheduler/jobs/missing/timezone", json={"timezone": "UTC"}).status_code == 404
    assert client.patch("/api/scheduler/jobs/explode/timezone", json={"timezone": "UTC"}).status_code == 500


def test_scheduler_uninitialized_and_service_exception_paths(monkeypatch):
    client = _build_client()
    init_scheduler_api(service=None, executor=None)

    assert client.get("/api/scheduler/jobs").status_code == 503
    assert client.get("/api/scheduler/jobs/job-1").status_code == 503
    assert client.post("/api/scheduler/jobs/job-1/enable").status_code == 503
    assert client.post("/api/scheduler/jobs/job-1/disable").status_code == 503
    assert client.delete("/api/scheduler/jobs/job-1").status_code == 503
    assert client.post("/api/scheduler/jobs/job-1/approve").status_code == 503
    assert client.get("/api/scheduler/approvals/pending").status_code == 503
    assert client.patch("/api/scheduler/jobs/job-1/timezone", json={"timezone": "UTC"}).status_code == 503

    class _BrokenListService(_FullService):
        def list_jobs(self):
            raise RuntimeError("list failed")

    init_scheduler_api(service=_BrokenListService(), executor=_ApprovalExecutor())
    assert client.get("/api/scheduler/jobs").status_code == 500

    class _BrokenTriggerExecutor(_ApprovalExecutor):
        def execute_job_with_identity(self, job_id, **kwargs):
            raise RuntimeError("execute failed")

    init_scheduler_api(service=_FullService(), executor=_BrokenTriggerExecutor())
    monkeypatch.setattr(
        "src.core.governance_receipts.emit_governance_receipt_for_identity",
        lambda *args, **kwargs: object(),
    )
    assert client.post("/api/scheduler/jobs/job-1/trigger").status_code == 500
