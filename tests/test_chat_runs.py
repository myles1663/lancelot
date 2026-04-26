import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chat_runs import ChatRunStore
from work_ledger import WorkLedgerStore


@pytest.fixture
def gateway_work_ledger(tmp_path, monkeypatch):
    import gateway

    store = WorkLedgerStore(str(tmp_path / "work_ledger.sqlite"))
    monkeypatch.setattr(gateway, "work_ledger_store", store)
    monkeypatch.setattr(gateway.main_orchestrator, "work_ledger_store", store)
    try:
        yield store
    finally:
        store.close()


def test_chat_run_store_records_lifecycle(tmp_path):
    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="continue with the dashboard work",
        )

        assert run.status == "queued"
        assert run.phase == "queued"
        assert run.message_preview == "continue with the dashboard work"

        running = store.mark_running(run.run_id)
        assert running is not None
        assert running.status == "running"
        assert running.phase == "executing"
        assert running.started_at is not None

        completed = store.complete(
            run.run_id,
            status="blocked",
            response="Pending approval. Send `continue` after approval.",
        )
        assert completed is not None
        assert completed.status == "blocked"
        assert completed.phase == "blocked"
        assert completed.completed_at is not None

        listed = store.list_recent(session_id="sess-1")
        assert [item.run_id for item in listed] == [run.run_id]
        assert store.list_recent(session_id="other") == []
    finally:
        store.close()


def test_chat_run_store_records_progress_timings(tmp_path):
    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="show status",
        )
        store.record_progress(
            run.run_id,
            phase="preflight",
            message="Running governance preflight checks",
        )
        store.record_progress(
            run.run_id,
            phase="classification",
            message="Classifying request and routing lane",
        )

        updated = store.get(run.run_id)
        assert updated is not None
        assert updated.phase == "classification"
        assert updated.last_progress_message == "Classifying request and routing lane"
        assert [event["phase"] for event in updated.progress_events] == [
            "preflight",
            "classification",
        ]
        assert updated.total_elapsed_ms is not None
        assert set(updated.phase_timings_ms) == {"preflight", "classification"}
    finally:
        store.close()


def test_chat_run_store_marks_stale_active_runs_failed(tmp_path):
    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="long running request",
        )
        stale = store.fail_stale_active_runs(
            max_age_seconds=0,
            reason="Async chat run was still active after gateway restart.",
        )

        assert [item.run_id for item in stale] == [run.run_id]
        failed = store.get(run.run_id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.phase == "failed"
        assert failed.error == "Async chat run was still active after gateway restart."
        assert failed.completed_at is not None
    finally:
        store.close()


def test_chat_run_store_cancels_active_run_and_preserves_terminal_state(tmp_path):
    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="long running governed request",
        )
        store.mark_running(run.run_id)

        cancelled = store.request_cancel(
            run.run_id,
            reason="Operator cancelled from Command Center.",
        )

        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert cancelled.phase == "cancelled"
        assert cancelled.cancel_requested is True
        assert cancelled.cancel_reason == "Operator cancelled from Command Center."
        assert cancelled.cancelled_at is not None

        store.record_progress(
            run.run_id,
            phase="execution",
            message="Late worker progress should not rewrite cancellation",
        )
        late_completion = store.complete(
            run.run_id,
            status="succeeded",
            response="late success",
        )

        assert late_completion is not None
        assert late_completion.status == "cancelled"
        assert late_completion.response == ""
        assert late_completion.phase == "cancelled"
    finally:
        store.close()


def test_chat_run_store_retries_failed_run_from_retained_message(tmp_path):
    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="retry this exact governed request",
        )
        store.fail(run.run_id, "provider timed out")

        retry = store.create_retry(
            run.run_id,
            request_id="req-2",
            session_id="sess-1",
            operator_id="op-1",
        )

        assert retry is not None
        assert retry.status == "queued"
        assert retry.message_text == "retry this exact governed request"
        assert retry.message_preview == "retry this exact governed request"
        assert retry.retry_of_run_id == run.run_id
        assert retry.retry_count == 1
    finally:
        store.close()


def test_chat_run_store_retries_blocked_run_from_retained_message(tmp_path):
    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="resume this blocked governed request",
        )
        store.complete(
            run.run_id,
            status="blocked",
            response="Approval group ID: abc-123",
        )

        retry = store.create_retry(
            run.run_id,
            request_id="req-2",
            session_id="sess-1",
            operator_id="op-1",
        )

        assert retry is not None
        assert retry.status == "queued"
        assert retry.message_text == "resume this blocked governed request"
        assert retry.message_preview == "resume this blocked governed request"
        assert retry.retry_of_run_id == run.run_id
        assert retry.retry_count == 1
    finally:
        store.close()


