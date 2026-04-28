"""Durable active-work ledger for long-running governed work.

The ledger is intentionally separate from chat history and TaskRun storage.
Chat history is a transcript; TaskRun is executable-plan state. This module
stores the compact operator-facing state needed to resume multiday work:
objective, current phase, blocker, next action, and receipt-backed events.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional


ACTIVE_STATUSES = {"active", "blocked", "checkpointed"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
VALID_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
CHECKPOINT_STATUSES = {"blocked", "checkpointed", "completed", "failed", "cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str | None) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _preview(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _bounded_jsonable(value: Any, *, depth: int = 0) -> Any:
    """Return a JSON-safe, bounded representation for ledger metadata."""
    if depth > 4:
        return _preview(value, 200)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _preview(value, 1000)
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 40:
                bounded["..."] = f"{len(value) - 40} more keys"
                break
            bounded[_preview(key, 80)] = _bounded_jsonable(item, depth=depth + 1)
        return bounded
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        bounded_items = [_bounded_jsonable(item, depth=depth + 1) for item in items[:40]]
        if len(items) > 40:
            bounded_items.append(f"... {len(items) - 40} more items")
        return bounded_items
    return _preview(value, 500)


def _dumps(value: Any) -> str:
    return json.dumps(_bounded_jsonable(value), separators=(",", ":"), sort_keys=True)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _normalize_status(status: str | None) -> str:
    normalized = str(status or "active").strip().lower()
    if normalized == "succeeded":
        normalized = "completed"
    if normalized == "running" or normalized == "queued":
        normalized = "active"
    if normalized not in VALID_STATUSES:
        raise ValueError(f"Unsupported work ledger status: {status!r}")
    return normalized


def _chat_run_status_to_work_status(status: str | None) -> str:
    normalized = str(status or "active").strip().lower()
    if normalized in {"queued", "running"}:
        return "active"
    if normalized == "succeeded":
        return "completed"
    if normalized in {"blocked", "failed", "cancelled"}:
        return normalized
    return "active"


@dataclass
class WorkItem:
    quest_id: str
    session_id: str = ""
    operator_id: str = ""
    channel: str = "warroom"
    objective: str = ""
    status: str = "active"
    phase: str = "queued"
    current_step: str = ""
    next_action: str = ""
    blocker: str = ""
    last_chat_run_id: str = ""
    last_task_run_id: str = ""
    last_receipt_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quest_id": self.quest_id,
            "session_id": self.session_id,
            "operator_id": self.operator_id,
            "channel": self.channel,
            "objective": self.objective,
            "status": self.status,
            "phase": self.phase,
            "current_step": self.current_step,
            "next_action": self.next_action,
            "blocker": self.blocker,
            "last_chat_run_id": self.last_chat_run_id,
            "last_task_run_id": self.last_task_run_id,
            "last_receipt_id": self.last_receipt_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass
class WorkLedgerEvent:
    event_id: str
    quest_id: str
    event_type: str
    summary: str
    receipt_id: str = ""
    phase: str = ""
    status: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "quest_id": self.quest_id,
            "event_type": self.event_type,
            "summary": self.summary,
            "receipt_id": self.receipt_id,
            "phase": self.phase,
            "status": self.status,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class WorkLedgerStore:
    """SQLite-backed active-work ledger with append-only event history."""

    CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS active_work_items (
        quest_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL DEFAULT '',
        operator_id TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL DEFAULT 'warroom',
        objective TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        phase TEXT NOT NULL DEFAULT 'queued',
        current_step TEXT NOT NULL DEFAULT '',
        next_action TEXT NOT NULL DEFAULT '',
        blocker TEXT NOT NULL DEFAULT '',
        last_chat_run_id TEXT NOT NULL DEFAULT '',
        last_task_run_id TEXT NOT NULL DEFAULT '',
        last_receipt_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS work_ledger_events (
        event_id TEXT PRIMARY KEY,
        quest_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        receipt_id TEXT NOT NULL DEFAULT '',
        phase TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS work_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        quest_id TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        completed_work TEXT NOT NULL DEFAULT '[]',
        pending_work TEXT NOT NULL DEFAULT '[]',
        open_decisions TEXT NOT NULL DEFAULT '[]',
        files_touched TEXT NOT NULL DEFAULT '[]',
        approvals TEXT NOT NULL DEFAULT '[]',
        receipt_ids TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_work_items_status ON active_work_items(status);
    CREATE INDEX IF NOT EXISTS idx_work_items_session ON active_work_items(session_id);
    CREATE INDEX IF NOT EXISTS idx_work_items_updated ON active_work_items(updated_at);
    CREATE INDEX IF NOT EXISTS idx_work_events_quest ON work_ledger_events(quest_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_work_checkpoints_quest ON work_checkpoints(quest_id, created_at);
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.connection = conn
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_database(self) -> None:
        with self._transaction() as conn:
            conn.executescript(self.CREATE_SQL)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(active_work_items)").fetchall()
            }
            migrations = {
                "metadata": "ALTER TABLE active_work_items ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'",
                "last_receipt_id": "ALTER TABLE active_work_items ADD COLUMN last_receipt_id TEXT NOT NULL DEFAULT ''",
                "last_task_run_id": "ALTER TABLE active_work_items ADD COLUMN last_task_run_id TEXT NOT NULL DEFAULT ''",
            }
            for column, sql in migrations.items():
                if column not in columns:
                    conn.execute(sql)

    def upsert_work(
        self,
        *,
        quest_id: str,
        objective: str = "",
        session_id: str = "",
        operator_id: str = "",
        channel: str = "warroom",
        status: str = "active",
        phase: str = "",
        current_step: str = "",
        next_action: str = "",
        blocker: str = "",
        last_chat_run_id: str = "",
        last_task_run_id: str = "",
        last_receipt_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> WorkItem:
        quest_id = str(quest_id or "").strip()
        if not quest_id:
            raise ValueError("quest_id is required")

        status = _normalize_status(status)
        now = _utc_now()
        existing = self.get_work(quest_id)
        merged_metadata = dict(existing.metadata if existing else {})
        merged_metadata.update(_bounded_jsonable(metadata or {}))

        values = {
            "quest_id": quest_id,
            "session_id": session_id or (existing.session_id if existing else ""),
            "operator_id": operator_id or (existing.operator_id if existing else ""),
            "channel": channel or (existing.channel if existing else "warroom"),
            "objective": objective or (existing.objective if existing else ""),
            "status": status,
            "phase": phase or (existing.phase if existing else "queued"),
            "current_step": current_step or (existing.current_step if existing else ""),
            "next_action": next_action or (existing.next_action if existing else ""),
            "blocker": blocker or (existing.blocker if existing else ""),
            "last_chat_run_id": last_chat_run_id or (existing.last_chat_run_id if existing else ""),
            "last_task_run_id": last_task_run_id or (existing.last_task_run_id if existing else ""),
            "last_receipt_id": last_receipt_id or (existing.last_receipt_id if existing else ""),
            "created_at": existing.created_at if existing else now,
            "updated_at": now,
            "metadata": _dumps(merged_metadata),
        }

        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO active_work_items (
                    quest_id, session_id, operator_id, channel, objective, status,
                    phase, current_step, next_action, blocker, last_chat_run_id,
                    last_task_run_id, last_receipt_id, created_at, updated_at, metadata
                ) VALUES (
                    :quest_id, :session_id, :operator_id, :channel, :objective,
                    :status, :phase, :current_step, :next_action, :blocker,
                    :last_chat_run_id, :last_task_run_id, :last_receipt_id,
                    :created_at, :updated_at, :metadata
                )
                ON CONFLICT(quest_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    operator_id = excluded.operator_id,
                    channel = excluded.channel,
                    objective = excluded.objective,
                    status = excluded.status,
                    phase = excluded.phase,
                    current_step = excluded.current_step,
                    next_action = excluded.next_action,
                    blocker = excluded.blocker,
                    last_chat_run_id = excluded.last_chat_run_id,
                    last_task_run_id = excluded.last_task_run_id,
                    last_receipt_id = excluded.last_receipt_id,
                    updated_at = excluded.updated_at,
                    metadata = excluded.metadata
                """,
                values,
            )
        return self.get_work(quest_id)  # type: ignore[return-value]

    def upsert_from_chat_run(
        self,
        run: Any,
        *,
        event_type: str = "chat_run_updated",
        metadata: Optional[dict[str, Any]] = None,
    ) -> WorkItem:
        status = _chat_run_status_to_work_status(getattr(run, "status", "active"))
        phase = str(getattr(run, "phase", "") or getattr(run, "status", "") or "processing")
        blocker = ""
        if status == "blocked":
            blocker = str(getattr(run, "last_progress_message", "") or "Waiting for Commander approval.")
        elif status == "failed":
            blocker = str(getattr(run, "error", "") or "Chat run failed.")
        elif status == "cancelled":
            blocker = str(getattr(run, "cancel_reason", "") or getattr(run, "error", "") or "Cancelled.")

        next_action = ""
        if status in {"active", "blocked", "checkpointed"}:
            next_action = str(getattr(run, "last_progress_message", "") or "")
        elif status == "failed":
            next_action = "Review the failure and retry the governed run if appropriate."

        event_metadata = {
            "chat_run_status": getattr(run, "status", ""),
            "request_id": getattr(run, "request_id", ""),
            "retry_of_run_id": getattr(run, "retry_of_run_id", ""),
            "retry_count": getattr(run, "retry_count", 0),
            "last_progress_message": getattr(run, "last_progress_message", ""),
        }
        event_metadata.update(metadata or {})

        item = self.upsert_work(
            quest_id=str(getattr(run, "run_id", "") or ""),
            objective=str(getattr(run, "message_text", "") or getattr(run, "message_preview", "") or ""),
            session_id=str(getattr(run, "session_id", "") or ""),
            operator_id=str(getattr(run, "operator_id", "") or ""),
            channel=str(getattr(run, "channel", "") or "warroom"),
            status=status,
            phase=phase,
            next_action=next_action,
            blocker=blocker,
            last_chat_run_id=str(getattr(run, "run_id", "") or ""),
            metadata=event_metadata,
        )
        summary = (
            str(getattr(run, "last_progress_message", "") or "")
            or str(getattr(run, "error", "") or "")
            or f"Chat run {getattr(run, 'status', 'updated')}"
        )
        self.append_event(
            quest_id=item.quest_id,
            event_type=event_type,
            summary=summary,
            phase=phase,
            status=status,
            metadata=event_metadata,
        )
        if status in CHECKPOINT_STATUSES:
            self.create_checkpoint(item.quest_id, reason=f"{event_type}:{status}")
        return item

    def record_progress(
        self,
        quest_id: str,
        *,
        phase: str,
        message: str,
        status: str = "active",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[WorkItem]:
        item = self.get_work(quest_id)
        if item is None:
            return None
        status = _normalize_status(status or item.status)
        blocker = item.blocker
        if status == "blocked":
            blocker = message or blocker
        updated = self.upsert_work(
            quest_id=quest_id,
            status=status,
            phase=phase or item.phase,
            next_action=message or item.next_action,
            blocker=blocker,
            metadata=metadata or {},
        )
        self.append_event(
            quest_id=quest_id,
            event_type="progress",
            summary=message,
            phase=phase,
            status=status,
            metadata=metadata or {},
        )
        return updated

    def append_event(
        self,
        *,
        quest_id: str,
        event_type: str,
        summary: str,
        receipt_id: str = "",
        phase: str = "",
        status: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> WorkLedgerEvent:
        quest_id = str(quest_id or "").strip()
        if not quest_id:
            raise ValueError("quest_id is required")
        event = WorkLedgerEvent(
            event_id=str(uuid.uuid4()),
            quest_id=quest_id,
            event_type=_preview(event_type or "event", 80),
            summary=_preview(summary, 1000),
            receipt_id=_preview(receipt_id, 120),
            phase=_preview(phase, 80),
            status=_preview(status, 40),
            created_at=_utc_now(),
            metadata=_bounded_jsonable(metadata or {}),
        )
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO work_ledger_events (
                    event_id, quest_id, event_type, summary, receipt_id, phase,
                    status, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.quest_id,
                    event.event_type,
                    event.summary,
                    event.receipt_id,
                    event.phase,
                    event.status,
                    event.created_at,
                    _dumps(event.metadata),
                ),
            )
        return event

    def get_work(self, quest_id: str) -> Optional[WorkItem]:
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM active_work_items WHERE quest_id = ?",
            (quest_id,),
        ).fetchone()
        return self._row_to_work(row) if row else None

    def list_work(
        self,
        *,
        session_id: str = "",
        operator_id: str = "",
        include_terminal: bool = False,
        limit: int = 25,
    ) -> list[WorkItem]:
        conditions: list[str] = []
        params: list[Any] = []
        if session_id and operator_id:
            conditions.append("(session_id = ? OR operator_id = ?)")
            params.extend([session_id, operator_id])
        elif session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        elif operator_id:
            conditions.append("operator_id = ?")
            params.append(operator_id)
        if not include_terminal:
            conditions.append("status NOT IN ('completed', 'failed', 'cancelled')")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(max(1, min(int(limit), 100)))
        conn = self._get_connection()
        rows = conn.execute(
            f"SELECT * FROM active_work_items {where} ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_work(row) for row in rows]

    def archive_work(
        self,
        quest_id: str,
        *,
        reason: str = "Archived by operator.",
        archived_by_run_id: str = "",
        archived_by_operator_id: str = "",
        archived_by_session_id: str = "",
        status: str = "cancelled",
    ) -> Optional[WorkItem]:
        """Hide an active work item while preserving its events and checkpoints."""
        item = self.get_work(quest_id)
        if item is None:
            return None
        normalized_status = _normalize_status(status)
        if normalized_status not in TERMINAL_STATUSES:
            raise ValueError("Archived work must use a terminal status")

        metadata = {
            "archived": True,
            "archive_reason": _preview(reason, 500),
        }
        if archived_by_run_id:
            metadata["archived_by_run_id"] = _preview(archived_by_run_id, 120)
        if archived_by_operator_id:
            metadata["archived_by_operator_id"] = _preview(archived_by_operator_id, 120)
        if archived_by_session_id:
            metadata["archived_by_session_id"] = _preview(archived_by_session_id, 120)

        merged_metadata = dict(item.metadata)
        merged_metadata.update(metadata)
        now = _utc_now()
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE active_work_items
                   SET status = ?, phase = 'archived', next_action = '',
                       blocker = '', updated_at = ?, metadata = ?
                 WHERE quest_id = ?
                """,
                (normalized_status, now, _dumps(merged_metadata), item.quest_id),
            )
        self.append_event(
            quest_id=item.quest_id,
            event_type="work_archived_by_operator",
            summary=reason,
            phase="archived",
            status=normalized_status,
            metadata=metadata,
        )
        self.create_checkpoint(item.quest_id, reason="work_archived_by_operator")
        return self.get_work(item.quest_id)

    def mark_superseded_by_retry(
        self,
        quest_id: str,
        *,
        retry_run_id: str,
        retry_status: str,
        reason: str = "retry_completed",
    ) -> Optional[WorkItem]:
        """Close a blocked source item once its retained retry reaches a terminal state."""
        item = self.get_work(quest_id)
        if item is None:
            return None
        if item.status in TERMINAL_STATUSES:
            if item.metadata.get("superseded_by_retry_run_id") == retry_run_id:
                return item
            return item

        terminal_status = "completed" if retry_status == "succeeded" else "cancelled"
        summary = (
            f"Work superseded by retry {retry_run_id} "
            f"which finished with chat status {retry_status}."
        )
        metadata = {
            "superseded_by_retry_run_id": _preview(retry_run_id, 120),
            "superseded_by_retry_status": _preview(retry_status, 40),
            "superseded_reason": _preview(reason, 120),
        }
        merged_metadata = dict(item.metadata)
        merged_metadata.update(metadata)
        next_action = f"Superseded by retry {retry_run_id} ({retry_status})."
        now = _utc_now()
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE active_work_items
                   SET status = ?, phase = 'superseded', next_action = ?,
                       blocker = '', updated_at = ?, metadata = ?
                 WHERE quest_id = ?
                """,
                (terminal_status, next_action, now, _dumps(merged_metadata), item.quest_id),
            )
        self.append_event(
            quest_id=item.quest_id,
            event_type="work_superseded_by_retry",
            summary=summary,
            phase="superseded",
            status=terminal_status,
            metadata=metadata,
        )
        self.create_checkpoint(item.quest_id, reason="work_superseded_by_retry")
        return self.get_work(item.quest_id)

    def list_events(self, quest_id: str, *, limit: int = 50) -> list[WorkLedgerEvent]:
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM work_ledger_events
             WHERE quest_id = ?
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (quest_id, max(1, min(int(limit), 200))),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_checkpoints(self, quest_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM work_checkpoints
             WHERE quest_id = ?
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (quest_id, max(1, min(int(limit), 50))),
        ).fetchall()
        return [self._row_to_checkpoint(row) for row in rows]

    def create_checkpoint(
        self,
        quest_id: str,
        *,
        reason: str = "",
        dedupe_window_seconds: int = 0,
    ) -> Optional[dict[str, Any]]:
        item = self.get_work(quest_id)
        if item is None:
            return None

        normalized_reason = _preview(reason or "manual", 200)
        if dedupe_window_seconds > 0:
            latest = self.list_checkpoints(quest_id, limit=1)
            if latest and latest[0].get("reason") == normalized_reason:
                latest_ts = _parse_timestamp(str(latest[0].get("created_at") or ""))
                now_ts = datetime.now(timezone.utc).timestamp()
                if latest_ts is not None and now_ts - latest_ts < dedupe_window_seconds:
                    return latest[0]

        events = list(reversed(self.list_events(quest_id, limit=40)))
        completed_work: list[str] = []
        pending_work: list[str] = []
        open_decisions: list[str] = []
        files_touched: list[str] = []
        approvals: list[str] = []
        receipt_ids: list[str] = []

        for event in events:
            if event.receipt_id and event.receipt_id not in receipt_ids:
                receipt_ids.append(event.receipt_id)
            metadata = event.metadata or {}
            for key in ("path", "file", "target_path"):
                value = metadata.get(key)
                if isinstance(value, str) and value and value not in files_touched:
                    files_touched.append(value)
            approval_id = metadata.get("approval_id") or metadata.get("approval_request_id")
            if approval_id and str(approval_id) not in approvals:
                approvals.append(str(approval_id))
            status = event.status or item.status
            if status == "completed" or "completed" in event.event_type or "success" in event.summary.lower():
                completed_work.append(event.summary)

        if item.status in {"active", "checkpointed"} and item.next_action:
            pending_work.append(item.next_action)
        if item.status == "blocked":
            if item.blocker:
                open_decisions.append(item.blocker)
            if item.next_action:
                pending_work.append(item.next_action)
        if item.status == "failed":
            pending_work.append(item.next_action or "Review failure and decide whether to retry.")
            if item.blocker:
                open_decisions.append(item.blocker)

        summary = (
            f"{item.objective or item.quest_id} | status={item.status} "
            f"phase={item.phase}"
        )
        checkpoint = {
            "checkpoint_id": str(uuid.uuid4()),
            "quest_id": item.quest_id,
            "reason": normalized_reason,
            "summary": _preview(summary, 1000),
            "completed_work": completed_work[-12:],
            "pending_work": list(dict.fromkeys(pending_work))[-12:],
            "open_decisions": list(dict.fromkeys(open_decisions))[-12:],
            "files_touched": files_touched[-30:],
            "approvals": approvals[-20:],
            "receipt_ids": receipt_ids[-40:],
            "created_at": _utc_now(),
        }
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO work_checkpoints (
                    checkpoint_id, quest_id, reason, summary, completed_work,
                    pending_work, open_decisions, files_touched, approvals,
                    receipt_ids, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint["checkpoint_id"],
                    checkpoint["quest_id"],
                    checkpoint["reason"],
                    checkpoint["summary"],
                    _dumps(checkpoint["completed_work"]),
                    _dumps(checkpoint["pending_work"]),
                    _dumps(checkpoint["open_decisions"]),
                    _dumps(checkpoint["files_touched"]),
                    _dumps(checkpoint["approvals"]),
                    _dumps(checkpoint["receipt_ids"]),
                    checkpoint["created_at"],
                ),
            )
        self.append_event(
            quest_id=item.quest_id,
            event_type="checkpoint_created",
            summary=checkpoint["summary"],
            status=item.status,
            phase=item.phase,
            metadata={"checkpoint_id": checkpoint["checkpoint_id"], "reason": checkpoint["reason"]},
        )
        return checkpoint

    def checkpoint_open_work(
        self,
        *,
        reason: str,
        session_id: str = "",
        limit: int = 100,
        dedupe_window_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for item in self.list_work(
            session_id=session_id,
            include_terminal=False,
            limit=limit,
        ):
            before = self.list_checkpoints(item.quest_id, limit=1)
            before_id = before[0]["checkpoint_id"] if before else ""
            checkpoint = self.create_checkpoint(
                item.quest_id,
                reason=reason,
                dedupe_window_seconds=dedupe_window_seconds,
            )
            if checkpoint and checkpoint.get("checkpoint_id") != before_id:
                created.append(checkpoint)
        return created

    def checkpoint_quiet_work(
        self,
        *,
        max_quiet_seconds: int,
        reason: str = "quiet_phase",
        session_id: str = "",
        operator_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        now_ts = datetime.now(timezone.utc).timestamp()
        created: list[dict[str, Any]] = []
        for item in self.list_work(
            session_id=session_id,
            operator_id=operator_id,
            include_terminal=False,
            limit=limit,
        ):
            updated_ts = _parse_timestamp(item.updated_at)
            if updated_ts is None or now_ts - updated_ts < max_quiet_seconds:
                continue
            before = self.list_checkpoints(item.quest_id, limit=1)
            before_id = before[0]["checkpoint_id"] if before else ""
            checkpoint = self.create_checkpoint(
                item.quest_id,
                reason=reason,
                dedupe_window_seconds=max_quiet_seconds,
            )
            if checkpoint and checkpoint.get("checkpoint_id") != before_id:
                created.append(checkpoint)
        return created

    def render_context_block(
        self,
        *,
        quest_id: str = "",
        session_id: str = "",
        operator_id: str = "",
        max_items: int = 3,
        max_events: int = 8,
    ) -> str:
        items: list[WorkItem] = []
        if quest_id:
            item = self.get_work(quest_id)
            if item:
                items.append(item)
        if not items and (session_id or operator_id):
            items = self.list_work(
                session_id=session_id,
                operator_id=operator_id,
                include_terminal=False,
                limit=max_items,
            )
        if not items:
            return ""

        lines = ["=== ACTIVE WORK STATE ==="]
        for index, item in enumerate(items[:max_items], start=1):
            if len(items) > 1:
                lines.append(f"\n[WORK ITEM {index}]")
            lines.append(f"Quest: {item.quest_id}")
            if item.objective:
                lines.append(f"Objective: {_preview(item.objective, 500)}")
            lines.append(f"Status: {item.status}")
            lines.append(f"Phase: {item.phase}")
            if item.current_step:
                lines.append(f"Current Step: {_preview(item.current_step, 300)}")
            if item.blocker:
                lines.append(f"Blocked On: {_preview(item.blocker, 500)}")
            if item.next_action:
                lines.append(f"Next Action: {_preview(item.next_action, 500)}")

            checkpoints = self.list_checkpoints(item.quest_id, limit=1)
            if checkpoints:
                cp = checkpoints[0]
                pending = cp.get("pending_work") or []
                decisions = cp.get("open_decisions") or []
                receipts = cp.get("receipt_ids") or []
                if pending:
                    lines.append("Pending:")
                    lines.extend(f"- {_preview(entry, 300)}" for entry in pending[:5])
                if decisions:
                    lines.append("Open Decisions:")
                    lines.extend(f"- {_preview(entry, 300)}" for entry in decisions[:5])
                if receipts:
                    lines.append("Receipt Evidence:")
                    lines.extend(f"- {receipt_id}" for receipt_id in receipts[-5:])

            events = list(reversed(self.list_events(item.quest_id, limit=max_events)))
            if events:
                lines.append("Recent Ledger Events:")
                for event in events[-max_events:]:
                    prefix = event.phase or event.event_type
                    lines.append(f"- {_preview(prefix, 80)}: {_preview(event.summary, 300)}")
        return "\n".join(lines)

    def _row_to_work(self, row: sqlite3.Row) -> WorkItem:
        return WorkItem(
            quest_id=row["quest_id"],
            session_id=row["session_id"],
            operator_id=row["operator_id"],
            channel=row["channel"],
            objective=row["objective"],
            status=row["status"],
            phase=row["phase"],
            current_step=row["current_step"],
            next_action=row["next_action"],
            blocker=row["blocker"],
            last_chat_run_id=row["last_chat_run_id"],
            last_task_run_id=row["last_task_run_id"],
            last_receipt_id=row["last_receipt_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_loads(row["metadata"], {}),
        )

    def _row_to_event(self, row: sqlite3.Row) -> WorkLedgerEvent:
        return WorkLedgerEvent(
            event_id=row["event_id"],
            quest_id=row["quest_id"],
            event_type=row["event_type"],
            summary=row["summary"],
            receipt_id=row["receipt_id"],
            phase=row["phase"],
            status=row["status"],
            created_at=row["created_at"],
            metadata=_loads(row["metadata"], {}),
        )

    def _row_to_checkpoint(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "checkpoint_id": row["checkpoint_id"],
            "quest_id": row["quest_id"],
            "reason": row["reason"],
            "summary": row["summary"],
            "completed_work": _loads(row["completed_work"], []),
            "pending_work": _loads(row["pending_work"], []),
            "open_decisions": _loads(row["open_decisions"], []),
            "files_touched": _loads(row["files_touched"], []),
            "approvals": _loads(row["approvals"], []),
            "receipt_ids": _loads(row["receipt_ids"], []),
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            self._local.connection = None
