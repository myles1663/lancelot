"""Operator-facing runtime report snapshots for fast chat commands."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def _preview(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def operator_notice_snapshot(degraded: list[str], feature_flags) -> dict[str, list[str]]:
    action_required = [
        f"{condition}. Investigate before continuing customer-facing work."
        for condition in degraded
    ]
    expected: list[str] = []
    if getattr(feature_flags, "FEATURE_TOOLS_HOST_EXECUTION", False):
        expected.append(
            "Host execution provider is enabled for this local operator instance; commands run in container Linux."
        )
    if getattr(feature_flags, "FEATURE_TOOLS_HOST_BRIDGE", False):
        expected.append(
            "Host bridge provider is enabled; commands can cross from the container to the host agent."
        )
    if getattr(feature_flags, "FEATURE_HOST_WRITE_COMMANDS", False):
        expected.append(
            "Host write commands are enabled; keep this off for customer deployments unless explicitly required."
        )
    if getattr(feature_flags, "FEATURE_TOOLS_UAB", False) or getattr(feature_flags, "FEATURE_HIVE_UAB", False):
        expected.append(
            "UAB desktop bridge is enabled; verify the daemon before desktop-control workflows."
        )
    return {
        "action_required": action_required,
        "expected": expected,
    }


def active_work_report_snapshot(
    work_ledger_store,
    preview_text,
    *,
    exclude_quest_id: str = "",
    session_id: str = "",
    operator_id: str = "",
) -> dict[str, Any]:
    if work_ledger_store is None:
        return {
            "status": "unavailable",
            "count": 0,
            "items": [],
            "degraded_conditions": ["active work ledger is not initialized"],
        }

    try:
        items = work_ledger_store.list_work(
            session_id=session_id,
            operator_id=operator_id,
            include_terminal=False,
            limit=10,
        )
    except Exception as exc:
        return {
            "status": "error",
            "count": 0,
            "items": [],
            "degraded_conditions": [f"active work list failed: {exc}"],
        }

    payloads = []
    for item in items:
        quest_id = str(getattr(item, "quest_id", ""))
        if exclude_quest_id and quest_id == exclude_quest_id:
            continue
        payloads.append({
            "quest_id": quest_id[:80],
            "status": str(getattr(item, "status", ""))[:40],
            "phase": str(getattr(item, "phase", ""))[:40],
            "objective": preview_text(str(getattr(item, "objective", "")), limit=140),
            "next_action": preview_text(str(getattr(item, "next_action", "")), limit=140),
            "blocker": preview_text(str(getattr(item, "blocker", "")), limit=140),
        })

    return {
        "status": "ok",
        "count": len(payloads),
        "items": payloads,
        "degraded_conditions": [],
    }


def scheduler_report_snapshot(scheduler_service, main_orchestrator) -> dict[str, Any]:
    service = scheduler_service or getattr(main_orchestrator, "scheduler_service", None)
    if service is None:
        return {
            "status": "unavailable",
            "last_tick": None,
            "job_count": 0,
            "enabled_count": 0,
            "jobs": [],
            "degraded_conditions": ["scheduler service is not initialized"],
        }

    try:
        jobs = service.list_jobs()
    except Exception as exc:
        return {
            "status": "error",
            "last_tick": getattr(service, "last_scheduler_tick_at", None),
            "job_count": 0,
            "enabled_count": 0,
            "jobs": [],
            "degraded_conditions": [f"scheduler job list failed: {exc}"],
        }

    job_payloads = []
    degraded_conditions = []
    for job in jobs:
        job_id = str(job_field(job, "id", "unknown"))
        enabled = bool(job_field(job, "enabled", False))
        trigger_type = str(job_field(job, "trigger_type", "unknown"))
        trigger_value = str(job_field(job, "trigger_value", ""))
        last_status = job_field(job, "last_run_status", None)
        last_error = job_field(job, "last_run_error", None)
        if last_status and str(last_status).lower() not in {
            "ok",
            "success",
            "succeeded",
            "triggered",
            "not run",
        }:
            detail = f": {_preview(last_error)}" if last_error else ""
            degraded_conditions.append(f"job:{job_id} last_status={last_status}{detail}")
        job_payloads.append({
            "id": job_id,
            "enabled": enabled,
            "trigger": f"{trigger_type}:{trigger_value}" if trigger_value else trigger_type,
            "last_run_at": job_field(job, "last_run_at", None),
            "last_run_status": last_status,
            "last_run_error": last_error,
        })

    last_tick = getattr(service, "last_scheduler_tick_at", None)
    return {
        "status": "running" if last_tick else "initialized",
        "last_tick": last_tick,
        "job_count": len(job_payloads),
        "enabled_count": sum(1 for job in job_payloads if job["enabled"]),
        "jobs": job_payloads,
        "degraded_conditions": degraded_conditions,
    }


def handle_operational_report_command(
    message: str,
    *,
    normalized_command_text,
    triggers: tuple[str, ...],
    health_snapshot,
    gateway_started: bool,
    operator_notice_snapshot_fn,
    active_work_report_snapshot_fn,
    scheduler_report_snapshot_fn,
    emit_receipt,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    normalized = normalized_command_text(message)
    if not any(trigger in normalized for trigger in triggers):
        return None
    if not any(term in normalized for term in ("read-only", "health", "operational", "smoke")):
        return None

    snapshot = health_snapshot()
    if isinstance(snapshot, JSONResponse):
        diagnostic_source = (
            "standalone gateway import; use GET /api/operator/smoke for live gateway status"
            if not gateway_started
            else "live gateway process"
        )
        response_text = (
            "**Operational Smoke Report**\n"
            "---\n"
            "Result: degraded\n"
            f"Diagnostic source: {diagnostic_source}\n"
            "Checked: internal health snapshot\n"
            "Gateway health could not be assembled. Check `/health` and gateway logs.\n"
            "No repository writes, deployments, or external messages were performed."
        )
        emit_receipt(
            action_name="operational_smoke_report",
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

    components = snapshot.get("components", {}) if isinstance(snapshot, dict) else {}
    local_llm = snapshot.get("local_llm", {}) if isinstance(snapshot, dict) else {}
    roles = local_llm.get("roles", {}) if isinstance(local_llm, dict) else {}
    enabled_roles = [
        (name, payload)
        for name, payload in sorted((roles or {}).items())
        if isinstance(payload, dict) and payload.get("enabled", True)
    ]
    ready_roles = [
        name for name, payload in enabled_roles
        if payload.get("ready")
    ]
    scheduler = scheduler_report_snapshot_fn()
    active_work = active_work_report_snapshot_fn()

    degraded: list[str] = []
    for component, status in sorted(components.items()):
        if status not in {"ok", "disabled"}:
            degraded.append(f"{component}={status}")
    for name, payload in enabled_roles:
        if not payload.get("ready"):
            status = payload.get("status") or "not ready"
            degraded.append(f"local_model_role:{name}={status}")
    if scheduler["status"] not in {"running", "initialized"}:
        degraded.append(f"scheduler={scheduler['status']}")
    degraded.extend(scheduler["degraded_conditions"])
    degraded.extend(active_work["degraded_conditions"])

    result = "ok" if not degraded else "degraded"
    local_role_summary = (
        f"{len(ready_roles)}/{len(enabled_roles)} roles ready"
        if enabled_roles else "no role-specific endpoints configured"
    )
    scheduler_summary = (
        f"{scheduler['status']} "
        f"({scheduler['job_count']} jobs, {scheduler['enabled_count']} enabled, "
        f"last tick: {scheduler['last_tick'] or 'not observed'})"
    )

    lines = [
        "**Operational Smoke Report**",
        "---",
        f"Result: {result}",
        (
            "Diagnostic source: live gateway process"
            if gateway_started
            else "Diagnostic source: standalone gateway import; use GET /api/operator/smoke for live gateway status"
        ),
        "Checked:",
        "- Gateway health snapshot via internal `/health` handler",
        "- Local model role health from the role router status",
        "- Scheduler service state and registered job records",
        "- Active work ledger state",
        "- Visible degraded conditions from component, role, scheduler, and active-work state",
        "",
        "Findings:",
        f"- Gateway: {components.get('gateway', 'unknown')}",
        f"- Orchestrator: {components.get('orchestrator', 'unknown')}",
        f"- Memory: {components.get('memory', 'unknown')}",
        f"- Sentry: {components.get('sentry', 'unknown')}",
        f"- Local model lane: {components.get('local_llm', 'unknown')} ({local_role_summary})",
        f"- Scheduler: {scheduler_summary}",
        f"- Active work: {active_work['status']} ({active_work['count']} open item(s))",
    ]

    if enabled_roles:
        lines.append("- Local model roles:")
        for name, payload in enabled_roles:
            smoke_ms = payload.get("last_smoke_elapsed_ms")
            smoke_text = f", smoke {smoke_ms}ms" if smoke_ms is not None else ""
            error = payload.get("last_error")
            error_text = f", error: {error}" if error else ""
            status = payload.get("status") or ("ready" if payload.get("ready") else "unknown")
            lines.append(
                f"  - {name}: {status}, ready={bool(payload.get('ready'))}{smoke_text}{error_text}"
            )

    if scheduler["jobs"]:
        lines.append("- Scheduler jobs:")
        displayed_jobs = list(scheduler["jobs"][:8])
        displayed_ids = {job["id"] for job in displayed_jobs}
        failed_omitted_jobs = [
            job for job in scheduler["jobs"][8:]
            if str(job.get("last_run_status") or "").lower() == "failed"
        ]
        for job in failed_omitted_jobs:
            if job["id"] not in displayed_ids:
                displayed_jobs.append(job)
                displayed_ids.add(job["id"])

        for job in displayed_jobs:
            last_run = job["last_run_at"] or "never"
            last_status = job["last_run_status"] or "not run"
            enabled = "enabled" if job["enabled"] else "disabled"
            error = job.get("last_run_error")
            error_text = f", error={_preview(error)}" if error and str(last_status).lower() == "failed" else ""
            lines.append(
                f"  - {job['id']}: {enabled}, trigger={job['trigger']}, "
                f"last_run={last_run}, last_status={last_status}{error_text}"
            )
        omitted = len(scheduler["jobs"]) - len(displayed_ids)
        if omitted > 0:
            lines.append(f"  - ... {omitted} additional jobs omitted")

    if active_work["items"]:
        lines.append("- Active work items:")
        for item in active_work["items"][:5]:
            lines.append(
                f"  - {item['quest_id']}: {item['status']}/{item['phase']} - {item['objective']}"
            )
        if len(active_work["items"]) > 5:
            lines.append(f"  - ... {len(active_work['items']) - 5} additional items omitted")

    if degraded:
        lines.append(f"- Degraded conditions: {', '.join(degraded)}")
    else:
        lines.append("- Degraded conditions: none visible from these checks")

    notices = operator_notice_snapshot_fn(degraded)
    if notices["action_required"]:
        lines.append("- Operator action required:")
        for notice in notices["action_required"]:
            lines.append(f"  - {notice}")
    else:
        lines.append("- Operator action required: none from these checks")
    if notices["expected"]:
        lines.append("- Expected operator notices:")
        for notice in notices["expected"]:
            lines.append(f"  - {notice}")

    lines.append("- Write safety: no repository writes, deployments, or external messages were performed")
    uptime = snapshot.get("uptime_seconds", 0) if isinstance(snapshot, dict) else 0
    lines.append(f"- Uptime: {uptime}s")
    response_text = "\n".join(lines)
    emit_receipt(
        action_name="operational_smoke_report",
        message=message,
        response_text=response_text,
        checks=[
            "internal_health_snapshot",
            "local_model_role_health",
            "scheduler_service_state",
            "registered_scheduler_jobs",
            "active_work_ledger",
        ],
        degraded_conditions=degraded,
        result=result,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    return response_text


def job_field(job: Any, name: str, default: Any = None) -> Any:
    if isinstance(job, dict):
        return job.get(name, default)
    return getattr(job, name, default)
