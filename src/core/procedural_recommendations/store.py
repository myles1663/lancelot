"""SQLite persistence for procedural recommendations.

The store owns recommendation lifecycle state. Receipts remain the immutable
audit log; this database gives War Room and fatigue/suppression logic a current
operator-facing view.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PENDING = "pending"
ACCEPTED = "accepted"
DISMISSED = "dismissed"
SNOOZED = "snoozed"
CONVERTED_TO_SOP = "converted_to_sop"

TERMINAL_STATUSES = {ACCEPTED, DISMISSED, CONVERTED_TO_SOP}


@dataclass(frozen=True)
class RecommendationRecord:
    recommendation_id: str
    fingerprint: str
    category: str
    title: str
    observation: str
    risk_or_opportunity: str
    recommendation: str
    suggested_action: str
    score: int
    score_breakdown: dict[str, Any]
    evidence: list[str]
    delivery_mode: str
    status: str
    user_response: str
    created_at: float
    updated_at: float
    snoozed_until: float | None = None
    quest_id: str = ""
    session_id: str = ""
    operator_id: str = ""
    channel: str = ""
    source_receipt_id: str = ""
    actioncard_id: str = ""
    sop_draft_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "fingerprint": self.fingerprint,
            "category": self.category,
            "title": self.title,
            "observation": self.observation,
            "risk_or_opportunity": self.risk_or_opportunity,
            "recommendation": self.recommendation,
            "suggested_action": self.suggested_action,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "evidence": self.evidence,
            "delivery_mode": self.delivery_mode,
            "status": self.status,
            "user_response": self.user_response,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "snoozed_until": self.snoozed_until,
            "quest_id": self.quest_id,
            "session_id": self.session_id,
            "operator_id": self.operator_id,
            "channel": self.channel,
            "source_receipt_id": self.source_receipt_id,
            "actioncard_id": self.actioncard_id,
            "sop_draft_path": self.sop_draft_path,
        }


def recommendation_fingerprint(category: str, title: str, recommendation: str) -> str:
    normalized = " ".join(f"{category} {title} {recommendation}".lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _slug(value: str, limit: int = 64) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (text[:limit].strip("-") or "procedural-recommendation")


class ProceduralRecommendationStore:
    """SQLite-backed current-state store for recommendations."""

    CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS procedural_recommendations (
        recommendation_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL,
        title TEXT NOT NULL,
        observation TEXT NOT NULL,
        risk_or_opportunity TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        suggested_action TEXT NOT NULL DEFAULT '',
        score INTEGER NOT NULL,
        score_breakdown TEXT NOT NULL DEFAULT '{}',
        evidence TEXT NOT NULL DEFAULT '[]',
        delivery_mode TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        user_response TEXT NOT NULL DEFAULT 'pending',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        snoozed_until REAL,
        quest_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        operator_id TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL DEFAULT '',
        source_receipt_id TEXT NOT NULL DEFAULT '',
        actioncard_id TEXT NOT NULL DEFAULT '',
        sop_draft_path TEXT NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_pr_status ON procedural_recommendations(status);
    CREATE INDEX IF NOT EXISTS idx_pr_category ON procedural_recommendations(category);
    CREATE INDEX IF NOT EXISTS idx_pr_operator ON procedural_recommendations(operator_id);
    CREATE INDEX IF NOT EXISTS idx_pr_updated ON procedural_recommendations(updated_at);

    CREATE TABLE IF NOT EXISTS procedural_recommendation_actions (
        action_id TEXT PRIMARY KEY,
        recommendation_id TEXT NOT NULL,
        action TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        operator_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );

    CREATE INDEX IF NOT EXISTS idx_pr_actions_recommendation
        ON procedural_recommendation_actions(recommendation_id);
    CREATE INDEX IF NOT EXISTS idx_pr_actions_category_time
        ON procedural_recommendation_actions(action, created_at);
    """

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self.data_dir = data_dir
        self.storage_dir = os.path.join(data_dir, "procedural_recommendations")
        self.db_path = os.path.join(self.storage_dir, "procedural_recommendations.db")
        self._local = threading.local()
        os.makedirs(self.storage_dir, exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0,
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
        return self._local.connection

    def _init_database(self) -> None:
        conn = self._get_connection()
        conn.executescript(self.CREATE_SQL)
        conn.commit()

    def upsert_candidate(
        self,
        *,
        category: str,
        title: str,
        observation: str,
        risk_or_opportunity: str,
        recommendation: str,
        suggested_action: str,
        score: int,
        score_breakdown: dict[str, Any],
        evidence: list[str],
        delivery_mode: str,
        quest_id: str = "",
        session_id: str = "",
        operator_id: str = "",
        channel: str = "",
        source_receipt_id: str = "",
    ) -> RecommendationRecord:
        fingerprint = recommendation_fingerprint(category, title, recommendation)
        now = time.time()
        conn = self._get_connection()
        existing = self.get_by_fingerprint(fingerprint)

        if existing is None:
            rec_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO procedural_recommendations (
                    recommendation_id, fingerprint, category, title, observation,
                    risk_or_opportunity, recommendation, suggested_action, score,
                    score_breakdown, evidence, delivery_mode, status, user_response,
                    created_at, updated_at, quest_id, session_id, operator_id,
                    channel, source_receipt_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec_id,
                    fingerprint,
                    category,
                    title,
                    observation,
                    risk_or_opportunity,
                    recommendation,
                    suggested_action,
                    int(score),
                    json.dumps(score_breakdown, sort_keys=True),
                    json.dumps(list(evidence)),
                    delivery_mode,
                    PENDING,
                    "pending",
                    now,
                    now,
                    quest_id,
                    session_id,
                    operator_id,
                    channel,
                    source_receipt_id,
                ),
            )
            conn.commit()
            return self.get(rec_id)  # type: ignore[return-value]

        should_reactivate = (
            existing.status == SNOOZED
            and existing.snoozed_until is not None
            and existing.snoozed_until <= now
        ) or (
            existing.status == DISMISSED
            and existing.updated_at <= now - 7 * 86400
        )

        if existing.status == PENDING or should_reactivate:
            status = PENDING
            user_response = "pending"
            snoozed_until = None
            conn.execute(
                """
                UPDATE procedural_recommendations
                   SET score = ?, score_breakdown = ?, evidence = ?,
                       delivery_mode = ?, status = ?, user_response = ?,
                       snoozed_until = ?, actioncard_id = ?, updated_at = ?, quest_id = ?,
                       session_id = ?, operator_id = ?, channel = ?,
                       source_receipt_id = COALESCE(NULLIF(?, ''), source_receipt_id)
                 WHERE recommendation_id = ?
                """,
                (
                    int(score),
                    json.dumps(score_breakdown, sort_keys=True),
                    json.dumps(list(evidence)),
                    delivery_mode,
                    status,
                    user_response,
                    snoozed_until,
                    "",
                    now,
                    quest_id,
                    session_id,
                    operator_id,
                    channel,
                    source_receipt_id,
                    existing.recommendation_id,
                ),
            )
            conn.commit()
            return self.get(existing.recommendation_id)  # type: ignore[return-value]

        return existing

    def get(self, recommendation_id: str) -> RecommendationRecord | None:
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM procedural_recommendations WHERE recommendation_id = ?",
            (recommendation_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_fingerprint(self, fingerprint: str) -> RecommendationRecord | None:
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM procedural_recommendations WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def list(
        self,
        *,
        status: str = PENDING,
        category: str | None = None,
        operator_id: str | None = None,
        limit: int = 50,
    ) -> list[RecommendationRecord]:
        query = "SELECT * FROM procedural_recommendations WHERE 1=1"
        params: list[Any] = []
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)
        if operator_id:
            query += " AND (operator_id = ? OR operator_id = '')"
            params.append(operator_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        rows = self._get_connection().execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def should_suppress(
        self,
        *,
        category: str,
        title: str,
        recommendation: str,
        operator_id: str = "",
        now: float | None = None,
    ) -> bool:
        now = now or time.time()
        fingerprint = recommendation_fingerprint(category, title, recommendation)
        existing = self.get_by_fingerprint(fingerprint)

        if existing is not None:
            if existing.status == PENDING:
                return True
            if existing.status == SNOOZED and existing.snoozed_until and existing.snoozed_until > now:
                return True
            if existing.status == DISMISSED and existing.updated_at > now - 7 * 86400:
                return True

        # Category-level fatigue: repeated dismiss/snooze by the same operator
        # should silence that category for a day.
        since = now - 86400
        conn = self._get_connection()
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
              FROM procedural_recommendation_actions a
              JOIN procedural_recommendations r
                ON r.recommendation_id = a.recommendation_id
             WHERE r.category = ?
               AND a.action IN ('dismiss', 'snooze')
               AND a.created_at >= ?
               AND (? = '' OR a.operator_id = ?)
            """,
            (category, since, operator_id, operator_id),
        ).fetchone()
        return int(row["count"] if row else 0) >= 2

    def set_actioncard_id(self, recommendation_id: str, actioncard_id: str) -> None:
        conn = self._get_connection()
        conn.execute(
            """
            UPDATE procedural_recommendations
               SET actioncard_id = ?, updated_at = ?
             WHERE recommendation_id = ?
            """,
            (actioncard_id, time.time(), recommendation_id),
        )
        conn.commit()

    def record_action(
        self,
        recommendation_id: str,
        action: str,
        *,
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
        channel: str = "",
        reason: str = "",
        snooze_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RecommendationRecord:
        record = self.get(recommendation_id)
        if record is None:
            raise KeyError(f"Recommendation not found: {recommendation_id}")

        now = time.time()
        status = {
            "accept": ACCEPTED,
            "useful": ACCEPTED,
            "dismiss": DISMISSED,
            "snooze": SNOOZED,
            "make_sop": CONVERTED_TO_SOP,
        }.get(action, action)
        snoozed_until = now + int(snooze_seconds or 0) if status == SNOOZED else None

        conn = self._get_connection()
        conn.execute(
            """
            UPDATE procedural_recommendations
               SET status = ?, user_response = ?, updated_at = ?, snoozed_until = ?
             WHERE recommendation_id = ?
            """,
            (status, action, now, snoozed_until, recommendation_id),
        )
        conn.execute(
            """
            INSERT INTO procedural_recommendation_actions (
                action_id, recommendation_id, action, reason, operator_id,
                session_id, actor, channel, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                recommendation_id,
                action,
                reason,
                operator_id,
                session_id,
                actor,
                channel,
                now,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        conn.commit()
        return self.get(recommendation_id)  # type: ignore[return-value]

    def convert_to_sop_draft(
        self,
        recommendation_id: str,
        *,
        operator_id: str = "",
        session_id: str = "",
        actor: str = "",
        channel: str = "",
    ) -> RecommendationRecord:
        record = self.record_action(
            recommendation_id,
            "make_sop",
            operator_id=operator_id,
            session_id=session_id,
            actor=actor,
            channel=channel,
        )
        drafts_dir = Path(self.storage_dir) / "sop_drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{_slug(record.title)}-{record.recommendation_id[:8]}.md"
        path = drafts_dir / filename
        content = _render_sop_draft(record)
        path.write_text(content, encoding="utf-8")

        conn = self._get_connection()
        conn.execute(
            """
            UPDATE procedural_recommendations
               SET sop_draft_path = ?, updated_at = ?
             WHERE recommendation_id = ?
            """,
            (str(path), time.time(), recommendation_id),
        )
        conn.commit()
        return self.get(recommendation_id)  # type: ignore[return-value]

    def stats(self, *, operator_id: str | None = None) -> dict[str, Any]:
        conn = self._get_connection()
        where = ""
        params: list[Any] = []
        if operator_id:
            where = " WHERE operator_id = ? OR operator_id = ''"
            params.append(operator_id)
        by_status = {
            row["status"]: int(row["count"])
            for row in conn.execute(
                f"SELECT status, COUNT(*) AS count FROM procedural_recommendations{where} GROUP BY status",
                params,
            ).fetchall()
        }
        by_category = {
            row["category"]: int(row["count"])
            for row in conn.execute(
                f"SELECT category, COUNT(*) AS count FROM procedural_recommendations{where} GROUP BY category",
                params,
            ).fetchall()
        }
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_category": by_category,
        }

    def _row_to_record(self, row: sqlite3.Row) -> RecommendationRecord:
        return RecommendationRecord(
            recommendation_id=row["recommendation_id"],
            fingerprint=row["fingerprint"],
            category=row["category"],
            title=row["title"],
            observation=row["observation"],
            risk_or_opportunity=row["risk_or_opportunity"],
            recommendation=row["recommendation"],
            suggested_action=row["suggested_action"],
            score=int(row["score"]),
            score_breakdown=json.loads(row["score_breakdown"] or "{}"),
            evidence=json.loads(row["evidence"] or "[]"),
            delivery_mode=row["delivery_mode"],
            status=row["status"],
            user_response=row["user_response"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            snoozed_until=row["snoozed_until"],
            quest_id=row["quest_id"],
            session_id=row["session_id"],
            operator_id=row["operator_id"],
            channel=row["channel"],
            source_receipt_id=row["source_receipt_id"],
            actioncard_id=row["actioncard_id"],
            sop_draft_path=row["sop_draft_path"],
        )

    def close(self) -> None:
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None


def _render_sop_draft(record: RecommendationRecord) -> str:
    evidence = "\n".join(f"- {item}" for item in record.evidence) or "- No evidence captured."
    return f"""# SOP Draft: {record.title}

## Trigger

Use this SOP when {record.observation}

## Risk Or Opportunity

{record.risk_or_opportunity}

## Recommended Operating Pattern

{record.recommendation}

## Owner

TBD

## Steps

1. Confirm the trigger applies.
2. Identify the owner and affected work surface.
3. Apply the recommended operating pattern.
4. Validate the result.
5. Record any exceptions or follow-up work.

## Validation

- Score at recommendation time: {record.score}
- Category: {record.category}

## Evidence

{evidence}

## Rollback Or Fallback

Pause the procedural change if it adds more interruption than value, then revisit the recommendation with more evidence.
"""
