from __future__ import annotations

import json
import logging as _logging
import time as _time
from typing import Any

from tool_loop_approval import (
    pending_approval_response as _pending_approval_response,
    tool_input_error as _tool_input_error,
)
from tool_loop_completion import (
    find_successful_tool_receipt as _find_successful_tool_receipt,
)
from tool_loop_frontier import (
    _external_network_error,
    create_approval_card as _create_approval_card,
)
from tool_loop_governance import (
    record_tool_governance_event as _record_tool_governance_event,
)
from tool_loop_receipts import persist_tool_call_receipt as _persist_tool_call_receipt
from tool_loop_results import (
    normalize_tool_failure as _normalize_tool_failure,
    normalize_tool_success as _normalize_tool_success,
)

_gov_logger = _logging.getLogger("src.core.orchestrator")
MAX_LOCAL_ITERATIONS = 5
LOCAL_CONTEXT_BUDGET = 4000


def _fallback_to_flagship(
    runtime: Any,
    *,
    prompt: str,
    system_instruction: str | None,
    allow_writes: bool,
    context_str: str | None,
) -> str:
    return runtime._agentic_generate(
        prompt=prompt,
        system_instruction=system_instruction,
        allow_writes=allow_writes,
        context_str=context_str,
    )


def _local_system_message() -> str:
    return (
        "You are Lancelot, an autonomous AI agent. Answer the user concisely. "
        "Use tools when needed. Never claim to have done something you haven't actually done via a tool call. "
        "Use emoji sparingly in casual or status replies when it improves clarity; keep technical output clean. "
        "You have access to tools including: schedule_job, network_client, file_operations, memory. "
        "When the user refers to a previous message or adds to a prior request, use the conversation "
        "history in context to understand what they mean."
    )


def _initial_local_messages(
    runtime: Any,
    prompt: str,
    context_str: str | None,
) -> list[dict[str, Any]]:
    ctx = context_str or runtime.context_env.get_context_string()
    if len(ctx) > LOCAL_CONTEXT_BUDGET:
        ctx = ctx[-LOCAL_CONTEXT_BUDGET:]
        _gov_logger.debug(
            "local_context_truncated",
            extra={"context_chars": len(ctx)},
        )
    return [
        {"role": "system", "content": _local_system_message()},
        {"role": "user", "content": f"{ctx}\n\nLATEST USER REQUEST:\n{prompt}"},
    ]


def _record_local_tool_usage(
    runtime: Any,
    iteration: int,
    result: dict[str, Any],
    total_est_tokens: int,
    model_label: str,
) -> int:
    usage = result.get("usage", {})
    iter_tokens = usage.get("total_tokens", 200)
    total_est_tokens += iter_tokens
    runtime.governor.log_usage("tokens", iter_tokens)
    usage_tracker = getattr(runtime, "usage_tracker", None)
    if usage_tracker:
        usage_tracker.record_simple(model_label, iter_tokens)
    _gov_logger.debug(
        "local_agentic_token_usage",
        extra={
            "iteration": iteration + 1,
            "iter_tokens": iter_tokens,
            "total_est_tokens": total_est_tokens,
        },
    )
    return total_est_tokens


def _emit_tool_completed(
    runtime: Any,
    *,
    quest_id: str,
    iteration: int,
    skill_name: str,
    status: str,
    message: str,
    channel: str,
) -> None:
    emitter = getattr(runtime, "toolflow_emitter", None)
    if emitter:
        emitter.tool_call_completed(
            quest_id,
            iteration + 1,
            skill_name,
            status,
            message,
            channel,
        )


def _parse_local_tool_inputs(raw_arguments: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_arguments or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _handle_local_input_error(
    runtime: Any,
    *,
    messages: list[dict[str, Any]],
    tool_receipts: list[dict[str, Any]],
    skill_name: str,
    inputs: dict[str, Any],
    input_error: str,
    tc_id: str,
    iteration: int,
    quest_id: str,
    channel: str,
) -> None:
    result_content = f"Error: {input_error}"
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
        outputs={"error": input_error},
        error=input_error,
        iteration=iteration + 1,
    )
    _emit_tool_completed(
        runtime,
        quest_id=quest_id,
        iteration=iteration,
        skill_name=skill_name,
        status="REJECTED",
        message=input_error,
        channel=channel,
    )
    _gov_logger.warning(
        "local_invalid_tool_call_rejected",
        extra={"skill": skill_name, "error": input_error},
    )
    messages.append({
        "role": "tool",
        "tool_call_id": tc_id,
        "content": result_content,
    })