def test_chat_run_store_lists_terminal_retries_only(tmp_path):
    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        blocked = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="blocked request",
        )
        store.complete(blocked.run_id, status="blocked", response="Approval required")
        retry = store.create_retry(
            blocked.run_id,
            request_id="req-2",
            session_id="sess-1",
            operator_id="op-1",
        )
        assert retry is not None
        active_retry = store.get(retry.run_id)
        assert active_retry is not None

        assert store.list_terminal_retries() == []

        store.complete(retry.run_id, status="succeeded", response="done")
        retries = store.list_terminal_retries()

        assert [item.run_id for item in retries] == [retry.run_id]
        assert retries[0].retry_of_run_id == blocked.run_id
        assert retries[0].status == "succeeded"
    finally:
        store.close()


def test_chat_run_status_classifier_identifies_operator_blocking():
    import gateway

    assert gateway._classify_chat_run_status("done") == "succeeded"
    assert gateway._classify_chat_run_status("Error: provider timeout") == "failed"
    assert gateway._classify_chat_run_status("Approval group ID: abc-123") == "blocked"
    assert gateway._classify_chat_run_status("Pending approval from Commander") == "blocked"


def test_execute_async_chat_run_marks_completion_and_emits_events(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    events = []

    observed_kwargs = {}

    async def fake_execute_chat_turn(*_args, **kwargs):
        observed_kwargs.update(kwargs)
        return "work complete"

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "_execute_chat_turn", fake_execute_chat_turn)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status, run.phase)),
    )

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="finish the dashboard",
        )

        asyncio.run(
            gateway._execute_async_chat_run(
                run.run_id,
                message="finish the dashboard",
                user="Myles",
                channel="warroom",
                identity=SimpleNamespace(
                    display_name="Myles",
                    operator_id="op-1",
                    session_id="sess-1",
                ),
            )
        )

        completed = store.get(run.run_id)
        assert completed is not None
        assert completed.status == "succeeded"
        assert completed.response == "work complete"
        assert observed_kwargs["quest_id"] == run.run_id
        assert [event["phase"] for event in completed.progress_events] == [
            "waiting_worker_slot",
            "execution",
            "finalization",
        ]
        assert completed.progress_events[0]["wait_reason"] == "worker_slot"
        assert completed.progress_events[1]["wait_reason"] == "execution_start"
        assert completed.progress_events[2]["wait_reason"] == "finalization"
        work_item = gateway_work_ledger.get_work(run.run_id)
        assert work_item is not None
        assert work_item.status == "completed"
        assert work_item.phase == "completed"
        assert gateway_work_ledger.list_checkpoints(run.run_id)
        assert events == [
            ("chat.run_progress", "queued", "waiting_worker_slot"),
            ("chat.run_started", "running", "executing"),
            ("chat.run_progress", "running", "execution"),
            ("chat.run_progress", "running", "finalization"),
            ("chat.run_completed", "succeeded", "completed"),
        ]
    finally:
        store.close()


def test_execute_async_chat_run_does_not_overwrite_operator_cancel(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    events = []

    async def fake_execute_chat_turn(*_args, **_kwargs):
        store.request_cancel(
            run.run_id,
            reason="Operator cancelled while provider call was in flight.",
        )
        return "late success"

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "_execute_chat_turn", fake_execute_chat_turn)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status, run.phase)),
    )

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="finish the dashboard",
        )

        asyncio.run(
            gateway._execute_async_chat_run(
                run.run_id,
                message="finish the dashboard",
                user="Myles",
                channel="warroom",
                identity=SimpleNamespace(
                    display_name="Myles",
                    operator_id="op-1",
                    session_id="sess-1",
                ),
            )
        )

        cancelled = store.get(run.run_id)
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert cancelled.response == ""
        work_item = gateway_work_ledger.get_work(run.run_id)
        assert work_item is not None
        assert work_item.status == "cancelled"
        assert events == [
            ("chat.run_progress", "queued", "waiting_worker_slot"),
            ("chat.run_started", "running", "executing"),
            ("chat.run_progress", "running", "execution"),
        ]
    finally:
        store.close()


