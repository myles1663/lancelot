from __future__ import annotations

import logging as _logging
import time as _time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import feature_flags as _ff
from tool_loop_approval import (
    approval_context as _approval_context,
    approval_group_reason as _approval_group_reason,
    approval_reason as _approval_reason,
    pending_approval_response as _pending_approval_response,
    tool_input_error as _tool_input_error,
)
from tool_loop_governance import (
    record_tool_governance_event as _record_tool_governance_event,
)
from tool_loop_receipts import (
    persist_tool_call_receipt as _persist_tool_call_receipt,
)
from tool_loop_results import (
    normalize_tool_failure as _normalize_tool_failure,
    normalize_tool_success as _normalize_tool_success,
)

_gov_logger = _logging.getLogger("src.core.orchestrator")


@dataclass
class FrontierToolBatchResult:
    tool_results: list[tuple[str, str, str]]
    pending_response: str | None = None


def _emit_tool_progress(runtime: Any, phase: str, message: str, **metadata: Any) -> None:
    emitter = getattr(runtime, "_emit_chat_progress", None)
    if callable(emitter):
        emitter(phase, message, **metadata)


def collect_additional_approval_requests(
    runtime: Any,
    remaining_tool_calls: list[Any],
    declared_tool_names: set[str],
    allow_writes: bool,
) -> list[dict[str, Any]]:
    """Create pending Sentry requests for later escalated calls in the same model batch."""
    if allow_writes:
        return []
    if not remaining_tool_calls:
        return []
    sentry = getattr(runtime, "sentry", None)
    if sentry is None:
        return []
    try:
        from mcp_sentry import MCPSentry
        if not isinstance(sentry, MCPSentry):
            return []
    except Exception:
        return []

    requests: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for tool_call in remaining_tool_calls:
        skill_name = getattr(tool_call, "name", "")
        inputs = getattr(tool_call, "args", {}) or {}
        if skill_name not in declared_tool_names:
            continue
        if _tool_input_error(skill_name, inputs):
            continue
        try:
            if runtime._classify_tool_call_safety(skill_name, inputs) != "escalate":
                continue
            perm = sentry.check_permission(skill_name, inputs)
        except Exception as exc:
            _gov_logger.warning(
                "approval_batch_collection_skipped",
                extra={"skill": skill_name, "error": str(exc)},
            )
            continue
        request_id = perm.get("request_id")
        if perm.get("status") != "PENDING" or not request_id:
            continue
        key = (skill_name, str(inputs))
        if key in seen:
            continue
        seen.add(key)
        requests.append({
            "request_id": request_id,
            "tool_name": skill_name,
            "params": inputs,
        })
    return requests


def create_approval_card(
    runtime: Any,
    prompt: str,
    quest_id: str,
    skill_name: str,
    inputs: dict[str, Any],
    sentry_req_id: str | None,
    additional_requests: list[dict[str, Any]] | None = None,
):
    if not runtime.actioncard_factory:
        return None, sentry_req_id, 1

    requests = []
    if sentry_req_id:
        requests.append({
            "request_id": sentry_req_id,
            "tool_name": skill_name,
            "params": inputs or {},
        })
    requests.extend(additional_requests or [])

    try:
        if len(requests) > 1 and hasattr(runtime.actioncard_factory, "from_sentry_request_batch"):
            card = runtime.actioncard_factory.from_sentry_request_batch(
                requests=requests,
                quest_id=quest_id,
                approval_context=_approval_context(prompt, "multiple governed actions", {
                    f"{idx}:{item['tool_name']}": item.get("params", {})
                    for idx, item in enumerate(requests, start=1)
                }),
                approval_reason=_approval_group_reason(requests),
            )
            return card, getattr(card, "card_id", None) or sentry_req_id, len(requests)

        card = runtime.actioncard_factory.from_sentry_request(
            req_id=sentry_req_id or f"block-{skill_name}-{quest_id[:8]}",
            tool_name=skill_name,
            params=inputs or {},
            quest_id=quest_id,
            approval_context=_approval_context(prompt, skill_name, inputs or {}),
            approval_reason=_approval_reason(skill_name),
        )
        return card, sentry_req_id, 1
    except Exception as exc:
        _gov_logger.warning(
            "action_card_creation_failed",
            extra={
                "context": "approval_card",
                "error": str(exc),
            },
        )
        return None, sentry_req_id, max(1, len(requests))