def _handle_local_duplicate_success(
    *,
    messages: list[dict[str, Any]],
    tool_receipts: list[dict[str, Any]],
    skill_name: str,
    inputs: dict[str, Any],
    tc_id: str,
    iteration: int,
) -> bool:
    prior_success = _find_successful_tool_receipt(tool_receipts, skill_name, inputs or {})
    if not prior_success:
        return False

    result_content = str({
        "status": "already_completed",
        "message": "This exact tool target already succeeded earlier in this governed run.",
        "tool": skill_name,
        "outputs": prior_success.get("outputs") or {},
    })
    _gov_logger.warning(
        "local_duplicate_successful_tool_call_suppressed",
        extra={"skill": skill_name, "iteration": iteration + 1},
    )
    messages.append({
        "role": "tool",
        "tool_call_id": tc_id,
        "content": result_content,
    })
    return True


def _handle_local_network_error(
    runtime: Any,
    *,
    messages: list[dict[str, Any]],
    tool_receipts: list[dict[str, Any]],
    skill_name: str,
    inputs: dict[str, Any],
    network_error: str,
    tc_id: str,
    iteration: int,
    quest_id: str,
    channel: str,
) -> None:
    result_content = str({
        "error": network_error,
        "instruction": (
            "Do not call external network tools for this request. "
            "Use local runtime status, scheduler, receipt, or command inspection instead."
        ),
    })
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
        outputs={"error": network_error},
        error=network_error,
        iteration=iteration + 1,
    )
    _emit_tool_completed(
        runtime,
        quest_id=quest_id,
        iteration=iteration,
        skill_name=skill_name,
        status="REJECTED",
        message=network_error,
        channel=channel,
    )
    _gov_logger.warning(
        "local_network_tool_call_rejected_without_explicit_intent",
        extra={"skill": skill_name, "url": str((inputs or {}).get("url") or "")[:200]},
    )
    messages.append({
        "role": "tool",
        "tool_call_id": tc_id,
        "content": result_content,
    })


def _check_local_sentry(
    runtime: Any,
    skill_name: str,
    inputs: dict[str, Any],
    allow_writes: bool,
) -> tuple[str, str | None, bool]:
    safety = runtime._classify_tool_call_safety(skill_name, inputs)
    sentry_req_id = None
    sentry_blocked = False

    if safety != "escalate":
        return safety, sentry_req_id, sentry_blocked

    if hasattr(runtime, "sentry") and runtime.sentry is not None:
        try:
            from mcp_sentry import MCPSentry
            if isinstance(runtime.sentry, MCPSentry):
                perm = runtime.sentry.check_permission(skill_name, inputs)
                sentry_req_id = perm.get("request_id")
                if perm["status"] == "APPROVED":
                    safety = "auto"
                elif perm["status"] == "PENDING":
                    sentry_blocked = True
        except Exception as exc:
            _logging.warning(
                "MCP Sentry check failed for local agentic tool %s: %s",
                skill_name,
                exc,
            )
    elif not allow_writes:
        sentry_blocked = True

    return safety, sentry_req_id, sentry_blocked


def _local_approval_result(
    runtime: Any,
    *,
    prompt: str,
    tool_receipts: list[dict[str, Any]],
    skill_name: str,
    inputs: dict[str, Any],
    sentry_req_id: str | None,
    iteration: int,
) -> str:
    from feature_flags import FEATURE_DEEP_REASONING_LOOP

    if FEATURE_DEEP_REASONING_LOOP:
        from src.core.reasoning_artifact import GovernanceFeedback
        feedback = GovernanceFeedback(
            skill_name=skill_name,
            action_detail=str(inputs)[:200],
            blocked_reason="Requires Commander approval",
            permission_state="PENDING" if sentry_req_id else "DENIED",
            trust_record_summary=runtime._get_trust_summary(skill_name, inputs),
            alternatives=runtime._suggest_alternatives(skill_name, inputs),
            resolution_hint="Commander can approve in War Room > Governance Dashboard",
            request_id=sentry_req_id or "",
        )
        result_content = feedback.to_tool_result()
    else:
        result_content = f"BLOCKED: {skill_name} requires Commander approval. Approve in War Room."
        if sentry_req_id:
            result_content += f" (Approval ID: {sentry_req_id})"

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
        outputs={"message": result_content},
        error="Requires Commander approval",
        approval_id=sentry_req_id,
        iteration=iteration + 1,
    )
    quest_id = getattr(runtime, "_current_quest_id", None) or ""
    _card, approval_ref, approval_count = _create_approval_card(
        runtime,
        prompt,
        quest_id,
        skill_name,
        inputs or {},
        sentry_req_id,
    )
    return _pending_approval_response(skill_name, approval_ref, approval_count)