def test_execute_async_chat_run_keeps_fast_runtime_commands_outside_worker_slot(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    events = []

    async def fake_execute_chat_turn(*_args, **_kwargs):
        return "runtime ok"

    def fail_worker_slot():
        raise AssertionError("fast runtime command should not acquire the governed worker slot")

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "_is_fast_runtime_command", lambda _message: True)
    monkeypatch.setattr(gateway, "_get_async_chat_worker_slot", fail_worker_slot)
    monkeypatch.setattr(gateway, "_execute_chat_turn", fake_execute_chat_turn)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status, run.phase)),
    )

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="status",
        )

        asyncio.run(
            gateway._execute_async_chat_run(
                run.run_id,
                message="status",
                user="Myles",
                channel="warroom",
                identity=SimpleNamespace(
                    display_name="Myles",
                    operator_id="op-1",
                    session_id="sess-1",
                ),
            )
        )

        completed = store.get(run.run_id)
        assert completed is not None
        assert completed.status == "succeeded"
        work_item = gateway_work_ledger.get_work(run.run_id)
        assert work_item is not None
        assert work_item.status == "completed"
        assert [event["phase"] for event in completed.progress_events] == ["execution", "finalization"]
        assert events == [
            ("chat.run_started", "running", "executing"),
            ("chat.run_progress", "running", "execution"),
            ("chat.run_progress", "running", "finalization"),
            ("chat.run_completed", "succeeded", "completed"),
        ]
    finally:
        store.close()


def test_chat_progress_event_updates_async_run(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    events = []
    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.phase)),
    )

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="continue the plan",
        )

        asyncio.run(
            gateway._record_chat_progress_event(
                SimpleNamespace(
                    payload={
                        "quest_id": run.run_id,
                        "phase": "execution",
                        "message": "Preparing governed model request",
                        "wait_reason": "provider_call",
                    },
                    timestamp=1_800_000_000.0,
                )
            )
        )

        updated = store.get(run.run_id)
        assert updated is not None
        assert updated.phase == "execution"
        assert updated.last_progress_message == "Preparing governed model request"
        assert updated.progress_events[-1]["wait_reason"] == "provider_call"
        work_item = gateway_work_ledger.get_work(run.run_id)
        assert work_item is not None
        assert work_item.phase == "execution"
        assert work_item.next_action == "Preparing governed model request"
        assert events == [("chat.run_progress", "execution")]
    finally:
        store.close()


def test_chat_progress_event_preserves_degraded_disclosure(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "_emit_chat_run_event", lambda *_args, **_kwargs: None)

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="large scrubbed request",
        )

        asyncio.run(
            gateway._record_chat_progress_event(
                SimpleNamespace(
                    payload={
                        "quest_id": run.run_id,
                        "phase": "frontier_scrub",
                        "message": "Local scrub fallback active; using deterministic redaction path",
                        "severity": "warning",
                        "degraded": True,
                        "degraded_reason": "deterministic local scrub fallback used",
                        "source": "local_model_error",
                    },
                    timestamp=1_800_000_000.0,
                )
            )
        )

        updated = store.get(run.run_id)
        assert updated is not None
        assert updated.last_progress_message == (
            "Local scrub fallback active; using deterministic redaction path"
        )
        event = updated.progress_events[-1]
        assert event["phase"] == "frontier_scrub"
        assert event["severity"] == "warning"
        assert event["degraded"] is True
        assert event["degraded_reason"] == "deterministic local scrub fallback used"
        assert event["source"] == "local_model_error"
        work_item = gateway_work_ledger.get_work(run.run_id)
        assert work_item is not None
        assert work_item.metadata["degraded_reason"] == "deterministic local scrub fallback used"
    finally:
        store.close()


def test_frontier_scrub_fallback_emits_degraded_progress():
    import orchestrator

    progress_events = []
    receipt_events = []

    fake_runtime = SimpleNamespace(
        _emit_chat_progress=lambda phase, message, **metadata: progress_events.append(
            {"phase": phase, "message": message, **metadata}
        ),
        _emit_frontier_scrub_receipt=lambda **kwargs: receipt_events.append(kwargs),
    )
    result = SimpleNamespace(
        source="local_model_error",
        reason="deterministic local scrub fallback used",
        detected_categories=("email",),
        residual_categories=(),
        scrubbed=True,
        fallback_used=True,
        pre_scrubbed=True,
        pre_scrub_source="deterministic_local",
        local_verification_used=False,
        scrub_stages=("deterministic_prescrub", "deterministic_fallback"),
    )

    orchestrator.LancelotOrchestrator._record_frontier_scrub_result(
        fake_runtime,
        result,
        path="root",
        input_length=128,
    )

    assert progress_events == [
        {
            "phase": "frontier_scrub",
            "message": "Local scrub fallback active; using deterministic redaction path",
            "severity": "warning",
            "degraded": True,
            "degraded_reason": "deterministic local scrub fallback used",
            "source": "local_model_error",
        }
    ]
    assert receipt_events[0]["action_name"] == "pii_scrub_fallback"
    assert receipt_events[0]["fallback_used"] is True


