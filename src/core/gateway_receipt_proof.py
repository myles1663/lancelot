"""Receipt-proof assembly for Command Center chat runs."""

from __future__ import annotations

from typing import Any

from shared.receipts import get_receipt_service


TERMINAL_CHAT_RUN_STATUSES = {"blocked", "succeeded", "failed", "cancelled"}
TOOL_RECEIPT_TYPES = {"tool_call", "mcp_tool_call"}
APPROVAL_GRANTED_TYPES = {"t3_approved", "mcp_t3_approved", "apl_rule_approved"}
DEGRADED_VERIFICATION_ACTIONS = {"pii_scrub_fallback"}


def chat_run_lineage_ids(run, chat_run_store) -> list[str]:
    lineage: list[str] = []
    seen: set[str] = set()
    current = run

    while current is not None and current.run_id:
        if current.run_id in seen:
            break
        seen.add(current.run_id)
        lineage.append(current.run_id)

        retry_of = str(current.retry_of_run_id or "").strip()
        if not retry_of:
            break
        current = chat_run_store.get(retry_of)

    lineage.reverse()
    return lineage


def load_chat_run_receipts(
    run,
    chat_run_store,
    logger,
    receipt_service_factory=get_receipt_service,
) -> tuple[list[Any], list[str], str | None]:
    try:
        receipt_service = receipt_service_factory("/home/lancelot/data")
        lineage = chat_run_lineage_ids(run, chat_run_store)
        receipts: list[Any] = []
        seen_receipt_ids: set[str] = set()

        for quest_id in lineage:
            for receipt in receipt_service.get_quest_receipts(quest_id):
                receipt_id = str(getattr(receipt, "id", "") or "")
                if receipt_id and receipt_id in seen_receipt_ids:
                    continue
                if receipt_id:
                    seen_receipt_ids.add(receipt_id)
                receipts.append(receipt)

        return receipts, lineage, None
    except Exception as exc:
        logger.warning("Failed to load receipt proof for chat run %s: %s", run.run_id, exc)
        return [], [], str(exc)


def build_chat_run_receipt_proof(
    run,
    chat_run_store,
    logger,
    receipt_service_factory=get_receipt_service,
) -> dict[str, Any] | None:
    if run.status not in TERMINAL_CHAT_RUN_STATUSES:
        return None

    receipts, lineage, error = load_chat_run_receipts(
        run,
        chat_run_store,
        logger,
        receipt_service_factory,
    )
    if error:
        return {
            "available": False,
            "receipt_count": 0,
            "linked_run_count": max(1, len(lineage)),
            "governed_tools": [],
            "approval_state": "unknown",
            "degraded_mode": "unknown",
            "degraded_reasons": [],
            "outcome": run.status,
            "error": error,
        }

    governed_tools: list[str] = []
    seen_tools: set[str] = set()
    approval_state = "not_used"
    degraded_mode = "not_used"
    degraded_reasons: list[str] = []

    for receipt in receipts:
        action_type = str(getattr(receipt, "action_type", "") or "")
        action_name = str(getattr(receipt, "action_name", "") or "")
        receipt_status = str(getattr(receipt, "status", "") or "")
        metadata = getattr(receipt, "metadata", None) or {}
        outputs = getattr(receipt, "outputs", None) or {}
        error_message = str(getattr(receipt, "error_message", "") or "")

        if action_type in TOOL_RECEIPT_TYPES:
            tool_name = str(metadata.get("tool_name") or action_name or "").strip()
            if tool_name and tool_name not in seen_tools:
                seen_tools.add(tool_name)
                governed_tools.append(tool_name)
            if approval_state != "used" and (
                receipt_status == "pending" or metadata.get("approval_id")
            ):
                approval_state = "required"

        if action_type in APPROVAL_GRANTED_TYPES:
            approval_state = "used"
        elif action_type == "action_card_resolved":
            resolved_status = str(outputs.get("status") or "").lower()
            if action_name.endswith(".approve") or resolved_status == "approved":
                approval_state = "used"

        degraded_flag = bool(metadata.get("degraded_privacy")) or bool(outputs.get("fallback_used"))
        if action_type == "verification" and (
            degraded_flag or action_name in DEGRADED_VERIFICATION_ACTIONS
        ):
            degraded_mode = "used"
            reason = (
                str(metadata.get("reason") or "").strip()
                or str(outputs.get("reason") or "").strip()
                or error_message.strip()
                or action_name
            )
            if reason and reason not in degraded_reasons:
                degraded_reasons.append(reason)

    if approval_state == "not_used" and run.status == "blocked":
        approval_state = "required"

    return {
        "available": True,
        "receipt_count": len(receipts),
        "linked_run_count": max(1, len(lineage)),
        "governed_tools": governed_tools,
        "approval_state": approval_state,
        "degraded_mode": degraded_mode,
        "degraded_reasons": degraded_reasons,
        "outcome": run.status,
    }


def chat_run_payload(
    run,
    chat_run_store,
    logger,
    receipt_service_factory=get_receipt_service,
) -> dict[str, Any]:
    payload = run.to_dict()
    payload["run_id"] = run.run_id
    payload["receipt_proof"] = build_chat_run_receipt_proof(
        run,
        chat_run_store,
        logger,
        receipt_service_factory,
    )
    return payload
