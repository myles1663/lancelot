from __future__ import annotations

import logging as _logging
from typing import Any, Optional

from receipts import (
    ActionType,
    CognitionTier,
    ReceiptStatus,
    create_finalized_receipt,
)
from src.core.frontier_scrubber import (
    LocalPIIScrubber,
    PIIScrubError,
    PIIScrubPayloadError,
    detect_frontier_pii_categories as _detect_frontier_pii_categories_fn,
)


def emit_frontier_scrub_receipt(
    runtime: Any,
    *,
    action_name: str,
    source: str,
    path: str,
    input_length: int,
    detected_categories: tuple[str, ...] = (),
    residual_categories: tuple[str, ...] = (),
    scrubbed: bool = False,
    fallback_used: bool = False,
    reason: Optional[str] = None,
    pre_scrubbed: bool = False,
    pre_scrub_source: Optional[str] = None,
    local_verification_used: bool = False,
    scrub_stages: tuple[str, ...] = (),
) -> None:
    """Persist frontier-scrub audit events as immutable receipts."""
    if not getattr(runtime, "receipt_service", None):
        return

    try:
        policy = runtime._current_model_usage_status()["frontier_scrub_mode"]
        status = ReceiptStatus.FAILURE if action_name == "pii_scrub_blocked" else ReceiptStatus.SUCCESS
        scrub_pipeline = list(scrub_stages)
        if not scrub_pipeline:
            if pre_scrubbed:
                scrub_pipeline.append("deterministic_prescrub")
            if local_verification_used:
                scrub_pipeline.append("local_model_verification")
            if fallback_used:
                scrub_pipeline.append("deterministic_fallback")
            if detected_categories or residual_categories or scrubbed:
                scrub_pipeline.append("deterministic_validation")
        receipt = create_finalized_receipt(
            ActionType.VERIFICATION,
            action_name,
            {
                "scrub_mode": policy,
                "source": source,
                "payload_path": path,
                "input_length": input_length,
                "detected_categories": list(detected_categories),
            },
            outputs={
                "scrubbed": scrubbed,
                "fallback_used": fallback_used,
                "residual_categories": list(residual_categories),
                "pre_scrubbed": pre_scrubbed,
                "pre_scrub_source": pre_scrub_source,
                "local_verification_used": local_verification_used,
                "scrub_pipeline": scrub_pipeline,
            },
            status=status,
            tier=CognitionTier.CLASSIFICATION,
            quest_id=getattr(runtime, "_current_quest_id", None),
            metadata={
                "frontier_scrub_event": True,
                "pii_detected": bool(detected_categories),
                "pii_scrubbed": scrubbed and not fallback_used,
                "pii_categories": list(detected_categories),
                "residual_categories": list(residual_categories),
                "degraded_privacy": fallback_used,
                "pre_scrubbed": pre_scrubbed,
                "pre_scrub_source": pre_scrub_source,
                "local_verification_used": local_verification_used,
                "scrub_pipeline": scrub_pipeline,
                "channel": getattr(runtime, "_current_channel", None),
                "source": source,
                "reason": reason,
                "operator_id": getattr(runtime, "_current_operator_id", None),
                "operator_name": getattr(runtime, "_current_operator_name", None),
                "session_id": getattr(runtime, "_current_session_id", None),
            },
            operator_id=getattr(runtime, "_current_operator_id", None),
            session_id=getattr(runtime, "_current_session_id", None),
            error_message=reason if status == ReceiptStatus.FAILURE else None,
        )
        runtime.receipt_service.create(receipt)
    except Exception as exc:
        _logging.warning(
            "Failed to record frontier scrub receipt %s for %s: %s",
            action_name,
            path,
            exc,
        )


def record_frontier_scrub_result(
    runtime: Any,
    result: Any,
    *,
    path: str,
    input_length: int,
) -> None:
    """Emit receipts for frontier scrub events that materially affect governance."""
    if result.source == "policy_disabled":
        return
    if result.fallback_used:
        runtime._emit_chat_progress(
            "frontier_scrub",
            "Local scrub fallback active; using deterministic redaction path",
            severity="warning",
            degraded=True,
            degraded_reason=result.reason or "deterministic local scrub fallback used",
            source=result.source,
        )
        runtime._emit_frontier_scrub_receipt(
            action_name="pii_scrub_fallback",
            source=result.source,
            path=path,
            input_length=input_length,
            detected_categories=result.detected_categories,
            residual_categories=result.residual_categories,
            scrubbed=result.scrubbed,
            fallback_used=True,
            reason=result.reason,
            pre_scrubbed=getattr(result, "pre_scrubbed", False),
            pre_scrub_source=getattr(result, "pre_scrub_source", None),
            local_verification_used=getattr(result, "local_verification_used", False),
            scrub_stages=getattr(result, "scrub_stages", ()),
        )
        return
    if result.detected_categories:
        runtime._emit_frontier_scrub_receipt(
            action_name="pii_scrub_applied",
            source=result.source,
            path=path,
            input_length=input_length,
            detected_categories=result.detected_categories,
            residual_categories=result.residual_categories,
            scrubbed=result.scrubbed,
            fallback_used=False,
            reason=result.reason,
            pre_scrubbed=getattr(result, "pre_scrubbed", False),
            pre_scrub_source=getattr(result, "pre_scrub_source", None),
            local_verification_used=getattr(result, "local_verification_used", False),
            scrub_stages=getattr(result, "scrub_stages", ()),
        )