def _emit_tool_call_started(
    runtime: Any,
    tool_call: Any,
    *,
    iteration: int,
    quest_id: str,
    channel: str,
) -> None:
    _gov_logger.debug(
        "tool_call_started",
        extra={
            "skill": tool_call.name,
            "tool_call_id": tool_call.id,
        },
    )
    if runtime.toolflow_emitter:
        runtime.toolflow_emitter.tool_call_started(
            quest_id, iteration + 1, tool_call.name, tool_call.args, channel,
        )


def _reject_undeclared_tool_call(
    runtime: Any,
    tool_call: Any,
    declared_tool_names: set[str],
    tool_receipts: list[dict[str, Any]],
    *,
    iteration: int,
    quest_id: str,
    channel: str,
) -> tuple[str, str, str]:
    skill_name = tool_call.name
    inputs = tool_call.args
    result_data = {
        "error": f"Tool '{skill_name}' does not exist. "
        f"Available tools: {', '.join(sorted(declared_tool_names))}. "
        "If this is a conversational request, respond directly without tools."
    }
    tool_receipts.append({
        "skill": skill_name,
        "inputs": inputs,
        "result": f"REJECTED - undeclared tool '{skill_name}'",
    })
    _persist_tool_call_receipt(
        runtime,
        skill_name,
        inputs or {},
        f"REJECTED - undeclared tool '{skill_name}'",
        outputs=result_data,
        error=result_data["error"],
        iteration=iteration + 1,
    )
    if runtime.toolflow_emitter:
        runtime.toolflow_emitter.tool_call_completed(
            quest_id, iteration + 1, skill_name,
            "REJECTED", "undeclared tool", channel,
        )
    _gov_logger.warning(
        "hallucinated_tool_call_rejected",
        extra={"skill": skill_name},
    )
    return tool_call.id, skill_name, str(result_data)


def _reject_invalid_tool_call(
    runtime: Any,
    tool_call: Any,
    input_error: str,
    tool_receipts: list[dict[str, Any]],
    *,
    iteration: int,
    quest_id: str,
    channel: str,
) -> tuple[str, str, str]:
    skill_name = tool_call.name
    inputs = tool_call.args
    result_data = {"error": input_error}
    tool_receipts.append({
        "skill": skill_name,
        "inputs": inputs,
        "result": f"REJECTED - {input_error}",
    })
    _persist_tool_call_receipt(
        runtime,
        skill_name,
        inputs or {},
        f"REJECTED - {input_error}",
        outputs=result_data,
        error=input_error,
        iteration=iteration + 1,
    )
    if runtime.toolflow_emitter:
        runtime.toolflow_emitter.tool_call_completed(
            quest_id, iteration + 1, skill_name,
            "REJECTED", input_error, channel,
        )
    _gov_logger.warning(
        "invalid_tool_call_rejected",
        extra={"skill": skill_name, "error": input_error},
    )
    return tool_call.id, skill_name, str(result_data)


def _external_network_error(prompt: str, inputs: dict[str, Any]) -> str:
    """Return a rejection reason when network_client lacks explicit operator intent."""
    normalized = " ".join(str(prompt or "").lower().split())
    url = str((inputs or {}).get("url") or "").strip()
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()

    if any(
        marker in normalized
        for marker in (
            "no external",
            "without external",
            "do not contact external",
            "do not call external",
            "do not run external",
            "no network",
            "local only",
        )
    ):
        return "network_client was not allowed because the operator requested local-only or no-external execution."

    if url and url.lower() in normalized:
        return ""
    if host and host in normalized:
        return ""

    explicit_network_intent = (
        "http://",
        "https://",
        "www.",
        "web ",
        "website",
        "internet",
        "online",
        " url",
        "fetch",
        "download",
        "research",
        "search",
        "github",
        "api ",
        "api docs",
        "documentation",
        "endpoint",
    )
    if any(marker in normalized for marker in explicit_network_intent):
        return ""

    return (
        "network_client requires explicit operator intent for external network access. "
        "Use internal health, scheduler, receipt, or command tools for local runtime inspection."
    )


