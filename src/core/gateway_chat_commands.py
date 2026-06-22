"""Fast operator command helpers for gateway chat runtime."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

import feature_flags as _ff
from gateway_runtime_reports import (
    active_work_report_snapshot as build_active_work_report_snapshot,
    handle_operational_report_command,
    job_field as runtime_report_job_field,
    operator_notice_snapshot as build_operator_notice_snapshot,
    scheduler_report_snapshot as build_scheduler_report_snapshot,
)
from shared.receipts import (
    ActionType,
    CognitionTier,
    create_finalized_receipt,
    get_receipt_service,
)

_COMMAND_HELPER_NAMES: set[str] = set()
_COMMAND_IMPLEMENTATIONS: dict[str, object] = {}


def bind_gateway_globals(**kwargs) -> None:
    for name in _COMMAND_HELPER_NAMES:
        if name in kwargs:
            continue
        implementation = _COMMAND_IMPLEMENTATIONS.get(name)
        if implementation is not None:
            globals()[name] = implementation
    globals().update(kwargs)


_FAST_RUNTIME_STATUS_COMMANDS = {
    "status",
    "/status",
    "system status",
    "runtime status",
    "health",
    "health check",
}
_OPERATIONAL_REPORT_TRIGGERS = (
    "operational smoke report",
    "operator acceptance smoke test",
    "operator acceptance test",
    "operational report",
    "runtime health report",
    "system health report",
    "runtime health and active work status",
    "active work status",
    "health report for this lancelot instance",
)
_OPERATOR_WORK_STATUS_TRIGGERS = (
    "active work status",
    "active-work status",
    "current active work",
    "active-work state",
    "active work state",
)
_OPERATOR_CONTINUATION_STATUS_TRIGGERS = (
    "continue with the plan",
    "continue the plan",
    "keep going with the plan",
    "resume the plan",
)
_OPERATOR_STATUS_TERMS = (
    "status",
    "what's next",
    "whats next",
    "what is next",
    "next practical step",
    "where do we stand",
    "what is left",
    "whats left",
)


def _normalized_command_text(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def _is_fast_runtime_command(message: str) -> bool:
    normalized = _normalized_command_text(message)
    if normalized in _FAST_RUNTIME_STATUS_COMMANDS:
        return True
    if _is_operator_work_status_command(normalized):
        return True
    return (
        any(trigger in normalized for trigger in _OPERATIONAL_REPORT_TRIGGERS)
        and any(term in normalized for term in ("read-only", "health", "operational", "smoke"))
    )


def _is_operator_work_status_command(normalized: str) -> bool:
    if any(trigger in normalized for trigger in _OPERATOR_WORK_STATUS_TRIGGERS):
        return True
    return (
        any(trigger in normalized for trigger in _OPERATOR_CONTINUATION_STATUS_TRIGGERS)
        and any(term in normalized for term in _OPERATOR_STATUS_TERMS)
    )


def _preview_text(value: str, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


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
    try:
        receipt_service = get_receipt_service("/home/lancelot/data")
        operator_id = getattr(identity, "operator_id", None) if identity is not None else None
        session_id = getattr(identity, "session_id", None) if identity is not None else None
        receipt = create_finalized_receipt(
            ActionType.VERIFICATION,
            action_name,
            {
                "message": _preview_text(message, limit=240),
                "checks": list(checks),
            },
            outputs={
                "result": result,
                "degraded_conditions": list(degraded_conditions),
                "response_preview": _preview_text(response_text, limit=600),
            },
            tier=CognitionTier.DETERMINISTIC,
            quest_id=quest_id,
            metadata={
                "fast_runtime_command": True,
                "channel": channel,
                "degraded": bool(degraded_conditions),
            },
            operator_id=operator_id,
            session_id=session_id,
            duration_ms=0,
        )
        receipt_service.create(receipt)
    except Exception as exc:
        logger.warning("Failed to emit fast runtime receipt %s for quest %s: %s", action_name, quest_id, exc)


def _try_handle_fast_runtime_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    """Handle exact low-risk runtime commands without a model/tool loop."""
    normalized = _normalized_command_text(message)
    if _is_operator_work_status_command(normalized):
        return _try_handle_operator_work_status_command(
            message,
            quest_id=quest_id,
            identity=identity,
            channel=channel,
        )
    if normalized not in _FAST_RUNTIME_STATUS_COMMANDS:
        return _try_handle_operational_report_command(
            message,
            quest_id=quest_id,
            identity=identity,
            channel=channel,
        )

    snapshot = health_check()
    if isinstance(snapshot, JSONResponse):
        response_text = "Runtime status unavailable. Check `/health` and gateway logs."
        _emit_fast_runtime_receipt(
            action_name="runtime_status_report",
            message=message,
            response_text=response_text,
            checks=["internal_health_snapshot"],
            degraded_conditions=["gateway health unavailable"],
            result="degraded",
            quest_id=quest_id,
            identity=identity,
            channel=channel,
        )
        return response_text
    components = snapshot.get("components", {})
    local_llm = snapshot.get("local_llm", {})
    roles = local_llm.get("roles", {}) if isinstance(local_llm, dict) else {}
    enabled_roles = [
        payload for payload in roles.values()
        if isinstance(payload, dict) and payload.get("enabled", True)
    ]
    ready_roles = [payload for payload in enabled_roles if payload.get("ready")]
    role_summary = (
        f"{len(ready_roles)}/{len(enabled_roles)} roles ready"
        if enabled_roles else "no role-specific endpoints configured"
    )
    degraded_conditions = [
        f"{component}={status}"
        for component, status in sorted(components.items())
        if status not in {"ok", "disabled"}
    ]
    degraded_conditions.extend(
        f"local_model_role:{name}={payload.get('status') or 'not ready'}"
        for name, payload in sorted(roles.items())
        if isinstance(payload, dict) and payload.get("enabled", True) and not payload.get("ready")
    )
    uptime = snapshot.get("uptime_seconds", 0)
    response_text = "\n".join([
        "**Runtime Status**",
        "---",
        f"Gateway: {components.get('gateway', 'unknown')}",
        f"Orchestrator: {components.get('orchestrator', 'unknown')}",
        f"Local model lane: {components.get('local_llm', 'unknown')} ({role_summary})",
        f"Memory: {components.get('memory', 'unknown')}",
        f"Sentry: {components.get('sentry', 'unknown')}",
        f"Uptime: {uptime}s",
    ])
    _emit_fast_runtime_receipt(
        action_name="runtime_status_report",
        message=message,
        response_text=response_text,
        checks=["internal_health_snapshot", "local_model_role_health"],
        degraded_conditions=degraded_conditions,
        result="ok" if not degraded_conditions else "degraded",
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    return response_text


def _try_handle_operator_work_status_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str:
    """Return durable operator work state without involving a model turn."""
    active_work = _active_work_report_snapshot(
        exclude_quest_id=quest_id or "",
        session_id=getattr(identity, "session_id", "") if identity is not None else "",
        operator_id=getattr(identity, "operator_id", "") if identity is not None else "",
    )
    scheduler = _scheduler_report_snapshot()
    degraded = list(active_work.get("degraded_conditions", []))
    degraded.extend(scheduler.get("degraded_conditions", []))

    failed_jobs = [
        job for job in scheduler.get("jobs", [])
        if str(job.get("last_run_status") or "").lower() == "failed"
    ]
    ticket_job = next(
        (job for job in scheduler.get("jobs", []) if job.get("id") == "ticket_sentinel_sync"),
        None,
    )

    lines = [
        "**Operator Work Status**",
        "---",
    ]
    items = active_work.get("items", [])
    if items:
        lines.append(f"Active work: {len(items)} open item(s).")
        lines.append("Open items:")
        for item in items[:5]:
            lines.append(
                f"- {item['quest_id']}: {item['status']}/{item['phase']} - {item['objective']}"
            )
            if item.get("blocker"):
                lines.append(f"  Blocked on: {item['blocker']}")
            if item.get("next_action"):
                lines.append(f"  Next: {item['next_action']}")
        if len(items) > 5:
            lines.append(f"- ... {len(items) - 5} additional items omitted")
        first = items[0]
        next_action = first.get("blocker") or first.get("next_action") or "review the first open item"
        lines.append(f"Next practical step: {next_action}.")
    else:
        lines.append("Active work: no retained active work is currently open for this operator.")
        lines.append(
            "Next practical step: start the next requested task, or name a blocked/failed run if you want it retried."
        )

    scheduler_status = scheduler.get("status", "unknown")
    lines.append(
        f"Scheduler: {scheduler_status} ({scheduler.get('job_count', 0)} jobs, "
        f"{scheduler.get('enabled_count', 0)} enabled)."
    )
    if failed_jobs:
        lines.append("Failed scheduler jobs:")
        for job in failed_jobs[:5]:
            error = f" - {_preview_text(job.get('last_run_error'), 180)}" if job.get("last_run_error") else ""
            lines.append(f"- {job['id']}: {job.get('last_run_status')}{error}")
    else:
        lines.append("Failed scheduler jobs: none.")
    if ticket_job:
        ticket_error = ticket_job.get("last_run_error")
        ticket_suffix = f", error={_preview_text(ticket_error, 180)}" if ticket_error else ""
        lines.append(
            f"Ticket Sentinel: last_status={ticket_job.get('last_run_status') or 'not run'}, "
            f"last_run={ticket_job.get('last_run_at') or 'never'}{ticket_suffix}."
        )

    if degraded:
        lines.append(f"Degraded conditions: {', '.join(degraded)}.")
    else:
        lines.append("Degraded conditions: none visible from work/scheduler state.")
    lines.append("Write safety: read-only status check; no files, deployments, or external messages were performed.")

    response_text = "\n".join(lines)
    _emit_fast_runtime_receipt(
        action_name="operator_work_status_report",
        message=message,
        response_text=response_text,
        checks=["active_work_ledger", "scheduler_service_state"],
        degraded_conditions=degraded,
        result="ok" if not degraded else "degraded",
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    return response_text


def _try_handle_operational_report_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    return handle_operational_report_command(
        message,
        normalized_command_text=_normalized_command_text,
        triggers=_OPERATIONAL_REPORT_TRIGGERS,
        health_snapshot=health_check,
        gateway_started=_gateway_started,
        operator_notice_snapshot_fn=_operator_notice_snapshot,
        active_work_report_snapshot_fn=_active_work_report_snapshot,
        scheduler_report_snapshot_fn=_scheduler_report_snapshot,
        emit_receipt=_emit_fast_runtime_receipt,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )


def _operator_notice_snapshot(degraded: list[str]) -> dict[str, list[str]]:
    return build_operator_notice_snapshot(degraded, _ff)


def _active_work_report_snapshot(
    *,
    exclude_quest_id: str = "",
    session_id: str = "",
    operator_id: str = "",
) -> dict[str, Any]:
    return build_active_work_report_snapshot(
        work_ledger_store,
        _preview_text,
        exclude_quest_id=exclude_quest_id,
        session_id=session_id,
        operator_id=operator_id,
    )


def _scheduler_report_snapshot() -> dict[str, Any]:
    return build_scheduler_report_snapshot(scheduler_service, main_orchestrator)


def _job_field(job: Any, name: str, default: Any = None) -> Any:
    return runtime_report_job_field(job, name, default)


_COMMAND_HELPER_NAMES = {
    name
    for name, value in globals().items()
    if name.startswith("_") and callable(value) and name not in {"_ff"}
}
_COMMAND_IMPLEMENTATIONS = {name: globals()[name] for name in _COMMAND_HELPER_NAMES}
