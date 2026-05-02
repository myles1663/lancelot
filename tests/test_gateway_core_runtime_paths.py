import asyncio
import json
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

import gateway


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.headers = {}


def _response_body(response):
    return json.loads(response.body.decode("utf-8"))


def test_audit_user_resolution_and_security_headers(monkeypatch):
    monkeypatch.setattr(
        "src.core.auth_api.resolve_operator_identity",
        lambda request: SimpleNamespace(display_name="Myles", operator_id="op-1"),
    )
    monkeypatch.setattr("src.core.auth_api.get_api_key_identity", lambda request: None)
    assert gateway._resolve_audit_user(SimpleNamespace()) == "Myles"

    monkeypatch.setattr("src.core.auth_api.resolve_operator_identity", lambda request: None)
    monkeypatch.setattr(
        "src.core.auth_api.get_api_key_identity",
        lambda request: SimpleNamespace(display_name="", operator_id="api-op"),
    )
    assert gateway._resolve_audit_user(SimpleNamespace()) == "api-op"

    monkeypatch.setattr(
        "src.core.auth_api.resolve_operator_identity",
        lambda request: (_ for _ in ()).throw(RuntimeError("auth down")),
    )
    assert gateway._resolve_audit_user(SimpleNamespace()) == "operator"

    async def call_next(_request):
        return FakeResponse(status_code=503)

    before_errors = gateway._error_count
    before_total = gateway._total_requests
    response = asyncio.run(gateway.security_headers_middleware(SimpleNamespace(), call_next))
    assert gateway._total_requests == before_total + 1
    assert gateway._error_count == before_errors + 1
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


def test_crusader_transition_rolls_back_when_runtime_soul_refresh_fails(monkeypatch):
    calls = []
    crusader = SimpleNamespace(
        activate=lambda: calls.append("activate") or "activated",
        deactivate=lambda: calls.append("deactivate") or "deactivated",
    )
    monkeypatch.setattr(gateway, "crusader_mode", crusader)
    monkeypatch.setattr(gateway, "_refresh_runtime_soul_from_store", lambda: calls.append("refresh"))

    assert gateway._transition_crusader_mode("activate") == (True, "activated")
    assert calls == ["activate", "refresh"]

    calls.clear()
    refresh_attempts = {"count": 0}

    def failing_refresh():
        refresh_attempts["count"] += 1
        if refresh_attempts["count"] == 1:
            raise RuntimeError("soul unavailable")
        calls.append("rollback_refresh")

    monkeypatch.setattr(gateway, "_refresh_runtime_soul_from_store", failing_refresh)
    ok, message = gateway._transition_crusader_mode("deactivate")
    assert ok is False
    assert "failed to refresh runtime Soul" in message
    assert calls == ["deactivate", "activate", "rollback_refresh"]

    with pytest.raises(ValueError):
        gateway._transition_crusader_mode("bogus")


def test_work_ledger_sync_reconcile_and_actioncard_archive(monkeypatch):
    run = SimpleNamespace(
        run_id="retry-1",
        retry_of_run_id="source-1",
        status="succeeded",
    )
    source_before = SimpleNamespace(status="blocked")
    source_after = SimpleNamespace(status="completed")
    work_store = SimpleNamespace(
        upsert_from_chat_run=lambda run, event_type, metadata=None: SimpleNamespace(
            quest_id=run.run_id, event_type=event_type, metadata=metadata
        ),
        mark_superseded_by_retry=lambda quest_id, retry_run_id, retry_status: source_after,
        get_work=lambda quest_id: source_before,
    )
    monkeypatch.setattr(gateway, "work_ledger_store", work_store)

    item = gateway._sync_work_ledger_from_chat_run(run, event_type="chat_run_completed", metadata={"a": 1})
    assert item.quest_id == "retry-1"
    assert item.metadata == {"a": 1}
    assert gateway._close_superseded_retry_source(SimpleNamespace(run_id="r", retry_of_run_id="", status="succeeded")) is None

    monkeypatch.setattr(
        gateway,
        "chat_run_store",
        SimpleNamespace(list_terminal_retries=lambda limit=200: [run]),
    )
    assert gateway._reconcile_superseded_retry_work(limit=5) == 1

    monkeypatch.setattr(
        gateway,
        "chat_run_store",
        SimpleNamespace(list_terminal_retries=lambda limit=200: (_ for _ in ()).throw(RuntimeError("db down"))),
    )
    assert gateway._reconcile_superseded_retry_work() == 0

    monkeypatch.setattr(
        gateway,
        "work_ledger_store",
        SimpleNamespace(
            upsert_from_chat_run=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ledger down")),
            mark_superseded_by_retry=lambda *args, **kwargs: None,
        ),
    )
    assert gateway._sync_work_ledger_from_chat_run(run) is None

    gateway.app.state.actioncard_store = SimpleNamespace(
        list_pending_by_quest=lambda quest_id, limit=50: [
            SimpleNamespace(card_id="card-1", title="Approve deploy", source_system="governance"),
            SimpleNamespace(card_id="card-2", title="Broken card", source_system="scheduler"),
        ]
    )
    gateway.app.state.actioncard_resolver = SimpleNamespace(
        archive=lambda card_id, **kwargs: {"status": "archived"} if card_id == "card-1" else (_ for _ in ()).throw(RuntimeError("archive failed"))
    )
    archived = gateway._archive_pending_actioncards_for_work(
        "quest-1",
        identity=SimpleNamespace(operator_id="op-1", session_id="sess-1", display_name="Myles"),
        reason="cleanup",
    )
    assert archived == [{"card_id": "card-1", "title": "Approve deploy", "source_system": "governance"}]

    delattr(gateway.app.state, "actioncard_store")
    delattr(gateway.app.state, "actioncard_resolver")
    assert gateway._archive_pending_actioncards_for_work("quest-1", identity=SimpleNamespace(), reason="x") == []