def test_local_model_health_summary_accepts_ready_role_lane():
    import gateway

    summary = gateway._summarize_local_model_role_lane({
        "scrub_region_finder": {
            "enabled": True,
            "ready": True,
            "loaded": True,
            "last_verified_at": "2026-04-22T10:00:00Z",
            "last_checked_at": "2026-04-22T10:00:00Z",
            "last_smoke_elapsed_ms": 200.0,
        },
        "scrub_segment_verifier": {
            "enabled": True,
            "ready": True,
            "loaded": True,
            "last_verified_at": "2026-04-22T10:01:00Z",
            "last_checked_at": "2026-04-22T10:01:00Z",
            "last_smoke_elapsed_ms": 300.0,
        },
        "utility": {
            "enabled": True,
            "ready": True,
            "loaded": True,
            "last_verified_at": "2026-04-22T10:02:00Z",
            "last_checked_at": "2026-04-22T10:02:00Z",
            "last_smoke_elapsed_ms": 250.0,
        },
    })

    assert summary["ready"] is True
    assert summary["loaded"] is True
    assert summary["status"] == "ok"
    assert summary["last_verified_at"] == "2026-04-22T10:02:00Z"
    assert summary["last_smoke_elapsed_ms"] == 300.0


def test_fast_runtime_status_command_formats_health_snapshot(monkeypatch):
    import gateway

    monkeypatch.setattr(
        gateway,
        "health_check",
        lambda: {
            "components": {
                "gateway": "ok",
                "orchestrator": "ok",
                "local_llm": "ok",
                "memory": "ok",
                "sentry": "ok",
            },
            "local_llm": {
                "roles": {
                    "scrub_region_finder": {"enabled": True, "ready": True},
                    "utility": {"enabled": True, "ready": True},
                }
            },
            "uptime_seconds": 12.3,
        },
    )

    response = gateway._try_handle_fast_runtime_command(" status ")

    assert response is not None
    assert "Runtime Status" in response
    assert "Local model lane: ok (2/2 roles ready)" in response
    assert "Uptime: 12.3s" in response


def test_operational_report_command_uses_concrete_read_only_checks(monkeypatch):
    import gateway

    monkeypatch.setattr(
        gateway,
        "health_check",
        lambda: {
            "components": {
                "gateway": "ok",
                "orchestrator": "ok",
                "local_llm": "ok",
                "memory": "ok",
                "sentry": "ok",
            },
            "local_llm": {
                "roles": {
                    "scrub_region_finder": {
                        "enabled": True,
                        "ready": True,
                        "status": "ok",
                        "last_smoke_elapsed_ms": 210.0,
                    },
                    "utility": {
                        "enabled": True,
                        "ready": True,
                        "status": "ok",
                        "last_smoke_elapsed_ms": 260.0,
                    },
                }
            },
            "uptime_seconds": 44.5,
        },
    )
    monkeypatch.setattr(
        gateway,
        "scheduler_service",
        SimpleNamespace(
            last_scheduler_tick_at="2026-04-22T14:00:00+00:00",
            list_jobs=lambda: [
                SimpleNamespace(
                    id="health_sweep",
                    enabled=True,
                    trigger_type="interval",
                    trigger_value="60",
                    last_run_at="2026-04-22T13:59:00+00:00",
                    last_run_status="triggered",
                ),
                SimpleNamespace(
                    id="ticket_sentinel_sync",
                    enabled=False,
                    trigger_type="interval",
                    trigger_value="300",
                    last_run_at=None,
                    last_run_status=None,
                ),
            ],
        ),
    )

    response = gateway._try_handle_fast_runtime_command(
        "Please produce a read-only operational smoke report for this Lancelot instance."
    )

    assert response is not None
    assert "Operational Smoke Report" in response
    assert "Gateway health snapshot via internal `/health` handler" in response
    assert "Local model lane: ok (2/2 roles ready)" in response
    assert "Scheduler: running (2 jobs, 1 enabled" in response
    assert "health_sweep: enabled, trigger=interval:60" in response
    assert "Degraded conditions: none visible from these checks" in response
    assert "Operator action required: none from these checks" in response
    assert "no repository writes, deployments, or external messages" in response


def test_operational_report_categorizes_expected_capability_notices(monkeypatch):
    import gateway

    monkeypatch.setattr(gateway._ff, "FEATURE_TOOLS_HOST_EXECUTION", True)
    monkeypatch.setattr(gateway._ff, "FEATURE_TOOLS_HOST_BRIDGE", False)
    monkeypatch.setattr(gateway._ff, "FEATURE_HOST_WRITE_COMMANDS", False)
    monkeypatch.setattr(gateway._ff, "FEATURE_TOOLS_UAB", True)
    monkeypatch.setattr(gateway._ff, "FEATURE_HIVE_UAB", False)

    notices = gateway._operator_notice_snapshot([])

    assert notices["action_required"] == []
    assert any("Host execution provider is enabled" in notice for notice in notices["expected"])
    assert any("UAB desktop bridge is enabled" in notice for notice in notices["expected"])


