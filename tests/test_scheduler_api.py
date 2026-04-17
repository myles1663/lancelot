from fastapi import FastAPI
from fastapi.testclient import TestClient

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