def test_chat_progress_events_emit_and_skip_terminal_runs(monkeypatch):
    emitted = []
    synced = []
    active_run = SimpleNamespace(run_id="run-1", status="running")
    terminal_run = SimpleNamespace(run_id="run-2", status="succeeded")
    store = SimpleNamespace(
        record_progress=lambda run_id, **kwargs: active_run if run_id == "run-1" else terminal_run
    )
    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "_sync_work_ledger_from_chat_run", lambda run, event_type, metadata=None: synced.append((run.run_id, event_type, metadata)))
    monkeypatch.setattr(gateway, "_emit_chat_run_event", lambda event_type, run: emitted.append((event_type, run.run_id)))

    run = gateway._record_persisted_chat_progress(
        "run-1",
        phase="execution",
        message="working",
        event_timestamp=12.0,
        severity="warning",
        degraded=True,
        degraded_reason="slow provider",
        metadata={"tool": "shell"},
    )
    assert run is active_run
    assert synced[-1][2] == {
        "tool": "shell",
        "severity": "warning",
        "degraded": True,
        "degraded_reason": "slow provider",
    }
    assert emitted == [("chat.run_progress", "run-1")]

    gateway._record_persisted_chat_progress("run-2", phase="done", message="complete")
    assert emitted == [("chat.run_progress", "run-1")]

    asyncio.run(
        gateway._record_chat_progress_event(
            SimpleNamespace(
                payload={
                    "quest_id": "run-1",
                    "phase": "approval",
                    "message": "waiting",
                    "severity": "info",
                    "custom": "value",
                },
                timestamp=44.0,
            )
        )
    )
    assert synced[-1][2]["custom"] == "value"

    asyncio.run(gateway._record_chat_progress_event(SimpleNamespace(payload={})))


def test_fast_runtime_commands_and_receipts(monkeypatch):
    receipts = []
    monkeypatch.setattr(
        gateway,
        "get_receipt_service",
        lambda data_dir: SimpleNamespace(create=lambda receipt: receipts.append(receipt)),
    )
    monkeypatch.setattr(
        gateway,
        "create_finalized_receipt",
        lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs),
    )

    gateway._emit_fast_runtime_receipt(
        action_name="runtime_status_report",
        message="status " * 200,
        response_text="ok",
        checks=["health"],
        degraded_conditions=[],
        result="ok",
        quest_id="quest-1",
        identity=SimpleNamespace(operator_id="op-1", session_id="sess-1"),
    )
    assert receipts[0].kwargs["operator_id"] == "op-1"

    monkeypatch.setattr(gateway, "get_receipt_service", lambda data_dir: (_ for _ in ()).throw(RuntimeError("receipts down")))
    gateway._emit_fast_runtime_receipt(
        action_name="runtime_status_report",
        message="status",
        response_text="ok",
        checks=[],
        degraded_conditions=[],
        result="ok",
    )

    emitted = []
    monkeypatch.setattr(gateway, "_emit_fast_runtime_receipt", lambda **kwargs: emitted.append(kwargs))
    monkeypatch.setattr(
        gateway,
        "health_check",
        lambda: {
            "components": {
                "gateway": "ok",
                "orchestrator": "degraded",
                "memory": "ok",
                "sentry": "disabled",
                "local_llm": "degraded",
            },
            "local_llm": {
                "roles": {
                    "planner": {"enabled": True, "ready": False, "status": "cold"},
                    "scrubber": {"enabled": False, "ready": False},
                }
            },
            "uptime_seconds": 7,
        },
    )
    response = gateway._try_handle_fast_runtime_command(
        "runtime status",
        quest_id="quest-1",
        identity=SimpleNamespace(operator_id="op-1"),
    )
    assert "Local model lane: degraded (0/1 roles ready)" in response
    assert emitted[-1]["result"] == "degraded"
    assert "local_model_role:planner=cold" in emitted[-1]["degraded_conditions"]

    monkeypatch.setattr(
        gateway,
        "health_check",
        lambda: JSONResponse(status_code=500, content={"status": "error"}),
    )
    assert "Runtime status unavailable" in gateway._try_handle_fast_runtime_command("health")

    monkeypatch.setattr(gateway, "_try_handle_operational_report_command", lambda *args, **kwargs: "operational report")
    assert gateway._try_handle_fast_runtime_command("please run a read-only operational smoke report") == "operational report"