def _execute_local_tool(
    runtime: Any,
    *,
    tool_receipts: list[dict[str, Any]],
    skill_name: str,
    inputs: dict[str, Any],
    iteration: int,
) -> str:
    runtime.governor.log_usage("tool_calls", 1)
    exec_success = False
    exec_start_ms = int(_time.time() * 1000)
    try:
        skill_result = runtime.skill_executor.run(skill_name, inputs)
        exec_duration_ms = int(_time.time() * 1000) - exec_start_ms
        if skill_result.success:
            normalized = _normalize_tool_success(
                skill_name,
                inputs,
                skill_result.outputs,
                max_result_chars=4000,
            )
            if normalized.success:
                exec_success = True
        else:
            normalized = _normalize_tool_failure(
                skill_name,
                inputs,
                skill_result.error,
                structured_result=False,
            )
    except Exception as exc:
        exec_duration_ms = int(_time.time() * 1000) - exec_start_ms
        normalized = _normalize_tool_failure(
            skill_name,
            inputs,
            exc,
            exception=True,
            structured_result=False,
        )

    tool_receipts.append(normalized.receipt)
    _persist_tool_call_receipt(
        runtime,
        skill_name,
        inputs or {},
        normalized.result_label,
        outputs=normalized.result_data,
        error=normalized.error,
        duration_ms=exec_duration_ms,
        iteration=iteration + 1,
    )
    _record_tool_governance_event(
        runtime,
        skill_name,
        inputs,
        exec_success,
        source="local",
    )
    return normalized.result_content