def _reject_network_tool_call(
    runtime: Any,
    tool_call: Any,
    network_error: str,
    tool_receipts: list[dict[str, Any]],
    *,
    iteration: int,
    quest_id: str,
    channel: str,
) -> tuple[str, str, str]:
    skill_name = tool_call.name
    inputs = tool_call.args
    result_data = {
        "error": network_error,
        "instruction": (
            "Do not call external network tools for this request. "
            "Use local runtime status, scheduler, receipt, or command inspection instead."
        ),
    }
    tool_receipts.append({
        "skill": skill_name,
        "inputs": inputs,
        "result": f"REJECTED - {network_error}",
    })
    _persist_tool_call_receipt(
        runtime,
        skill_name,
        inputs or {},
        f"REJECTED - {network_error}",
        outputs=result_data,
        error=network_error,
        iteration=iteration + 1,
    )
    if runtime.toolflow_emitter:
        runtime.toolflow_emitter.tool_call_completed(
            quest_id, iteration + 1, skill_name,
            "REJECTED", network_error, channel,
        )
    _gov_logger.warning(
        "network_tool_call_rejected_without_explicit_intent",
        extra={"skill": skill_name, "url": str((inputs or {}).get("url") or "")[:200]},
    )
    return tool_call.id, skill_name, str(result_data)


def _duplicate_success_tool_result(
    runtime: Any,
    tool_call: Any,
    prior_success: dict[str, Any],
    *,
    iteration: int,
) -> tuple[str, str, str]:
    skill_name = tool_call.name
    result_data = {
        "status": "already_completed",
        "message": "This exact tool target already succeeded earlier in this governed run.",
        "tool": skill_name,
        "outputs": prior_success.get("outputs") or {},
    }
    _gov_logger.warning(
        "duplicate_successful_tool_call_suppressed",
        extra={"skill": skill_name, "iteration": iteration + 1},
    )
    _emit_tool_progress(
        runtime,
        "execution",
        f"Skipping duplicate governed tool already completed: {skill_name}",
        tool_name=skill_name,
        iteration=iteration + 1,
    )
    return tool_call.id, skill_name, str(result_data)


def _sentry_blocks_tool_call(
    runtime: Any,
    skill_name: str,
    inputs: dict[str, Any],
    allow_writes: bool,
) -> tuple[bool, str | None]:
    if runtime._classify_tool_call_safety(skill_name, inputs) != "escalate":
        return False, None

    if hasattr(runtime, "sentry") and runtime.sentry is not None:
        try:
            from mcp_sentry import MCPSentry
            if isinstance(runtime.sentry, MCPSentry):
                perm = runtime.sentry.check_permission(skill_name, inputs)
                request_id = perm.get("request_id")
                return perm["status"] == "PENDING", request_id
        except Exception as exc:
            _logging.warning("MCP Sentry check failed for flagship agentic tool %s: %s", skill_name, exc)
        return False, None

    return not allow_writes, None


def _blocked_tool_response_data(
    runtime: Any,
    skill_name: str,
    inputs: dict[str, Any],
    sentry_req_id: str | None,
    allow_writes: bool,
) -> dict[str, Any]:
    if _ff.FEATURE_DEEP_REASONING_LOOP:
        from src.core.reasoning_artifact import GovernanceFeedback
        feedback = GovernanceFeedback(
            skill_name=skill_name,
            action_detail=str(inputs)[:200],
            blocked_reason="Requires Commander approval" if not allow_writes else "Escalated by security classification",
            permission_state="PENDING" if sentry_req_id else "DENIED",
            trust_record_summary=runtime._get_trust_summary(skill_name, inputs),
            alternatives=runtime._suggest_alternatives(skill_name, inputs),
            resolution_hint="Commander can approve in War Room > Governance Dashboard",
            request_id=sentry_req_id or "",
        )
        return {"governance_feedback": feedback.to_tool_result()}

    escalation_msg = (
        f"BLOCKED: {skill_name} requires Commander approval. "
        "Approve in the War Room Governance Dashboard."
    )
    if sentry_req_id:
        escalation_msg += f" (Approval ID: {sentry_req_id})"
    return {"error": escalation_msg}