def test_operator_work_status_command_reports_items_scheduler_and_degraded(monkeypatch):
    emitted = []
    monkeypatch.setattr(gateway, "_emit_fast_runtime_receipt", lambda **kwargs: emitted.append(kwargs))
    monkeypatch.setattr(
        gateway,
        "_active_work_report_snapshot",
        lambda **kwargs: {
            "items": [
                {
                    "quest_id": "q1",
                    "status": "blocked",
                    "phase": "approval",
                    "objective": "finish deployment",
                    "blocker": "approval",
                    "next_action": "approve",
                }
            ]
            * 6,
            "degraded_conditions": ["work ledger stale"],
        },
    )
    monkeypatch.setattr(
        gateway,
        "_scheduler_report_snapshot",
        lambda: {
            "status": "degraded",
            "job_count": 2,
            "enabled_count": 1,
            "degraded_conditions": ["scheduler paused"],
            "jobs": [
                {
                    "id": "daily_news_brief",
                    "last_run_status": "failed",
                    "last_run_error": "Telegram unavailable",
                },
                {
                    "id": "ticket_sentinel_sync",
                    "last_run_status": "ok",
                    "last_run_at": "2026-05-01T00:00:00Z",
                },
            ],
        },
    )
    text = gateway._try_handle_operator_work_status_command(
        "continue with the plan status",
        identity=SimpleNamespace(operator_id="op-1", session_id="sess-1"),
    )
    assert "Active work: 6 open item(s)." in text
    assert "... 1 additional items omitted" in text
    assert "Failed scheduler jobs:" in text
    assert "Ticket Sentinel: last_status=ok" in text
    assert "Degraded conditions: work ledger stale, scheduler paused." in text
    assert emitted[-1]["result"] == "degraded"


@pytest.mark.asyncio
async def test_execute_chat_turn_and_async_run_paths(monkeypatch):
    audit_events = []
    monkeypatch.setattr(
        gateway,
        "onboarding_orch",
        SimpleNamespace(
            state="READY",
            determine_state=lambda: "NEEDS_SETUP",
            process=lambda user, message: f"onboarding:{user}:{message}",
        ),
    )
    assert await gateway._execute_chat_turn(
        "hello",
        user="Myles",
        channel="warroom",
        identity=SimpleNamespace(display_name="Myles", operator_id="op-1", session_id="sess-1"),
    ) == "onboarding:Myles:hello"

    monkeypatch.setattr(gateway.onboarding_orch, "determine_state", lambda: "READY")
    monkeypatch.setattr(gateway, "_try_handle_fast_runtime_command", lambda *args, **kwargs: "fast status")
    assert await gateway._execute_chat_turn(
        "status",
        user="Myles",
        channel="warroom",
        identity=SimpleNamespace(display_name="Myles", operator_id="op-1", session_id="sess-1"),
    ) == "fast status"

    monkeypatch.setattr(gateway, "_try_handle_fast_runtime_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway,
        "crusader_mode",
        SimpleNamespace(
            is_active=False,
            should_intercept=lambda message: (True, "activate"),
        ),
    )
    monkeypatch.setattr(gateway, "_transition_crusader_mode", lambda action: (True, "activated"))
    monkeypatch.setattr(
        gateway,
        "main_orchestrator",
        SimpleNamespace(audit_logger=SimpleNamespace(log_event=lambda *args: audit_events.append(args))),
    )
    assert await gateway._execute_chat_turn(
        "activate crusader",
        user="Myles",
        channel="warroom",
        identity=SimpleNamespace(display_name="Myles", operator_id="op-1", session_id="sess-1"),
    ) == "activated"
    assert audit_events[-1][0] == "CRUSADER_MODE_ACTIVATED"

    monkeypatch.setattr(
        gateway,
        "crusader_mode",
        SimpleNamespace(
            is_active=True,
            should_intercept=lambda message: (False, ""),
        ),
    )
    monkeypatch.setattr(
        gateway,
        "crusader_adapter",
        SimpleNamespace(
            check_auto_pause=lambda message: True,
            format_response=lambda text: f"crusader:{text}",
        ),
    )
    assert "Authority required" in await gateway._execute_chat_turn(
        "danger",
        user="Myles",
        channel="warroom",
        identity=SimpleNamespace(display_name="Myles", operator_id="op-1", session_id="sess-1"),
    )