def test_operational_report_categorizes_degraded_conditions_as_action_required(monkeypatch):
    import gateway

    notices = gateway._operator_notice_snapshot(["local_model_role:utility=timeout"])

    assert notices["action_required"] == [
        "local_model_role:utility=timeout. Investigate before continuing customer-facing work."
    ]


def test_operational_report_uses_orchestrator_scheduler_fallback(monkeypatch):
    import gateway

    monkeypatch.setattr(gateway, "scheduler_service", None)
    monkeypatch.setattr(
        gateway.main_orchestrator,
        "scheduler_service",
        SimpleNamespace(
            last_scheduler_tick_at="2026-04-22T14:00:00+00:00",
            list_jobs=lambda: [],
        ),
        raising=False,
    )

    snapshot = gateway._scheduler_report_snapshot()

    assert snapshot["status"] == "running"
    assert snapshot["last_tick"] == "2026-04-22T14:00:00+00:00"


def test_operational_report_fast_path_emits_receipt(monkeypatch):
    import gateway

    captured = []
    monkeypatch.setattr(
        gateway,
        "get_receipt_service",
        lambda *_args, **_kwargs: SimpleNamespace(create=lambda receipt: captured.append(receipt)),
    )
    monkeypatch.setattr(
        gateway,
        "health_check",
        lambda: {
            "components": {
                "gateway": "ok",
                "orchestrator": "ok",
                "local_llm": "ok",
                "memory": "ok",
                "sentry": "ok",
            },
            "local_llm": {"roles": {}},
            "uptime_seconds": 11.0,
        },
    )
    monkeypatch.setattr(
        gateway,
        "_scheduler_report_snapshot",
        lambda: {
            "status": "running",
            "last_tick": "2026-04-22T14:00:00+00:00",
            "job_count": 1,
            "enabled_count": 1,
            "jobs": [],
            "degraded_conditions": [],
        },
    )

    response = gateway._try_handle_fast_runtime_command(
        "Please produce a read-only operational smoke report for this Lancelot instance.",
        quest_id="quest-fast-path",
        identity=SimpleNamespace(operator_id="op-1", session_id="sess-1"),
        channel="warroom",
    )

    assert response is not None
    assert len(captured) == 1
    receipt = captured[0]
    assert receipt.action_type == "verification"
    assert receipt.action_name == "operational_smoke_report"
    assert receipt.quest_id == "quest-fast-path"
    assert receipt.operator_id == "op-1"
    assert receipt.session_id == "sess-1"
    assert receipt.metadata["fast_runtime_command"] is True
    assert receipt.outputs["result"] == "ok"


def test_fast_runtime_status_command_requires_exact_runtime_command():
    import gateway

    assert gateway._try_handle_fast_runtime_command("what is the project status?") is None


def test_chat_run_payload_includes_receipt_proof_from_retry_lineage(tmp_path, monkeypatch):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    monkeypatch.setattr(gateway, "chat_run_store", store)
    try:
        blocked = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="write the governed files",
        )
        store.complete(
            blocked.run_id,
            status="blocked",
            response="Approval required before continuing.",
        )

        retried = store.create_retry(
            blocked.run_id,
            request_id="req-2",
            session_id="sess-1",
            operator_id="op-1",
        )
        assert retried is not None
        store.complete(
            retried.run_id,
            status="succeeded",
            response="Done",
        )

        receipts_by_quest = {
            blocked.run_id: [
                SimpleNamespace(
                    id="r-1",
                    action_type="tool_call",
                    action_name="repo_writer",
                    status="pending",
                    metadata={"tool_name": "repo_writer", "approval_id": "approval-1"},
                    outputs={},
                    error_message=None,
                ),
                SimpleNamespace(
                    id="r-2",
                    action_type="action_card_resolved",
                    action_name="actioncard.governance.approve",
                    status="success",
                    metadata={},
                    outputs={"status": "approved"},
                    error_message=None,
                ),
            ],
            retried.run_id: [
                SimpleNamespace(
                    id="r-3",
                    action_type="tool_call",
                    action_name="repo_writer",
                    status="success",
                    metadata={"tool_name": "repo_writer"},
                    outputs={"path": "/workspace/group_one.txt"},
                    error_message=None,
                ),
                SimpleNamespace(
                    id="r-4",
                    action_type="verification",
                    action_name="pii_scrub_fallback",
                    status="success",
                    metadata={
                        "frontier_scrub_event": True,
                        "degraded_privacy": True,
                        "reason": "deterministic local scrub fallback used",
                    },
                    outputs={"fallback_used": True},
                    error_message=None,
                ),
            ],
        }

        monkeypatch.setattr(
            gateway,
            "get_receipt_service",
            lambda *_args, **_kwargs: SimpleNamespace(
                get_quest_receipts=lambda quest_id: list(receipts_by_quest.get(quest_id, []))
            ),
        )

        payload = gateway._chat_run_payload(store.get(retried.run_id))
        proof = payload["receipt_proof"]

        assert proof is not None
        assert proof["available"] is True
        assert proof["receipt_count"] == 4
        assert proof["linked_run_count"] == 2
        assert proof["governed_tools"] == ["repo_writer"]
        assert proof["approval_state"] == "used"
        assert proof["degraded_mode"] == "used"
        assert proof["degraded_reasons"] == ["deterministic local scrub fallback used"]
        assert proof["outcome"] == "succeeded"
    finally:
        store.close()


