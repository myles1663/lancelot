"""Work-ledger, access, and progress helpers for gateway chat runtime."""

from __future__ import annotations

from typing import Any

from chat_runs import ChatRun
from work_ledger import WorkItem

_WORK_HELPER_NAMES: set[str] = set()
_WORK_IMPLEMENTATIONS: dict[str, object] = {}


def bind_gateway_globals(**kwargs) -> None:
    for name in _WORK_HELPER_NAMES:
        if name in kwargs:
            continue
        implementation = _WORK_IMPLEMENTATIONS.get(name)
        if implementation is not None:
            globals()[name] = implementation
    globals().update(kwargs)


def _can_access_chat_run(run: ChatRun, identity) -> bool:
    auth_method = getattr(identity, "auth_method", "")
    identity_session_id = getattr(identity, "session_id", "")
    identity_operator_id = getattr(identity, "operator_id", "")
    return bool(
        auth_method == "api_key"
        or not run.session_id
        or run.session_id == identity_session_id
        or (run.operator_id and run.operator_id == identity_operator_id)
    )


def _can_access_work_item(item: WorkItem, identity) -> bool:
    auth_method = getattr(identity, "auth_method", "")
    identity_session_id = getattr(identity, "session_id", "")
    identity_operator_id = getattr(identity, "operator_id", "")
    return bool(
        auth_method == "api_key"
        or not item.session_id
        or item.session_id == identity_session_id
        or (item.operator_id and item.operator_id == identity_operator_id)
    )


def _sync_work_ledger_from_chat_run(
    run: ChatRun | None,
    *,
    event_type: str = "chat_run_updated",
    metadata: dict[str, Any] | None = None,
) -> WorkItem | None:
    if run is None:
        return None
    try:
        item = work_ledger_store.upsert_from_chat_run(
            run,
            event_type=event_type,
            metadata=metadata,
        )
        _close_superseded_retry_source(run)
        return item
    except Exception as exc:
        logger.warning(
            "Failed to sync work ledger from chat run %s: %s",
            getattr(run, "run_id", "unknown"),
            exc,
        )
        return None


def _close_superseded_retry_source(run: ChatRun | None) -> WorkItem | None:
    if run is None:
        return None
    retry_of = str(getattr(run, "retry_of_run_id", "") or "").strip()
    status = str(getattr(run, "status", "") or "").strip().lower()
    if not retry_of or status not in _TERMINAL_CHAT_RUN_STATUSES:
        return None
    return work_ledger_store.mark_superseded_by_retry(
        retry_of,
        retry_run_id=run.run_id,
        retry_status=status,
    )


def _reconcile_superseded_retry_work(*, limit: int = 200) -> int:
    closed = 0
    try:
        retries = chat_run_store.list_terminal_retries(limit=limit)
    except Exception as exc:
        logger.warning("Failed to list terminal retries for work reconciliation: %s", exc)
        return 0

    for retry in retries:
        try:
            before = work_ledger_store.get_work(retry.retry_of_run_id)
            updated = _close_superseded_retry_source(retry)
            if (
                before is not None
                and before.status not in {"completed", "failed", "cancelled"}
                and updated is not None
                and updated.status in {"completed", "failed", "cancelled"}
            ):
                closed += 1
        except Exception as exc:
            logger.warning(
                "Failed to close superseded work %s from retry %s: %s",
                getattr(retry, "retry_of_run_id", "unknown"),
                getattr(retry, "run_id", "unknown"),
                exc,
            )
    return closed