@pytest.mark.asyncio
async def test_async_chat_run_worker_handles_success_cancelled_and_failure(monkeypatch):
    events = []
    synced = []
    progress = []
    runs = {}

    def make_run(run_id, status="queued", message="work"):
        run = SimpleNamespace(
            run_id=run_id,
            status=status,
            phase=status,
            message_text=message,
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
        )
        runs[run_id] = run
        return run

    make_run("run-success")
    make_run("run-cancel-late")
    make_run("run-fail")

    class Store:
        def mark_running(self, run_id):
            run = runs.get(run_id)
            if run is None:
                return None
            run.status = "running"
            return run

        def get(self, run_id):
            return runs.get(run_id)

        def complete(self, run_id, status, response, crusader_mode=False):
            run = runs[run_id]
            run.status = status
            run.response = response
            return run

        def fail(self, run_id, error):
            run = runs[run_id]
            run.status = "failed"
            run.error = error
            return run

    monkeypatch.setattr(gateway, "chat_run_store", Store())
    monkeypatch.setattr(gateway, "_sync_work_ledger_from_chat_run", lambda run, event_type, metadata=None: synced.append((run.run_id, event_type)))
    monkeypatch.setattr(gateway, "_emit_chat_run_event", lambda event_type, run: events.append((event_type, run.run_id)))
    monkeypatch.setattr(gateway, "_record_persisted_chat_progress", lambda run_id, **kwargs: progress.append((run_id, kwargs)) or runs.get(run_id))
    monkeypatch.setattr(gateway, "_is_fast_runtime_command", lambda message: True)
    monkeypatch.setattr(gateway, "crusader_mode", SimpleNamespace(is_active=False))

    async def execute_chat(message, **kwargs):
        if kwargs["quest_id"] == "run-cancel-late":
            runs["run-cancel-late"].status = "cancelled"
            return "late success"
        if kwargs["quest_id"] == "run-fail":
            raise RuntimeError("provider failed")
        return "completed response"

    monkeypatch.setattr(gateway, "_execute_chat_turn", execute_chat)

    await gateway._execute_async_chat_run(
        "run-success",
        message="fast status",
        user="Myles",
        channel="warroom",
        identity=SimpleNamespace(),
    )
    assert runs["run-success"].status == "succeeded"
    assert ("chat.run_completed", "run-success") in events

    await gateway._execute_async_chat_run(
        "run-cancel-late",
        message="fast status",
        user="Myles",
        channel="warroom",
        identity=SimpleNamespace(),
    )
    assert ("run-cancel-late", "chat_run_cancelled") in synced

    await gateway._execute_async_chat_run(
        "run-fail",
        message="fast status",
        user="Myles",
        channel="warroom",
        identity=SimpleNamespace(),
    )
    assert runs["run-fail"].status == "failed"
    assert ("chat.run_failed", "run-fail") in events


def test_track_async_chat_task_logs_cancelled_and_failed_tasks():
    async def cancelled():
        raise asyncio.CancelledError()

    async def failed():
        raise RuntimeError("task crashed")

    async def exercise():
        task_cancelled = asyncio.create_task(cancelled())
        task_failed = asyncio.create_task(failed())
        gateway._track_async_chat_task(task_cancelled)
        gateway._track_async_chat_task(task_failed)
        await asyncio.gather(task_cancelled, task_failed, return_exceptions=True)
        await asyncio.sleep(0)
        assert task_cancelled not in gateway._async_chat_tasks
        assert task_failed not in gateway._async_chat_tasks

    asyncio.run(exercise())


def test_health_and_readiness_error_paths(monkeypatch):
    monkeypatch.setattr(
        gateway,
        "build_health_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("health down")),
    )
    health = gateway.health_check()
    assert health.status_code == 500
    assert _response_body(health)["error"] == "Health check failed"

    monkeypatch.setattr(
        gateway,
        "build_readiness_snapshot",
        lambda **kwargs: (503, {"ready": False, "reason": "booting"}),
    )
    ready = gateway.readiness_check()
    assert ready.status_code == 503
    assert _response_body(ready)["reason"] == "booting"