def test_chat_run_payload_omits_receipt_proof_for_active_runs(tmp_path):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="still running",
        )
        payload = gateway._chat_run_payload(run)
        assert payload["receipt_proof"] is None
    finally:
        store.close()


def test_chat_async_endpoint_queues_run_without_waiting_for_result(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )

    async def fake_execute_async_chat_run(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "verify_token", lambda _request: True)
    monkeypatch.setattr(gateway.rate_limiter, "check", lambda _ip: True)
    monkeypatch.setattr(gateway.rate_limiter, "check", lambda _ip: True)
    monkeypatch.setattr(gateway, "_execute_async_chat_run", fake_execute_async_chat_run)
    monkeypatch.setattr(gateway, "_emit_chat_run_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)

    try:
        client = TestClient(gateway.app)
        response = client.post(
            "/chat/async",
            json={"text": "continue the plan", "user": "Myles", "channel": "warroom"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["status"] == "queued"
        assert payload["run"]["session_id"] == "sess-1"
        assert payload["run"]["operator_id"] == "op-1"
        assert store.get(payload["run_id"]) is not None
        work_item = gateway_work_ledger.get_work(payload["run_id"])
        assert work_item is not None
        assert work_item.status == "active"
        assert work_item.objective == "continue the plan"
    finally:
        store.close()


def test_chat_run_cancel_endpoint_marks_run_cancelled(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    events = []
    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "verify_token", lambda _request: True)
    monkeypatch.setattr(gateway.rate_limiter, "check", lambda _ip: True)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status)),
    )
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="long running request",
        )
        store.mark_running(run.run_id)

        client = TestClient(gateway.app)
        response = client.post(
            f"/api/chat/runs/{run.run_id}/cancel",
            json={"reason": "Operator cancelled from test."},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["cancelled"] is True
        assert payload["run"]["status"] == "cancelled"
        assert payload["run"]["cancel_reason"] == "Operator cancelled from test."
        work_item = gateway_work_ledger.get_work(run.run_id)
        assert work_item is not None
        assert work_item.status == "cancelled"
        assert work_item.blocker == "Operator cancelled from test."
        assert events == [("chat.run_cancelled", "cancelled")]
    finally:
        store.close()


def test_chat_run_retry_endpoint_queues_new_run(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    events = []
    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )

    async def fake_execute_async_chat_run(*args, **kwargs):
        return None

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "verify_token", lambda _request: True)
    monkeypatch.setattr(gateway.rate_limiter, "check", lambda _ip: True)
    monkeypatch.setattr(gateway, "_execute_async_chat_run", fake_execute_async_chat_run)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status)),
    )
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="retryable governed command",
        )
        store.fail(run.run_id, "provider timed out")

        client = TestClient(gateway.app)
        response = client.post(f"/api/chat/runs/{run.run_id}/retry")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["run"]["status"] == "queued"
        assert payload["run"]["retry_of_run_id"] == run.run_id
        assert payload["run"]["retry_count"] == 1
        assert store.get(payload["run_id"]) is not None
        retry_item = gateway_work_ledger.get_work(payload["run_id"])
        assert retry_item is not None
        assert retry_item.status == "active"
        assert retry_item.metadata["retry_of_run_id"] == run.run_id
        assert events == [("chat.run_queued", "queued")]
    finally:
        store.close()