def _block_frontier_tool_call(
    runtime: Any,
    *,
    prompt: str,
    result: Any,
    tool_index: int,
    declared_tool_names: set[str],
    tool_call: Any,
    tool_receipts: list[dict[str, Any]],
    allow_writes: bool,
    iteration: int,
    quest_id: str,
    channel: str,
    agentic_start_ms: int,
    sentry_req_id: str | None,
) -> FrontierToolBatchResult:
    skill_name = tool_call.name
    inputs = tool_call.args
    result_data = _blocked_tool_response_data(
        runtime,
        skill_name,
        inputs,
        sentry_req_id,
        allow_writes,
    )
    tool_receipts.append({
        "skill": skill_name,
        "inputs": inputs,
        "result": "ESCALATED - needs Commander approval",
        "approval_id": sentry_req_id,
    })
    _persist_tool_call_receipt(
        runtime,
        skill_name,
        inputs or {},
        "ESCALATED - needs Commander approval",
        outputs=result_data,
        error="Requires Commander approval",
        approval_id=sentry_req_id,
        iteration=iteration + 1,
    )
    if runtime.toolflow_emitter:
        runtime.toolflow_emitter.tool_call_blocked(
            quest_id, iteration + 1, skill_name,
            sentry_req_id or "", channel,
            "Awaiting Commander approval",
        )
    additional_requests = collect_additional_approval_requests(
        runtime,
        result.tool_calls[tool_index + 1:],
        declared_tool_names,
        allow_writes,
    )
    _card, approval_ref, approval_count = create_approval_card(
        runtime,
        prompt,
        quest_id,
        skill_name,
        inputs or {},
        sentry_req_id,
        additional_requests,
    )
    if runtime.toolflow_emitter:
        duration_ms = int(_time.time() * 1000) - agentic_start_ms
        runtime.toolflow_emitter.quest_blocked(
            quest_id,
            "Pending Commander approval",
            approval_ref or sentry_req_id or "",
            duration_ms,
            channel,
        )
    _emit_tool_progress(
        runtime,
        "approval",
        "Waiting for Commander approval",
        approval_id=approval_ref or sentry_req_id,
        tool_name=skill_name,
        approval_count=approval_count,
    )
    return FrontierToolBatchResult(
        tool_results=[],
        pending_response=_pending_approval_response(skill_name, approval_ref, approval_count),
    )


def _execute_frontier_tool_call(
    runtime: Any,
    tool_call: Any,
    tool_receipts: list[dict[str, Any]],
    *,
    iteration: int,
    quest_id: str,
    channel: str,
) -> tuple[str, str, str]:
    skill_name = tool_call.name
    inputs = tool_call.args
    runtime.governor.log_usage("tool_calls", 1)
    exec_success = False
    exec_start_ms = int(_time.time() * 1000)
    _emit_tool_progress(
        runtime,
        "execution",
        f"Executing governed tool: {skill_name}",
        tool_name=skill_name,
        iteration=iteration + 1,
    )
    try:
        exec_result = runtime.skill_executor.run(skill_name, inputs)
        exec_duration_ms = int(_time.time() * 1000) - exec_start_ms
        if exec_result.success:
            normalized = _normalize_tool_success(
                skill_name,
                inputs,
                exec_result.outputs,
                max_result_chars=8000,
            )
            if normalized.success:
                exec_success = True
            result_data = normalized.result_data
            tool_receipts.append(normalized.receipt)
            _persist_tool_call_receipt(
                runtime,
                skill_name,
                inputs or {},
                normalized.result_label,
                outputs=result_data,
                error=normalized.error,
                duration_ms=exec_duration_ms,
                iteration=iteration + 1,
            )
        else:
            normalized = _normalize_tool_failure(
                skill_name,
                inputs,
                exec_result.error,
                structured_result=True,
            )
            result_data = normalized.result_data
            tool_receipts.append(normalized.receipt)
            _persist_tool_call_receipt(
                runtime,
                skill_name,
                inputs or {},
                normalized.result_label,
                outputs=result_data,
                error=normalized.error,
                duration_ms=exec_duration_ms,
                iteration=iteration + 1,
            )
    except Exception as exc:
        exec_duration_ms = int(_time.time() * 1000) - exec_start_ms
        normalized = _normalize_tool_failure(
            skill_name,
            inputs,
            exc,
            exception=True,
            structured_result=True,
        )
        result_data = normalized.result_data
        tool_receipts.append(normalized.receipt)
        _persist_tool_call_receipt(
            runtime,
            skill_name,
            inputs or {},
            normalized.result_label,
            outputs=result_data,
            error=normalized.error,
            duration_ms=exec_duration_ms,
            iteration=iteration + 1,
        )

    if runtime.toolflow_emitter and tool_receipts:
        last_receipt = tool_receipts[-1]
        result_status = last_receipt.get("result", "UNKNOWN")
        output_summary = str(last_receipt.get("outputs", ""))[:200] if exec_success else ""
        runtime.toolflow_emitter.tool_call_completed(
            quest_id, iteration + 1, skill_name,
            result_status, output_summary, channel,
        )
    _emit_tool_progress(
        runtime,
        "execution",
        "Tool result received; validating next step",
        tool_name=skill_name,
        result=str(tool_receipts[-1].get("result", "")) if tool_receipts else None,
        iteration=iteration + 1,
    )
    _record_tool_governance_event(
        runtime,
        skill_name,
        inputs,
        exec_success,
        source="flagship",
    )
    return tool_call.id, skill_name, str(result_data)


