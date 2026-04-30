"""
DecisionLog — append-only journal of all approve/deny decisions.

Persists to JSONL (one JSON object per line). Never modifies or deletes
existing lines. Thread-safe via threading lock.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from src.core.governance.approval_learning.config import APLConfig
from src.core.governance.approval_learning.models import (
    DecisionContext,
    DecisionRecord,
    RiskTier,
)

logger = logging.getLogger(__name__)


class DecisionLog:
    """Append-only journal of owner approve/deny decisions."""

    def __init__(self, config: APLConfig):
        self._config = config
        self._records: List[DecisionRecord] = []
        self._lock = threading.Lock()
        self._decisions_since_analysis = 0
        self._load()

    def record(
        self,
        context: DecisionContext,
        decision: str,
        decision_time_ms: int = 0,
        reason: str = "",
        rule_id: str = "",
    ) -> DecisionRecord:
        """Append a new decision. Persist immediately."""
        rec = DecisionRecord(
            id=str(uuid.uuid4()),
            context=context,
            decision=decision,
            decision_time_ms=decision_time_ms,
            reason=reason,
            rule_id=rule_id,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._records.append(rec)
            self._decisions_since_analysis += 1
            self._persist(rec)
        return rec

    def get_recent(self, n: int = 100) -> List[DecisionRecord]:
        """Last N decisions, newest first."""
        with self._lock:
            return list(reversed(self._records[-n:]))

    def get_window(self, days: int = 30) -> List[DecisionRecord]:
        """Decisions within the last N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        with self._lock:
            return [r for r in self._records if r.recorded_at >= cutoff_iso]

    def get_by_capability(self, capability: str) -> List[DecisionRecord]:
        """All decisions for a specific capability."""
        with self._lock:
            return [r for r in self._records if r.context.capability == capability]

    def get_by_target_domain(self, domain: str) -> List[DecisionRecord]:
        """All decisions targeting a specific domain."""
        with self._lock:
            return [r for r in self._records if r.context.target_domain == domain]

    def count_since_last_analysis(self) -> int:
        """How many decisions since the last pattern analysis."""
        with self._lock:
            return self._decisions_since_analysis

    def mark_analysis_complete(self) -> None:
        """Record that pattern analysis was just run."""
        with self._lock:
            self._decisions_since_analysis = 0

    @property
    def total_decisions(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def total_approvals(self) -> int:
        with self._lock:
            return sum(1 for r in self._records if r.decision == "approved")

    @property
    def total_denials(self) -> int:
        with self._lock:
            return sum(1 for r in self._records if r.decision == "denied")

    @property
    def auto_approved_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records if r.rule_id != "")

    def _persist(self, record: DecisionRecord) -> None:
        """Append single record as JSON line to JSONL file."""
        path = Path(self._config.persistence.decision_log_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "id": record.id,
                "context": {
                    "capability": record.context.capability,
                    "operation_id": record.context.operation_id,
                    "connector_id": record.context.connector_id,
                    "risk_tier": int(record.context.risk_tier),
                    "target": record.context.target,
                    "target_domain": record.context.target_domain,
                    "target_category": record.context.target_category,
                    "scope": record.context.scope,
                    "timestamp": record.context.timestamp,
                    "day_of_week": record.context.day_of_week,
                    "hour_of_day": record.context.hour_of_day,
                    "content_hash": record.context.content_hash,
                    "content_size": record.context.content_size,
                    "metadata": record.context.metadata,
                },
                "decision": record.decision,
                "decision_time_ms": record.decision_time_ms,
                "reason": record.reason,
                "rule_id": record.rule_id,
                "recorded_at": record.recorded_at,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.error("Failed to persist decision record: %s", e)

    def _load(self) -> None:
        """Load existing records from JSONL file."""
        path = Path(self._config.persistence.decision_log_path)
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    ctx_data = data["context"]
                    context = DecisionContext(
                        capability=ctx_data["capability"],
                        operation_id=ctx_data["operation_id"],
                        connector_id=ctx_data["connector_id"],
                        risk_tier=RiskTier(ctx_data["risk_tier"]),
                        target=ctx_data["target"],
                        target_domain=ctx_data["target_domain"],
                        target_category=ctx_data.get("target_category", ""),
                        scope=ctx_data.get("scope", ""),
                        timestamp=ctx_data["timestamp"],
                        day_of_week=ctx_data["day_of_week"],
                        hour_of_day=ctx_data["hour_of_day"],
                        content_hash=ctx_data.get("content_hash", ""),
                        content_size=ctx_data.get("content_size", 0),
                        metadata=ctx_data.get("metadata", {}),
                    )
                    rec = DecisionRecord(
                        id=data["id"],
                        context=context,
                        decision=data["decision"],
                        decision_time_ms=data.get("decision_time_ms", 0),
                        reason=data.get("reason", ""),
                        rule_id=data.get("rule_id", ""),
                        recorded_at=data.get("recorded_at", ""),
                    )
                    self._records.append(rec)

            self._decisions_since_analysis = len(self._records)
        except Exception as e:
            logger.error("Failed to load decision log: %s", e)