def test_chat_run_retry_endpoint_queues_blocked_run(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    events = []
    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )

    async def fake_execute_async_chat_run(*args, **kwargs):
        return None

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "verify_token", lambda _request: True)
    monkeypatch.setattr(gateway.rate_limiter, "check", lambda _ip: True)
    monkeypatch.setattr(gateway, "_execute_async_chat_run", fake_execute_async_chat_run)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status)),
    )
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)

    try:
        run = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="blocked governed command",
        )
        store.complete(
            run.run_id,
            status="blocked",
            response="Approval group ID: abc-123",
        )

        client = TestClient(gateway.app)
        response = client.post(f"/api/chat/runs/{run.run_id}/retry")

        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["run"]["status"] == "queued"
        assert payload["run"]["retry_of_run_id"] == run.run_id
        assert payload["run"]["retry_count"] == 1
        assert store.get(payload["run_id"]) is not None
        retry_item = gateway_work_ledger.get_work(payload["run_id"])
        assert retry_item is not None
        assert retry_item.status == "active"
        assert retry_item.metadata["retry_of_run_id"] == run.run_id
        assert events == [("chat.run_queued", "queued")]
    finally:
        store.close()


def test_work_api_lists_gets_and_checkpoints_active_work(monkeypatch, gateway_work_ledger):
    import gateway

    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )
    monkeypatch.setattr(gateway, "verify_token", lambda _request: True)
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)

    gateway_work_ledger.upsert_work(
        quest_id="quest-1",
        objective="continue the hardening pass",
        session_id="sess-1",
        operator_id="op-1",
        status="active",
        phase="execution",
        next_action="Run focused tests",
    )

    client = TestClient(gateway.app)
    active_response = client.get("/api/work/active")
    item_response = client.get("/api/work/quest-1")
    checkpoint_response = client.post(
        "/api/work/quest-1/checkpoint",
        json={"reason": "operator_pause"},
    )

    assert active_response.status_code == 200
    assert active_response.json()["items"][0]["quest_id"] == "quest-1"
    assert item_response.status_code == 200
    assert item_response.json()["item"]["next_action"] == "Run focused tests"
    assert checkpoint_response.status_code == 200
    assert checkpoint_response.json()["checkpoint"]["reason"] == "operator_pause"


def test_work_api_creates_quiet_phase_checkpoint_before_listing(monkeypatch, gateway_work_ledger):
    import gateway

    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )
    monkeypatch.setattr(gateway, "verify_token", lambda _request: True)
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)
    monkeypatch.setattr(gateway, "ACTIVE_WORK_QUIET_CHECKPOINT_AFTER_SECONDS", 0)

    gateway_work_ledger.upsert_work(
        quest_id="quest-quiet",
        objective="quiet work",
        session_id="sess-1",
        operator_id="op-1",
        status="active",
        phase="provider_call",
        next_action="Waiting on governed provider response",
    )

    client = TestClient(gateway.app)
    response = client.get("/api/work/active")

    assert response.status_code == 200
    checkpoints = gateway_work_ledger.list_checkpoints("quest-quiet")
    assert len(checkpoints) == 1
    assert checkpoints[0]["reason"] == "quiet_phase"


def test_work_resume_endpoint_queues_retry_from_ledger_item(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )
    events = []

    async def fake_execute_async_chat_run(*_args, **_kwargs):
        return None

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "verify_token", lambda _request: True)
    monkeypatch.setattr(gateway.rate_limiter, "check", lambda _ip: True)
    monkeypatch.setattr(gateway, "_execute_async_chat_run", fake_execute_async_chat_run)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status)),
    )
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)

    try:
        blocked = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="resume this blocked governed request",
        )
        blocked = store.complete(
            blocked.run_id,
            status="blocked",
            response="Approval group ID: abc-123",
        )
        gateway._sync_work_ledger_from_chat_run(blocked, event_type="chat_run_blocked")
        assert gateway_work_ledger.get_work(blocked.run_id) is not None

        client = TestClient(gateway.app)
        response = client.post(f"/api/work/{blocked.run_id}/resume")

        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["source_quest_id"] == blocked.run_id
        retry = store.get(payload["run_id"])
        assert retry is not None
        assert retry.retry_of_run_id == blocked.run_id
        assert gateway_work_ledger.get_work(payload["run_id"]) is not None
        assert events == [("chat.run_queued", "queued")]
    finally:
        store.close()


def test_work_ledger_sync_closes_source_when_retry_finishes(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    monkeypatch.setattr(gateway, "chat_run_store", store)
    try:
        blocked = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="sess-1",
            operator_id="op-1",
            message="write the governed artifact",
        )
        blocked = store.complete(
            blocked.run_id,
            status="blocked",
            response="Approval group ID: abc-123",
        )
        gateway._sync_work_ledger_from_chat_run(blocked, event_type="chat_run_blocked")
        assert gateway_work_ledger.get_work(blocked.run_id).status == "blocked"

        retry = store.create_retry(
            blocked.run_id,
            request_id="req-2",
            session_id="sess-1",
            operator_id="op-1",
        )
        retry = store.complete(retry.run_id, status="succeeded", response="done")
        gateway._sync_work_ledger_from_chat_run(retry, event_type="chat_run_completed")

        source_item = gateway_work_ledger.get_work(blocked.run_id)
        assert source_item is not None
        assert source_item.status == "completed"
        assert source_item.phase == "superseded"
        assert source_item.metadata["superseded_by_retry_run_id"] == retry.run_id
        assert gateway_work_ledger.list_work(session_id="sess-1") == []
    finally:
        store.close()