def get_frontier_scrubber(runtime: Any) -> LocalPIIScrubber:
    """Return the canonical frontier scrubber bound to live runtime deps."""
    scrubber = getattr(runtime, "frontier_scrubber", None)
    if scrubber is None:
        scrubber = LocalPIIScrubber()
        runtime.frontier_scrubber = scrubber
    scrubber.bind(
        model_router=getattr(runtime, "model_router", None),
        local_model=getattr(runtime, "local_model", None),
        local_model_roles=getattr(runtime, "local_model_roles", None),
    )
    return scrubber


def redact_for_frontier(runtime: Any, text: str) -> str:
    """Scrub sensitive text locally before it reaches a frontier provider."""
    scrubber = runtime._get_frontier_scrubber()
    try:
        result = scrubber.scrub_text(text)
    except PIIScrubError as exc:
        runtime._emit_chat_progress(
            "frontier_scrub",
            "Frontier payload blocked by local scrub policy",
            severity="error",
            degraded=True,
            degraded_reason=str(exc),
            source="required_policy_block",
        )
        runtime._emit_frontier_scrub_receipt(
            action_name="pii_scrub_blocked",
            source="required_policy_block",
            path="root",
            input_length=len(text) if isinstance(text, str) else 0,
            detected_categories=tuple(sorted(_detect_frontier_pii_categories_fn(text))),
            residual_categories=tuple(sorted(_detect_frontier_pii_categories_fn(text))),
            scrubbed=False,
            fallback_used=False,
            reason=str(exc),
        )
        raise

    runtime._record_frontier_scrub_result(
        result,
        path="root",
        input_length=len(text) if isinstance(text, str) else 0,
    )
    return result.text


def scrub_frontier_payload(runtime: Any, payload: Any) -> Any:
    """Recursively scrub provider-native payloads where text content is present."""
    scrubber = runtime._get_frontier_scrubber()
    try:
        scrubbed, audit_events = scrubber.scrub_payload_with_audit(payload)
    except PIIScrubPayloadError as exc:
        runtime._emit_chat_progress(
            "frontier_scrub",
            "Frontier payload blocked by local scrub policy",
            severity="error",
            degraded=True,
            degraded_reason=exc.reason,
            source="required_policy_block",
        )
        runtime._emit_frontier_scrub_receipt(
            action_name="pii_scrub_blocked",
            source="required_policy_block",
            path=exc.path,
            input_length=len(exc.original_text),
            detected_categories=exc.detected_categories,
            residual_categories=exc.detected_categories,
            scrubbed=False,
            fallback_used=False,
            reason=exc.reason,
        )
        raise
    except PIIScrubError as exc:
        runtime._emit_chat_progress(
            "frontier_scrub",
            "Frontier payload blocked by local scrub policy",
            severity="error",
            degraded=True,
            degraded_reason=str(exc),
            source="required_policy_block",
        )
        runtime._emit_frontier_scrub_receipt(
            action_name="pii_scrub_blocked",
            source="required_policy_block",
            path="root",
            input_length=0,
            reason=str(exc),
        )
        raise

    for event in audit_events:
        runtime._record_frontier_scrub_result(
            event,
            path=event.path,
            input_length=event.input_length,
        )
    return scrubbed


def build_frontier_user_message(
    runtime: Any,
    text: str,
    images: list | None = None,
) -> Any:
    """Build a frontier-bound user message after local redaction."""
    runtime._emit_chat_progress(
        "frontier_scrub",
        "Scrubbing outbound user/context payload locally",
    )
    return runtime.provider.build_user_message(runtime._redact_for_frontier(text), images=images)


def build_frontier_tool_response_message(
    runtime: Any,
    tool_results: list[tuple[str, str, str]],
) -> Any:
    """Build a frontier-bound tool response message after local redaction."""
    runtime._emit_chat_progress(
        "frontier_scrub",
        "Scrubbing tool results before frontier model handoff",
    )
    scrubbed_results = []
    for call_id, fn_name, result_str in tool_results:
        scrubbed_results.append((call_id, fn_name, runtime._redact_for_frontier(str(result_str))))
    return runtime.provider.build_tool_response_message(scrubbed_results)


def provider_generate(
    runtime: Any,
    *,
    model: str,
    messages: list,
    system_instruction: str = "",
    config: Optional[dict] = None,
) -> Any:
    """Frontier provider wrapper that enforces local scrubbing before generation."""
    runtime._emit_chat_progress(
        "frontier_scrub",
        "Validating provider payload against local scrub policy",
    )
    scrubbed_messages = runtime._scrub_frontier_payload(messages)
    runtime._emit_chat_progress(
        "provider_call",
        "Calling governed frontier model",
        model=model,
        wait_reason="provider_call",
    )
    return runtime.provider.generate(
        model=model,
        messages=scrubbed_messages,
        system_instruction=system_instruction,
        config=config,
    )


def provider_generate_with_tools(
    runtime: Any,
    *,
    model: str,
    messages: list,
    system_instruction: str,
    tools: list,
    tool_config: Optional[dict] = None,
    config: Optional[dict] = None,
) -> Any:
    """Frontier provider wrapper for tool calls with local scrubbing."""
    runtime._emit_chat_progress(
        "frontier_scrub",
        "Validating tool-capable provider payload against local scrub policy",
    )
    scrubbed_messages = runtime._scrub_frontier_payload(messages)
    runtime._emit_chat_progress(
        "provider_call",
        "Calling governed frontier model with tools",
        model=model,
        wait_reason="provider_call",
    )
    return runtime.provider.generate_with_tools(
        model=model,
        messages=scrubbed_messages,
        system_instruction=system_instruction,
        tools=tools,
        tool_config=tool_config,
        config=config,
    )
