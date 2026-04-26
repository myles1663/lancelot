"""
Persistent run state for asynchronous Command Center chat execution.

The chat run store is intentionally separate from TaskRun. TaskRun represents
compiled executable plans; ChatRun represents an operator-facing chat turn that
may plan, request approval, execute tools, or answer directly.
"""

from __future__ import annotations

import os
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

TERMINAL_STATUSES = {"blocked", "succeeded", "failed", "cancelled"}
RETRYABLE_STATUSES = {"failed", "cancelled", "blocked"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _parse_timestamp(value: str | None) -> Optional[float]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _preview(value: str, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _load_progress_events(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        events: list[dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict):
                events.append(item)
        return events
    except Exception:
        return []


def _dump_progress_events(events: list[dict[str, Any]]) -> str:
    bounded = events[-100:]
    return json.dumps(bounded, separators=(",", ":"), sort_keys=True)


def _bounded_progress_metadata(
    *,
    severity: str | None = None,
    degraded: bool | None = None,
    degraded_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_metadata: dict[str, Any] = {}
    normalized_severity = str(severity or "").strip().lower()
    if normalized_severity in {"info", "warning", "error"}:
        event_metadata["severity"] = normalized_severity
    if degraded is not None:
        event_metadata["degraded"] = bool(degraded)
    if degraded_reason:
        event_metadata["degraded_reason"] = _preview(degraded_reason, limit=240)
    for key, value in (metadata or {}).items():
        if key in {"severity", "degraded", "degraded_reason"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            event_metadata[str(key)] = _preview(str(value), limit=160) if isinstance(value, str) else value
    return event_metadata


def _elapsed_ms(start: str | None, end: str | None) -> Optional[int]:
    start_ts = _parse_timestamp(start)
    end_ts = _parse_timestamp(end)
    if start_ts is None or end_ts is None:
        return None
    return max(0, int((end_ts - start_ts) * 1000))


def _phase_timings_ms(
    events: list[dict[str, Any]],
    *,
    created_at: str,
    updated_at: str,
    completed_at: str | None,
) -> dict[str, int]:
    if not events:
        return {}

    end_elapsed = _elapsed_ms(created_at, completed_at or updated_at)
    if end_elapsed is None:
        return {}

    timings: dict[str, int] = {}
    current_phase: str | None = None
    current_elapsed = 0
    for event in sorted(events, key=lambda item: int(item.get("elapsed_ms") or 0)):
        phase = _preview(str(event.get("phase") or "processing"), limit=80)
        event_elapsed = max(0, int(event.get("elapsed_ms") or 0))
        if current_phase is not None:
            timings[current_phase] = timings.get(current_phase, 0) + max(
                0, event_elapsed - current_elapsed
            )
        current_phase = phase
        current_elapsed = event_elapsed

    if current_phase is not None:
        timings[current_phase] = timings.get(current_phase, 0) + max(
            0, end_elapsed - current_elapsed
        )
    return timings


@dataclass
class ChatRun:
    run_id: str
    request_id: str
    status: str
    user: str
    channel: str
    session_id: str
    operator_id: str
    message_preview: str
    message_text: str = ""
    response: str = ""
    error: str = ""
    phase: str = "queued"
    created_at: str = ""
    updated_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    crusader_mode: bool = False
    retry_of_run_id: str = ""
    retry_count: int = 0
    cancel_requested: bool = False
    cancel_reason: str = ""
    cancelled_at: Optional[str] = None
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    phase_timings_ms: dict[str, int] = field(default_factory=dict)
    total_elapsed_ms: Optional[int] = None
    last_progress_message: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "status": self.status,
            "user": self.user,
            "channel": self.channel,
            "session_id": self.session_id,
            "operator_id": self.operator_id,
            "message_preview": self.message_preview,
            "response": self.response,
            "error": self.error,
            "phase": self.phase,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "crusader_mode": self.crusader_mode,
            "retry_of_run_id": self.retry_of_run_id,
            "retry_count": self.retry_count,
            "cancel_requested": self.cancel_requested,
            "cancel_reason": self.cancel_reason,
            "cancelled_at": self.cancelled_at,
            "progress_events": self.progress_events,
            "phase_timings_ms": self.phase_timings_ms,
            "total_elapsed_ms": self.total_elapsed_ms,
            "last_progress_message": self.last_progress_message,
        }


class ChatRunStore:
    """SQLite-backed status store for async chat runs."""

    CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS chat_runs (
        run_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        status TEXT NOT NULL,
        user TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL DEFAULT 'warroom',
        session_id TEXT NOT NULL DEFAULT '',
        operator_id TEXT NOT NULL DEFAULT '',
        message_preview TEXT NOT NULL DEFAULT '',
        message_text TEXT NOT NULL DEFAULT '',
        response TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        phase TEXT NOT NULL DEFAULT 'queued',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        crusader_mode INTEGER NOT NULL DEFAULT 0,
        retry_of_run_id TEXT NOT NULL DEFAULT '',
        retry_count INTEGER NOT NULL DEFAULT 0,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        cancel_reason TEXT NOT NULL DEFAULT '',
        cancelled_at TEXT,
        progress_events TEXT NOT NULL DEFAULT '[]'
    );

    CREATE INDEX IF NOT EXISTS idx_chat_runs_status ON chat_runs(status);
    CREATE INDEX IF NOT EXISTS idx_chat_runs_session ON chat_runs(session_id);
    CREATE INDEX IF NOT EXISTS idx_chat_runs_created ON chat_runs(created_at);
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
    def _transaction(self):
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
                for row in conn.execute("PRAGMA table_info(chat_runs)").fetchall()
            }
            if "progress_events" not in columns:
                conn.execute(
                    "ALTER TABLE chat_runs "
                    "ADD COLUMN progress_events TEXT NOT NULL DEFAULT '[]'"
                )
            migrations = {
                "message_text": "ALTER TABLE chat_runs ADD COLUMN message_text TEXT NOT NULL DEFAULT ''",
                "retry_of_run_id": "ALTER TABLE chat_runs ADD COLUMN retry_of_run_id TEXT NOT NULL DEFAULT ''",
                "retry_count": "ALTER TABLE chat_runs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
                "cancel_requested": "ALTER TABLE chat_runs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0",
                "cancel_reason": "ALTER TABLE chat_runs ADD COLUMN cancel_reason TEXT NOT NULL DEFAULT ''",
                "cancelled_at": "ALTER TABLE chat_runs ADD COLUMN cancelled_at TEXT",
            }
            for column, sql in migrations.items():
                if column not in columns:
                    conn.execute(sql)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_runs_retry_of ON chat_runs(retry_of_run_id)"
            )

    def create(
        self,
        *,
        request_id: str,
        user: str,
        channel: str,
        session_id: str,
        operator_id: str,
        message: str,
    ) -> ChatRun:
        now = _utc_now()
        run = ChatRun(
            run_id=str(uuid.uuid4()),
            request_id=request_id,
            status="queued",
            user=user or "",
            channel=channel or "warroom",
            session_id=session_id or "",
            operator_id=operator_id or "",
            message_preview=_preview(message),
            message_text=message or "",
            created_at=now,
            updated_at=now,
        )
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO chat_runs (
                    run_id, request_id, status, user, channel, session_id,
                    operator_id, message_preview, message_text, response, error, phase,
                    created_at, updated_at, started_at, completed_at, crusader_mode,
                    retry_of_run_id, retry_count, cancel_requested, cancel_reason,
                    cancelled_at, progress_events
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.request_id,
                    run.status,
                    run.user,
                    run.channel,
                    run.session_id,
                    run.operator_id,
                    run.message_preview,
                    run.message_text,
                    run.response,
                    run.error,
                    run.phase,
                    run.created_at,
                    run.updated_at,
                    run.started_at,
                    run.completed_at,
                    int(run.crusader_mode),
                    run.retry_of_run_id,
                    run.retry_count,
                    int(run.cancel_requested),
                    run.cancel_reason,
                    run.cancelled_at,
                    _dump_progress_events(run.progress_events),
                ),
            )
        return run

    def get(self, run_id: str) -> Optional[ChatRun]:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM chat_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list_recent(self, *, limit: int = 25, session_id: str = "") -> list[ChatRun]:
        query = "SELECT * FROM chat_runs"
        params: list[object] = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        conn = self._get_connection()
        return [self._row_to_run(row) for row in conn.execute(query, params).fetchall()]

    def list_terminal_retries(self, *, limit: int = 200) -> list[ChatRun]:
        """Return recent retry runs that reached an operator-visible terminal state."""
        safe_limit = max(1, min(int(limit), 1000))
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM chat_runs
             WHERE retry_of_run_id != ''
               AND status IN ('blocked', 'succeeded', 'failed', 'cancelled')
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def mark_running(self, run_id: str) -> Optional[ChatRun]:
        now = _utc_now()
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE chat_runs
                   SET status = 'running', phase = 'executing',
                       started_at = COALESCE(started_at, ?), updated_at = ?
                 WHERE run_id = ? AND status = 'queued'
                """,
                (now, now, run_id),
            )
        return self.get(run_id)

    def record_progress(
        self,
        run_id: str,
        *,
        phase: str,
        message: str,
        event_timestamp: float | None = None,
        severity: str | None = None,
        degraded: bool | None = None,
        degraded_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Optional[ChatRun]:
        event_at = _utc_from_timestamp(event_timestamp) if event_timestamp else _utc_now()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT created_at, status, progress_events FROM chat_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            if row["status"] in TERMINAL_STATUSES:
                return self.get(run_id)

            elapsed = _elapsed_ms(row["created_at"], event_at) or 0
            events = _load_progress_events(row["progress_events"])
            event = {
                "phase": _preview(phase or "processing", limit=80),
                "message": _preview(message or "Processing request", limit=240),
                "at": event_at,
                "elapsed_ms": elapsed,
            }
            event.update(_bounded_progress_metadata(
                severity=severity,
                degraded=degraded,
                degraded_reason=degraded_reason,
                metadata=metadata,
            ))
            if not events or (
                events[-1].get("phase") != event["phase"]
                or events[-1].get("message") != event["message"]
                or events[-1].get("severity") != event.get("severity")
                or events[-1].get("degraded") != event.get("degraded")
                or events[-1].get("degraded_reason") != event.get("degraded_reason")
            ):
                events.append(event)
            else:
                events[-1] = event

            conn.execute(
                """
                UPDATE chat_runs
                   SET phase = ?, progress_events = ?, updated_at = ?
                 WHERE run_id = ?
                """,
                (event["phase"], _dump_progress_events(events), event_at, run_id),
            )
        return self.get(run_id)

    def complete(
        self,
        run_id: str,
        *,
        status: str,
        response: str = "",
        error: str = "",
        crusader_mode: bool = False,
    ) -> Optional[ChatRun]:
        now = _utc_now()
        if status == "blocked":
            phase = "blocked"
        elif status == "succeeded":
            phase = "completed"
        elif status == "cancelled":
            phase = "cancelled"
        else:
            phase = "failed"
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE chat_runs
                   SET status = ?, phase = ?, response = ?, error = ?,
                       completed_at = ?, updated_at = ?, crusader_mode = ?
                 WHERE run_id = ? AND status != 'cancelled'
                """,
                (status, phase, response or "", error or "", now, now, int(crusader_mode), run_id),
            )
        return self.get(run_id)

    def fail(self, run_id: str, error: str) -> Optional[ChatRun]:
        return self.complete(run_id, status="failed", error=error)

    def request_cancel(self, run_id: str, *, reason: str) -> Optional[ChatRun]:
        now = _utc_now()
        bounded_reason = _preview(reason or "Cancelled by operator from Command Center.", limit=500)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT status FROM chat_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            if row["status"] in {"succeeded", "failed", "cancelled"}:
                return self.get(run_id)

            conn.execute(
                """
                UPDATE chat_runs
                   SET status = 'cancelled',
                       phase = 'cancelled',
                       error = ?,
                       cancel_requested = 1,
                       cancel_reason = ?,
                       cancelled_at = ?,
                       completed_at = COALESCE(completed_at, ?),
                       updated_at = ?
                 WHERE run_id = ?
                """,
                (bounded_reason, bounded_reason, now, now, now, run_id),
            )
        return self.get(run_id)

    def create_retry(
        self,
        run_id: str,
        *,
        request_id: str,
        session_id: str,
        operator_id: str,
    ) -> Optional[ChatRun]:
        original = self.get(run_id)
        if original is None:
            return None
        if original.status not in RETRYABLE_STATUSES:
            raise ValueError(
                "Only failed, cancelled, or blocked chat runs can be retried; "
                f"current status is {original.status}."
            )
        if not original.message_text:
            raise ValueError(
                f"Chat run {run_id} cannot be retried because the original message body was not retained."
            )

        retry = self.create(
            request_id=request_id,
            user=original.user,
            channel=original.channel,
            session_id=session_id or original.session_id,
            operator_id=operator_id or original.operator_id,
            message=original.message_text,
        )
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE chat_runs
                   SET retry_of_run_id = ?, retry_count = ?
                 WHERE run_id = ?
                """,
                (original.run_id, original.retry_count + 1, retry.run_id),
            )
        return self.get(retry.run_id)

    def fail_stale_active_runs(self, *, max_age_seconds: int, reason: str) -> list[ChatRun]:
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - max_age_seconds,
            timezone.utc,
        ).isoformat()
        now = _utc_now()
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT run_id FROM chat_runs
                 WHERE status IN ('queued', 'running')
                   AND created_at <= ?
                """,
                (cutoff,),
            ).fetchall()
            run_ids = [row["run_id"] for row in rows]
            for run_id in run_ids:
                conn.execute(
                    """
                    UPDATE chat_runs
                       SET status = 'failed', phase = 'failed',
                           error = ?, completed_at = ?, updated_at = ?
                     WHERE run_id = ?
                    """,
                    (reason, now, now, run_id),
                )
        return [run for run_id in run_ids if (run := self.get(run_id)) is not None]

    def _row_to_run(self, row: sqlite3.Row) -> ChatRun:
        progress_events = _load_progress_events(row["progress_events"])
        return ChatRun(
            run_id=row["run_id"],
            request_id=row["request_id"],
            status=row["status"],
            user=row["user"],
            channel=row["channel"],
            session_id=row["session_id"],
            operator_id=row["operator_id"],
            message_preview=row["message_preview"],
            message_text=row["message_text"],
            response=row["response"],
            error=row["error"],
            phase=row["phase"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            crusader_mode=bool(row["crusader_mode"]),
            retry_of_run_id=row["retry_of_run_id"],
            retry_count=int(row["retry_count"] or 0),
            cancel_requested=bool(row["cancel_requested"]),
            cancel_reason=row["cancel_reason"],
            cancelled_at=row["cancelled_at"],
            progress_events=progress_events,
            phase_timings_ms=_phase_timings_ms(
                progress_events,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
            ),
            total_elapsed_ms=_elapsed_ms(
                row["created_at"],
                row["completed_at"] or row["updated_at"],
            ),
            last_progress_message=(
                str(progress_events[-1].get("message") or "") if progress_events else ""
            ),
        )

    def close(self) -> None:
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            self._local.connection = None