def test_work_archive_endpoint_cancels_and_hides_blocked_work(tmp_path, monkeypatch, gateway_work_ledger):
    import gateway

    store = ChatRunStore(str(tmp_path / "runs.sqlite"))
    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )
    events = []
    archived_cards = []
    linked_card = SimpleNamespace(
        card_id="card-1",
        title="Approve stale work",
        source_system="governance",
    )

    monkeypatch.setattr(gateway, "chat_run_store", store)
    monkeypatch.setattr(gateway, "_require_request_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        gateway,
        "_emit_chat_run_event",
        lambda event_type, run, **_extra: events.append((event_type, run.status)),
    )
    monkeypatch.setattr(
        gateway.app.state,
        "actioncard_store",
        SimpleNamespace(list_pending_by_quest=lambda quest_id, limit=50: [linked_card]),
        raising=False,
    )
    monkeypatch.setattr(
        gateway.app.state,
        "actioncard_resolver",
        SimpleNamespace(
            archive=lambda *args, **kwargs: (
                archived_cards.append((args, kwargs))
                or {"status": "archived", "message": "archived"}
            )
        ),
        raising=False,
    )
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)

    try:
        blocked = store.create(
            request_id="req-1",
            user="Myles",
            channel="warroom",
            session_id="old-sess",
            operator_id="op-1",
            message="blocked governed command",
        )
        blocked = store.complete(
            blocked.run_id,
            status="blocked",
            response="Approval group ID: abc-123",
        )
        gateway._sync_work_ledger_from_chat_run(blocked, event_type="chat_run_blocked")

        client = TestClient(gateway.app)
        response = client.post(
            f"/api/work/{blocked.run_id}/archive",
            json={"reason": "Operator cleared stale blocked work after retry succeeded."},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["archived"] is True
        assert payload["item"]["status"] == "cancelled"
        assert payload["item"]["phase"] == "archived"
        assert payload["archived_actioncards"] == [
            {
                "card_id": "card-1",
                "title": "Approve stale work",
                "source_system": "governance",
            }
        ]
        assert archived_cards[0][0] == ("card-1",)
        assert archived_cards[0][1]["channel"] == "work_archive"
        assert gateway_work_ledger.list_work(session_id="old-sess") == []
        cancelled = store.get(blocked.run_id)
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        assert events == [("chat.run_cancelled", "cancelled")]
    finally:
        store.close()


def test_operator_smoke_endpoint_runs_against_live_gateway(monkeypatch):
    import gateway

    captured_receipts = []
    identity = SimpleNamespace(
        display_name="Myles",
        operator_id="op-1",
        session_id="sess-1",
    )
    monkeypatch.setattr(gateway, "_gateway_started", True)
    monkeypatch.setattr(gateway, "_require_request_capability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.core.auth_api.resolve_authenticated_identity", lambda _request: identity)
    monkeypatch.setattr(
        gateway,
        "get_receipt_service",
        lambda *_args, **_kwargs: SimpleNamespace(create=lambda receipt: captured_receipts.append(receipt)),
    )
    monkeypatch.setattr(
        gateway,
        "health_check",
        lambda: {
            "components": {
                "gateway": "ok",
                "orchestrator": "ok",
                "local_llm": "ok",
                "memory": "ok",
                "sentry": "ok",
            },
            "local_llm": {
                "roles": {
                    "scrub_region_finder": {"enabled": True, "ready": True, "status": "ok"},
                    "utility": {"enabled": True, "ready": True, "status": "ok"},
                }
            },
            "uptime_seconds": 30.0,
        },
    )
    monkeypatch.setattr(
        gateway,
        "_scheduler_report_snapshot",
        lambda: {
            "status": "running",
            "last_tick": "2026-04-22T14:00:00+00:00",
            "job_count": 1,
            "enabled_count": 1,
            "jobs": [],
            "degraded_conditions": [],
        },
    )
    monkeypatch.setattr(
        gateway,
        "_active_work_report_snapshot",
        lambda: {"status": "ok", "count": 0, "items": [], "degraded_conditions": []},
    )

    client = TestClient(gateway.app)
    response = client.get("/api/operator/smoke")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["source"] == "live_gateway"
    assert "Diagnostic source: live gateway process" in payload["report"]
    assert "Write safety: no repository writes" in payload["report"]
    assert captured_receipts