def local_agentic_generate(
    runtime: Any,
    prompt: str,
    system_instruction: str = None,
    allow_writes: bool = False,
    context_str: str = None,
) -> str:
    """Run a bounded local-model tool loop for simple requests."""
    local_model = getattr(runtime, "local_model", None)
    local_model_label = "local-llm"
    local_model_timeout = 60.0

    local_roles = getattr(runtime, "local_model_roles", None)
    if local_roles is not None:
        try:
            from src.core.local_model_roles import ROLE_UTILITY

            utility_config = local_roles.config_for(ROLE_UTILITY)
            local_model = local_roles.client_for(ROLE_UTILITY)
            local_model_label = utility_config.model or ROLE_UTILITY
            local_model_timeout = max(1.0, float(utility_config.timeout_s or 60.0))
        except Exception as exc:
            _gov_logger.warning(
                "local_agentic_utility_role_unavailable: %s",
                exc,
                extra={"error": str(exc)},
            )

    if not local_model:
        _gov_logger.debug(
            "local_agentic_fallback_to_flagship",
            extra={"reason": "local_model_unavailable"},
        )
        return _fallback_to_flagship(
            runtime,
            prompt=prompt,
            system_instruction=system_instruction,
            allow_writes=allow_writes,
            context_str=context_str,
        )

    if not local_model.is_healthy():
        _gov_logger.debug(
            "local_agentic_fallback_to_flagship",
            extra={"reason": "local_model_unhealthy", "model": local_model_label},
        )
        return _fallback_to_flagship(
            runtime,
            prompt=prompt,
            system_instruction=system_instruction,
            allow_writes=allow_writes,
            context_str=context_str,
        )

    tools = runtime._build_openai_tool_declarations()
    messages = _initial_local_messages(runtime, prompt, context_str)

    tool_receipts = []
    runtime._last_tool_receipts = tool_receipts
    total_est_tokens = 0
    quest_id = getattr(runtime, "_current_quest_id", None) or ""
    channel = getattr(runtime, "_current_channel", "api")

    for iteration in range(MAX_LOCAL_ITERATIONS):
        _gov_logger.debug(
            "local_agentic_iteration_started",
            extra={
                "iteration": iteration + 1,
                "max_iterations": MAX_LOCAL_ITERATIONS,
            },
        )

        try:
            result = local_model.chat_with_tools(
                messages=messages,
                tools=tools,
                max_tokens=512,
                temperature=0.1,
                timeout=local_model_timeout,
            )
        except Exception as exc:
            _gov_logger.warning(
                "local_agentic_model_call_failed",
                extra={
                    "iteration": iteration + 1,
                    "error": str(exc),
                    "model": local_model_label,
                },
            )
            if tool_receipts:
                return runtime._format_tool_receipts(
                    tool_receipts,
                    note=f"Stopped after local planner error: {exc}. Results so far:",
                )
            _gov_logger.info(
                "local_agentic_fallback_to_flagship",
                extra={"reason": "local_model_error"},
            )
            return _fallback_to_flagship(
                runtime,
                prompt=prompt,
                system_instruction=system_instruction,
                allow_writes=allow_writes,
                context_str=context_str,
            )

        choices = result.get("choices", [])
        if not choices:
            _gov_logger.warning("local_agentic_no_choices")
            return "Error: Local model returned no response."

        choice = choices[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")
        total_est_tokens = _record_local_tool_usage(
            runtime,
            iteration,
            result,
            total_est_tokens,
            local_model_label,
        )

        tool_calls = message.get("tool_calls")
        if not tool_calls or finish_reason == "stop":
            text = message.get("content", "")
            if tool_receipts:
                _gov_logger.debug(
                    "local_agentic_completed",
                    extra={"tool_call_count": len(tool_receipts)},
                )
            return text or "No response from local model."

        # llama-cpp-python expects assistant content to be iterable.
        if message.get("content") is None:
            message["content"] = ""
        messages.append(message)

        for tc in tool_calls:
            func = tc.get("function", {})
            skill_name = func.get("name", "")
            inputs = _parse_local_tool_inputs(func.get("arguments", "{}"))
            tc_id = tc.get("id", f"call_{iteration}_{skill_name}")
            _gov_logger.debug(
                "local_tool_call_started",
                extra={
                    "skill": skill_name,
                    "tool_call_id": tc_id,
                },
            )

            input_error = _tool_input_error(skill_name, inputs or {})
            if input_error:
                _handle_local_input_error(
                    runtime,
                    messages=messages,
                    tool_receipts=tool_receipts,
                    skill_name=skill_name,
                    inputs=inputs,
                    input_error=input_error,
                    tc_id=tc_id,
                    iteration=iteration,
                    quest_id=quest_id,
                    channel=channel,
                )
                continue

            if skill_name == "network_client":
                network_error = _external_network_error(prompt, inputs or {})
                if network_error:
                    _handle_local_network_error(
                        runtime,
                        messages=messages,
                        tool_receipts=tool_receipts,
                        skill_name=skill_name,
                        inputs=inputs,
                        network_error=network_error,
                        tc_id=tc_id,
                        iteration=iteration,
                        quest_id=quest_id,
                        channel=channel,
                    )
                    continue

            if _handle_local_duplicate_success(
                messages=messages,
                tool_receipts=tool_receipts,
                skill_name=skill_name,
                inputs=inputs,
                tc_id=tc_id,
                iteration=iteration,
            ):
                continue

            _safety, sentry_req_id, sentry_blocked = _check_local_sentry(
                runtime,
                skill_name,
                inputs,
                allow_writes,
            )
            if sentry_blocked:
                return _local_approval_result(
                    runtime,
                    prompt=prompt,
                    tool_receipts=tool_receipts,
                    skill_name=skill_name,
                    inputs=inputs,
                    sentry_req_id=sentry_req_id,
                    iteration=iteration,
                )

            result_content = _execute_local_tool(
                runtime,
                tool_receipts=tool_receipts,
                skill_name=skill_name,
                inputs=inputs,
                iteration=iteration,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_content,
            })

    _gov_logger.warning(
        "local_agentic_max_iterations_reached",
        extra={"max_iterations": MAX_LOCAL_ITERATIONS},
    )
    return runtime._format_tool_receipts(
        tool_receipts,
        note="Reached maximum local tool call limit. Here's what I found:",
    )
