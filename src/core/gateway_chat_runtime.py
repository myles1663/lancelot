"""Chat and operator-runtime helpers for the FastAPI gateway composition root."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Request
from chat_runs import ChatRun
from work_ledger import WorkItem
from gateway_receipt_proof import chat_run_payload as build_chat_run_payload
import gateway_chat_commands as _chat_commands
import gateway_chat_work_ledger as _chat_work_ledger
from shared.receipts import get_receipt_service

_RUNTIME_HELPER_NAMES: set[str] = set()
_RUNTIME_IMPLEMENTATIONS: dict[str, object] = {}


def bind_gateway_globals(**kwargs) -> None:
    """Bind gateway-owned services while preserving runtime implementations.

    Patched private helpers are passed here by `gateway.py`; otherwise the
    implementation in this module remains active for helper-to-helper calls.
    """
    for name in _RUNTIME_HELPER_NAMES:
        if name in kwargs:
            continue
        implementation = _RUNTIME_IMPLEMENTATIONS.get(name)
        if implementation is not None:
            globals()[name] = implementation
    globals().update(kwargs)
    _chat_commands.bind_gateway_globals(**kwargs)
    _chat_work_ledger.bind_gateway_globals(**kwargs)


async def _run_orchestrator_chat(*args, **kwargs) -> str:
    """Run the synchronous orchestrator turn off the FastAPI event loop.

    The orchestrator keeps per-turn state on the instance, so chat turns remain
    serialized. The important part is that WebSocket progress events can flush
    while the worker thread is waiting on governance/model/tool work.
    """
    def _invoke() -> str:
        with _orchestrator_chat_lock:
            return main_orchestrator.chat(*args, **kwargs)

    return await asyncio.to_thread(_invoke)


def _refresh_runtime_soul_from_store():
    """Reload the active Soul from store and apply it to live runtime subscribers."""
    from soul.store import load_active_soul
    from soul.layers import load_overlays, merge_soul

    active_soul = load_active_soul()
    if active_soul is None:
        raise RuntimeError("No active Soul found for runtime refresh")

    overlays = load_overlays()
    if overlays:
        active_soul = merge_soul(active_soul, overlays)

    apply_runtime_soul = getattr(app.state, "apply_runtime_soul", None)
    if callable(apply_runtime_soul):
        apply_runtime_soul(active_soul)
    else:
        main_orchestrator.soul = active_soul
        app.state.active_soul = active_soul

    return active_soul


def _transition_crusader_mode(action: str) -> tuple[bool, str]:
    """Apply a Crusader mode transition and refresh live runtime Soul subscribers."""
    if action == "activate":
        response_text = crusader_mode.activate()
        rollback = crusader_mode.deactivate
        failure_prefix = "Crusader activation"
    elif action == "deactivate":
        response_text = crusader_mode.deactivate()
        rollback = crusader_mode.activate
        failure_prefix = "Crusader deactivation"
    else:
        raise ValueError(f"Unsupported Crusader action: {action}")

    try:
        _refresh_runtime_soul_from_store()
        return True, response_text
    except Exception as exc:
        logger.error("%s runtime Soul refresh failed: %s", failure_prefix, exc)
        try:
            rollback()
            _refresh_runtime_soul_from_store()
        except Exception as rollback_exc:
            logger.error("%s rollback failed: %s", failure_prefix, rollback_exc)
        return False, f"{failure_prefix} failed to refresh runtime Soul: {exc}"


def _chat_run_payload(run: ChatRun) -> dict[str, Any]:
    return build_chat_run_payload(run, chat_run_store, logger, get_receipt_service)

async def _optional_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _can_access_chat_run(run: ChatRun, identity) -> bool:
    return _chat_work_ledger._can_access_chat_run(run, identity)

def _can_access_work_item(item: WorkItem, identity) -> bool:
    return _chat_work_ledger._can_access_work_item(item, identity)


def _sync_work_ledger_from_chat_run(
    run: ChatRun | None,
    *,
    event_type: str = "chat_run_updated",
    metadata: dict[str, Any] | None = None,
) -> WorkItem | None:
    return _chat_work_ledger._sync_work_ledger_from_chat_run(
        run,
        event_type=event_type,
        metadata=metadata,
    )


def _close_superseded_retry_source(run: ChatRun | None) -> WorkItem | None:
    return _chat_work_ledger._close_superseded_retry_source(run)


def _reconcile_superseded_retry_work(*, limit: int = 200) -> int:
    return _chat_work_ledger._reconcile_superseded_retry_work(limit=limit)


def _archive_pending_actioncards_for_work(
    quest_id: str,
    *,
    identity,
    reason: str,
) -> list[dict[str, Any]]:
    return _chat_work_ledger._archive_pending_actioncards_for_work(
        quest_id,
        identity=identity,
        reason=reason,
    )


def _emit_chat_run_event(event_type: str, run: ChatRun, **extra: Any) -> None:
    return _chat_work_ledger._emit_chat_run_event(event_type, run, **extra)


async def _record_chat_progress_event(event) -> None:
    return await _chat_work_ledger._record_chat_progress_event(event)


def _record_persisted_chat_progress(
    run_id: str,
    *,
    phase: str,
    message: str,
    event_timestamp: float | None = None,
    severity: str | None = None,
    degraded: bool | None = None,
    degraded_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ChatRun | None:
    return _chat_work_ledger._record_persisted_chat_progress(
        run_id,
        phase=phase,
        message=message,
        event_timestamp=event_timestamp,
        severity=severity,
        degraded=degraded,
        degraded_reason=degraded_reason,
        metadata=metadata,
    )


def _classify_chat_run_status(response_text: str) -> str:
    return _chat_work_ledger._classify_chat_run_status(response_text)

_FAST_RUNTIME_STATUS_COMMANDS = _chat_commands._FAST_RUNTIME_STATUS_COMMANDS
_OPERATIONAL_REPORT_TRIGGERS = _chat_commands._OPERATIONAL_REPORT_TRIGGERS
_OPERATOR_WORK_STATUS_TRIGGERS = _chat_commands._OPERATOR_WORK_STATUS_TRIGGERS
_OPERATOR_CONTINUATION_STATUS_TRIGGERS = _chat_commands._OPERATOR_CONTINUATION_STATUS_TRIGGERS
_OPERATOR_STATUS_TERMS = _chat_commands._OPERATOR_STATUS_TERMS


def _normalized_command_text(message: str) -> str:
    return _chat_commands._normalized_command_text(message)


def _is_fast_runtime_command(message: str) -> bool:
    return _chat_commands._is_fast_runtime_command(message)


def _is_operator_work_status_command(normalized: str) -> bool:
    return _chat_commands._is_operator_work_status_command(normalized)


def _preview_text(value: str, limit: int = 500) -> str:
    return _chat_commands._preview_text(value, limit=limit)


def _emit_fast_runtime_receipt(
    *,
    action_name: str,
    message: str,
    response_text: str,
    checks: list[str],
    degraded_conditions: list[str],
    result: str,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> None:
    return _chat_commands._emit_fast_runtime_receipt(
        action_name=action_name,
        message=message,
        response_text=response_text,
        checks=checks,
        degraded_conditions=degraded_conditions,
        result=result,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )


def _try_handle_fast_runtime_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    return _chat_commands._try_handle_fast_runtime_command(
        message,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )


def _try_handle_operator_work_status_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str:
    return _chat_commands._try_handle_operator_work_status_command(
        message,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )


def _try_handle_operational_report_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    return _chat_commands._try_handle_operational_report_command(
        message,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )


def _operator_notice_snapshot(degraded: list[str]) -> dict[str, list[str]]:
    return _chat_commands._operator_notice_snapshot(degraded)


def _active_work_report_snapshot(
    *,
    exclude_quest_id: str = "",
    session_id: str = "",
    operator_id: str = "",
) -> dict[str, Any]:
    return _chat_commands._active_work_report_snapshot(
        exclude_quest_id=exclude_quest_id,
        session_id=session_id,
        operator_id=operator_id,
    )


def _scheduler_report_snapshot() -> dict[str, Any]:
    return _chat_commands._scheduler_report_snapshot()


def _job_field(job: Any, name: str, default: Any = None) -> Any:
    return _chat_commands._job_field(job, name, default)

async def _execute_chat_turn(
    message: str,
    *,
    user: str,
    channel: str,
    identity,
    attachments=None,
    quest_id: str | None = None,
) -> str:
    """Execute the existing chat semantics for sync and async callers."""
    onboarding_orch.state = onboarding_orch.determine_state()

    if onboarding_orch.state != "READY":
        return onboarding_orch.process(user, message)

    fast_response = _try_handle_fast_runtime_command(
        message,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    if fast_response is not None:
        return fast_response

    is_trigger, action = crusader_mode.should_intercept(message)
    if is_trigger:
        if action == "activate":
            ok, response_text = _transition_crusader_mode("activate")
            if ok:
                main_orchestrator.audit_logger.log_event(
                    "CRUSADER_MODE_ACTIVATED",
                    "User activated Crusader Mode",
                    user,
                )
            return response_text

        ok, response_text = _transition_crusader_mode("deactivate")
        if ok:
            main_orchestrator.audit_logger.log_event(
                "CRUSADER_MODE_DEACTIVATED",
                "User deactivated Crusader Mode",
                user,
            )
        return response_text

    operator_name = getattr(identity, "display_name", "") or user
    operator_id = getattr(identity, "operator_id", "")
    session_id = getattr(identity, "session_id", "")

    if crusader_mode.is_active:
        if crusader_adapter.check_auto_pause(message):
            main_orchestrator.audit_logger.log_event(
                "CRUSADER_AUTO_PAUSE",
                f"Blocked: {message}",
                user,
            )
            return (
                "Authority required.\n"
                "This operation is restricted even in Crusader Mode."
            )

        response_text = await _run_orchestrator_chat(
            message,
            crusader_mode=True,
            attachments=attachments,
            channel=channel,
            session_id=session_id,
            operator_id=operator_id,
            operator_name=operator_name,
            quest_id=quest_id,
        )
        return crusader_adapter.format_response(response_text)

    return await _run_orchestrator_chat(
        message,
        attachments=attachments,
        channel=channel,
        session_id=session_id,
        operator_id=operator_id,
        operator_name=operator_name,
        quest_id=quest_id,
    )


async def _execute_async_chat_run(
    run_id: str,
    *,
    message: str,
    user: str,
    channel: str,
    identity,
) -> None:
    async def _execute_in_worker_slot() -> None:
        run = chat_run_store.mark_running(run_id)
        if run is None:
            logger.warning("Async chat run %s disappeared before execution started.", run_id)
            return
        if run.status == "cancelled":
            logger.info("Async chat run %s was cancelled before execution started.", run_id)
            return
        if run.status != "running":
            logger.info(
                "Async chat run %s is no longer queued for execution (status=%s).",
                run_id,
                run.status,
            )
            return

        _sync_work_ledger_from_chat_run(run, event_type="chat_run_started")
        _emit_chat_run_event("chat.run_started", run)
        _record_persisted_chat_progress(
            run_id,
            phase="execution",
            message="Worker slot acquired; starting governed chat turn.",
            metadata={"wait_reason": "execution_start"},
        )

        try:
            response_text = await _execute_chat_turn(
                message,
                user=user,
                channel=channel,
                identity=identity,
                quest_id=run_id,
            )
            current = chat_run_store.get(run_id)
            if current is not None and current.status == "cancelled":
                logger.info("Async chat run %s finished after operator cancellation; preserving cancelled state.", run_id)
                _sync_work_ledger_from_chat_run(current, event_type="chat_run_cancelled")
                return

            status = _classify_chat_run_status(response_text)
            if status == "blocked":
                _record_persisted_chat_progress(
                    run_id,
                    phase="approval",
                    message="Waiting for Commander approval.",
                    metadata={"wait_reason": "approval"},
                )
            else:
                _record_persisted_chat_progress(
                    run_id,
                    phase="finalization",
                    message="Finalizing response and execution proof.",
                    metadata={"wait_reason": "finalization"},
                )

            run = chat_run_store.complete(
                run_id,
                status=status,
                response=response_text,
                crusader_mode=crusader_mode.is_active,
            )
            if run is not None:
                event_name = "chat.run_blocked" if status == "blocked" else (
                    "chat.run_failed" if status == "failed" else "chat.run_completed"
                )
                _sync_work_ledger_from_chat_run(run, event_type=event_name.replace(".", "_"))
                _emit_chat_run_event(event_name, run)
        except Exception as exc:
            logger.exception("Async chat run failed: %s", run_id)
            run = chat_run_store.fail(run_id, str(exc))
            if run is not None:
                _sync_work_ledger_from_chat_run(run, event_type="chat_run_failed")
                _emit_chat_run_event("chat.run_failed", run)

    if _is_fast_runtime_command(message):
        await _execute_in_worker_slot()
        return

    queued = _record_persisted_chat_progress(
        run_id,
        phase="waiting_worker_slot",
        message="Waiting for governed execution worker slot.",
        metadata={"wait_reason": "worker_slot"},
    )
    if queued is None:
        logger.warning("Async chat run %s disappeared before worker-slot wait was recorded.", run_id)
        return
    if queued.status == "cancelled":
        logger.info("Async chat run %s was cancelled before worker-slot acquisition.", run_id)
        return

    async with _get_async_chat_worker_slot():
        current = chat_run_store.get(run_id)
        if current is not None and current.status == "cancelled":
            logger.info("Async chat run %s was cancelled while waiting for worker slot.", run_id)
            _sync_work_ledger_from_chat_run(current, event_type="chat_run_cancelled")
            return
        await _execute_in_worker_slot()


_RUNTIME_HELPER_NAMES = {
    name
    for name, value in globals().items()
    if name.startswith("_") and callable(value) and name not in {"_ff"}
}
_RUNTIME_IMPLEMENTATIONS = {name: globals()[name] for name in _RUNTIME_HELPER_NAMES}
