from __future__ import annotations

import json
import logging
from typing import Any

from receipts import ActionType, CognitionTier, ReceiptStatus, create_finalized_receipt

_logger = logging.getLogger("src.core.orchestrator")


def receipt_safe_payload(value: Any, limit: int = 4000) -> Any:
    try:
        rendered = json.dumps(value, default=str, sort_keys=True)
    except Exception:
        rendered = str(value)
        if len(rendered) > limit:
            return {"truncated": True, "preview": rendered[:limit]}
        return rendered
    if len(rendered) > limit:
        return {"truncated": True, "preview": rendered[:limit]}
    try:
        return json.loads(rendered)
    except Exception:
        return rendered


def persist_tool_call_receipt(
    runtime: Any,
    skill_name: str,
    inputs: dict[str, Any],
    result_label: str,
    outputs: Any = None,
    *,
    error: str | None = None,
    approval_id: str | None = None,
    duration_ms: int | None = None,
    iteration: int | None = None,
) -> None:
    receipt_service = getattr(runtime, "receipt_service", None)
    if receipt_service is None:
        return

    label = str(result_label or "")
    status = ReceiptStatus.SUCCESS
    if label.startswith("ESCALATED"):
        status = ReceiptStatus.PENDING
    elif label.startswith(("FAILED", "EXCEPTION", "REJECTED")):
        status = ReceiptStatus.FAILURE

    metadata = {
        "tool_name": skill_name,
        "result": label,
        "channel": getattr(runtime, "_current_channel", None),
        "iteration": iteration,
    }
    if approval_id:
        metadata["approval_id"] = approval_id

    try:
        receipt = create_finalized_receipt(
            ActionType.TOOL_CALL,
            skill_name,
            {"tool": skill_name, "inputs": receipt_safe_payload(inputs or {})},
            outputs=receipt_safe_payload(outputs or {}),
            status=status,
            tier=CognitionTier.DETERMINISTIC,
            quest_id=getattr(runtime, "_current_quest_id", None),
            metadata={key: value for key, value in metadata.items() if value is not None},
            operator_id=getattr(runtime, "_current_operator_id", None) or None,
            session_id=getattr(runtime, "_current_session_id", None) or None,
            duration_ms=duration_ms,
            error_message=error,
        )
        receipt_service.create(receipt)
    except Exception as exc:
        _logger.warning(
            "tool_call_receipt_persist_failed",
            extra={"skill": skill_name, "error": str(exc)},
        )