def _archive_pending_actioncards_for_work(
    quest_id: str,
    *,
    identity,
    reason: str,
) -> list[dict[str, Any]]:
    card_store = getattr(app.state, "actioncard_store", None)
    card_resolver = getattr(app.state, "actioncard_resolver", None)
    if card_store is None or card_resolver is None:
        return []
    list_by_quest = getattr(card_store, "list_pending_by_quest", None)
    archive_card = getattr(card_resolver, "archive", None)
    if not callable(list_by_quest) or not callable(archive_card):
        return []

    archived: list[dict[str, Any]] = []
    try:
        cards = list_by_quest(quest_id, limit=50)
    except Exception as exc:
        logger.warning("Failed to list ActionCards for archived work %s: %s", quest_id, exc)
        return []

    for card in cards:
        try:
            result = archive_card(
                card.card_id,
                channel="work_archive",
                operator_id=getattr(identity, "operator_id", ""),
                session_id=getattr(identity, "session_id", ""),
                actor=getattr(identity, "display_name", "") or getattr(identity, "operator_id", ""),
                reason=f"Work item archived: {reason}",
            )
            if result.get("status") == "archived":
                archived.append({
                    "card_id": card.card_id,
                    "title": card.title,
                    "source_system": card.source_system,
                })
        except Exception as exc:
            logger.warning("Failed to archive ActionCard %s for work %s: %s", card.card_id, quest_id, exc)
    return archived


def _emit_chat_run_event(event_type: str, run: ChatRun, **extra: Any) -> None:
    try:
        from event_bus import Event, event_bus

        payload = _chat_run_payload(run)
        payload.update(extra)
        event_bus.publish_sync(Event(type=event_type, payload=payload))
    except Exception as exc:
        logger.warning("Failed to emit %s for chat run %s: %s", event_type, run.run_id, exc)


async def _record_chat_progress_event(event) -> None:
    payload = getattr(event, "payload", {}) or {}
    run_id = str(payload.get("chat_run_id") or payload.get("run_id") or payload.get("quest_id") or "")
    if not run_id:
        return
    phase = str(payload.get("phase") or "processing")
    message = str(payload.get("message") or "Processing request")
    severity = str(payload.get("severity") or "") or None
    degraded = payload.get("degraded")
    degraded_reason = str(payload.get("degraded_reason") or "") or None
    progress_metadata = {
        key: value for key, value in payload.items()
        if key not in {
            "chat_run_id",
            "run_id",
            "quest_id",
            "phase",
            "message",
            "severity",
            "degraded",
            "degraded_reason",
        }
    }
    run = _record_persisted_chat_progress(
        run_id,
        phase=phase,
        message=message,
        event_timestamp=getattr(event, "timestamp", None),
        severity=severity,
        degraded=bool(degraded) if degraded is not None else None,
        degraded_reason=degraded_reason,
        metadata=progress_metadata,
    )


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
    run = chat_run_store.record_progress(
        run_id,
        phase=phase,
        message=message,
        event_timestamp=event_timestamp,
        severity=severity,
        degraded=degraded,
        degraded_reason=degraded_reason,
        metadata=metadata,
    )
    if run is not None:
        ledger_metadata = dict(metadata or {})
        if severity:
            ledger_metadata["severity"] = severity
        if degraded is not None:
            ledger_metadata["degraded"] = bool(degraded)
        if degraded_reason:
            ledger_metadata["degraded_reason"] = degraded_reason
        _sync_work_ledger_from_chat_run(
            run,
            event_type="chat_run_progress",
            metadata=ledger_metadata,
        )
    if run is not None and run.status not in {"succeeded", "failed", "cancelled"}:
        _emit_chat_run_event("chat.run_progress", run)
    return run

def _classify_chat_run_status(response_text: str) -> str:
    text = str(response_text or "")
    lowered = text.lower()
    if (
        "send `continue` after approval" in lowered
        or "continue control to resume" in lowered
        or "approval id:" in lowered
        or "approval group id:" in lowered
        or "pending_approval" in lowered
        or "pending approval" in lowered
    ):
        return "blocked"
    if text.startswith("Error:") or text.startswith("Status: FAILED") or "\nStatus: FAILED" in text:
        return "failed"
    return "succeeded"


_WORK_HELPER_NAMES = {
    name
    for name, value in globals().items()
    if name.startswith("_") and callable(value)
}
_WORK_IMPLEMENTATIONS = {name: globals()[name] for name in _WORK_HELPER_NAMES}