def process_frontier_tool_calls(
    runtime: Any,
    *,
    prompt: str,
    result: Any,
    declarations: list[Any],
    tool_receipts: list[dict[str, Any]],
    find_successful_tool_receipt: Any,
    allow_writes: bool,
    iteration: int,
    quest_id: str,
    channel: str,
    agentic_start_ms: int,
) -> FrontierToolBatchResult:
    declared_tool_names = {declaration.name for declaration in declarations}
    tool_results: list[tuple[str, str, str]] = []

    for tool_index, tool_call in enumerate(result.tool_calls):
        skill_name = tool_call.name
        inputs = tool_call.args
        _emit_tool_call_started(
            runtime,
            tool_call,
            iteration=iteration,
            quest_id=quest_id,
            channel=channel,
        )

        if skill_name not in declared_tool_names:
            tool_results.append(_reject_undeclared_tool_call(
                runtime,
                tool_call,
                declared_tool_names,
                tool_receipts,
                iteration=iteration,
                quest_id=quest_id,
                channel=channel,
            ))
            continue

        input_error = _tool_input_error(skill_name, inputs or {})
        if input_error:
            tool_results.append(_reject_invalid_tool_call(
                runtime,
                tool_call,
                input_error,
                tool_receipts,
                iteration=iteration,
                quest_id=quest_id,
                channel=channel,
            ))
            continue

        if skill_name == "network_client":
            network_error = _external_network_error(prompt, inputs or {})
            if network_error:
                tool_results.append(_reject_network_tool_call(
                    runtime,
                    tool_call,
                    network_error,
                    tool_receipts,
                    iteration=iteration,
                    quest_id=quest_id,
                    channel=channel,
                ))
                continue

        prior_success = find_successful_tool_receipt(tool_receipts, skill_name, inputs or {})
        if prior_success:
            tool_results.append(_duplicate_success_tool_result(
                runtime,
                tool_call,
                prior_success,
                iteration=iteration,
            ))
            continue

        _emit_tool_progress(
            runtime,
            "governance",
            f"Evaluating permission for {skill_name}",
            tool_name=skill_name,
            iteration=iteration + 1,
        )
        sentry_blocked, sentry_req_id = _sentry_blocks_tool_call(
            runtime,
            skill_name,
            inputs,
            allow_writes,
        )
        if sentry_blocked:
            return _block_frontier_tool_call(
                runtime,
                prompt=prompt,
                result=result,
                tool_index=tool_index,
                declared_tool_names=declared_tool_names,
                tool_call=tool_call,
                tool_receipts=tool_receipts,
                allow_writes=allow_writes,
                iteration=iteration,
                quest_id=quest_id,
                channel=channel,
                agentic_start_ms=agentic_start_ms,
                sentry_req_id=sentry_req_id,
            )

        tool_results.append(_execute_frontier_tool_call(
            runtime,
            tool_call,
            tool_receipts,
            iteration=iteration,
            quest_id=quest_id,
            channel=channel,
        ))

    return FrontierToolBatchResult(tool_results=tool_results)
