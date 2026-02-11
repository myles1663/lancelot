"""
Lancelot vNext4: Batch Receipt System

Reduces I/O overhead for T0/T1 actions by collecting receipts in a buffer
and flushing as a single JSON artifact. Supports tier-boundary flush and
context manager for safe cleanup.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .config import BatchReceiptConfig
from .models import RiskTier


# ── Data Models ──────────────────────────────────────────────────

@dataclass
class ReceiptEntry:
    """A single action entry within a batch receipt."""
    entry_index: int
    timestamp: str
    capability: str
    tool_id: str
    risk_tier: int
    input_hash: str
    output_hash: str
    success: bool
    error: Optional[str] = None
    verification_status: str = "skipped"


@dataclass
class BatchSummary:
    """Aggregated stats for a batch receipt."""
    total_actions: int
    succeeded: int
    failed: int
    highest_risk_tier: int
    total_elapsed_ms: float


@dataclass
class BatchReceipt:
    """A collection of receipt entries flushed as a single artifact."""
    batch_id: str
    task_id: str
    created_at: str
    closed_at: Optional[str] = None
    entries: list[ReceiptEntry] = field(default_factory=list)
    summary: BatchSummary = field(default_factory=lambda: BatchSummary(0, 0, 0, 0, 0.0))

    def to_dict(self) -> dict:
        """Serialize the entire batch receipt to a dict."""
        return {
            "batch_id": self.batch_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "entries": [
                {
                    "entry_index": e.entry_index,
                    "timestamp": e.timestamp,
                    "capability": e.capability,
                    "tool_id": e.tool_id,
                    "risk_tier": e.risk_tier,
                    "input_hash": e.input_hash,
                    "output_hash": e.output_hash,
                    "success": e.success,
                    "error": e.error,
                    "verification_status": e.verification_status,
                }
                for e in self.entries
            ],
            "summary": {
                "total_actions": self.summary.total_actions,
                "succeeded": self.summary.succeeded,
                "failed": self.summary.failed,
                "highest_risk_tier": self.summary.highest_risk_tier,
                "total_elapsed_ms": self.summary.total_elapsed_ms,
            },
        }


# ── Buffer ───────────────────────────────────────────────────────

def _hash(value: Any) -> str:
    """SHA-256 hex digest of the string representation."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class BatchReceiptBuffer:
    """Collects receipt entries and flushes them as batched JSON files.

    Supports:
    - Auto-flush when buffer reaches configured size
    - Tier-boundary flush before T2/T3 actions
    - Context manager for safe cleanup
    """

    def __init__(
        self,
        task_id: str,
        config: Optional[BatchReceiptConfig] = None,
        data_dir: str = "lancelot_data/receipts",
    ):
        self._task_id = task_id
        self._config = config or BatchReceiptConfig()
        self._data_dir = data_dir
        self._entries: list[ReceiptEntry] = []
        self._total_elapsed_ms: float = 0.0
        self._batch_id = str(uuid.uuid4())
        self._created_at = datetime.now(timezone.utc).isoformat()

    def append(
        self,
        capability: str,
        tool_id: str,
        risk_tier: RiskTier,
        inputs: Any,
        outputs: Any,
        success: bool,
        error: Optional[str] = None,
        elapsed_ms: float = 0.0,
        verification_status: str = "skipped",
    ) -> None:
        """Add a receipt entry to the buffer. Auto-flushes when full."""
        entry = ReceiptEntry(
            entry_index=len(self._entries),
            timestamp=datetime.now(timezone.utc).isoformat(),
            capability=capability,
            tool_id=tool_id,
            risk_tier=int(risk_tier),
            input_hash=_hash(inputs),
            output_hash=_hash(outputs),
            success=success,
            error=error,
            verification_status=verification_status,
        )
        self._entries.append(entry)
        self._total_elapsed_ms += elapsed_ms

        # Auto-flush when buffer is full
        if len(self._entries) >= self._config.buffer_size:
            self.flush()

    def flush(self) -> Optional[BatchReceipt]:
        """Write the current buffer to disk as a JSON file.

        Returns:
            The flushed BatchReceipt, or None if buffer was empty.
        """
        if not self._entries:
            return None

        succeeded = sum(1 for e in self._entries if e.success)
        failed = len(self._entries) - succeeded
        highest_tier = max(e.risk_tier for e in self._entries)

        summary = BatchSummary(
            total_actions=len(self._entries),
            succeeded=succeeded,
            failed=failed,
            highest_risk_tier=highest_tier,
            total_elapsed_ms=self._total_elapsed_ms,
        )

        receipt = BatchReceipt(
            batch_id=self._batch_id,
            task_id=self._task_id,
            created_at=self._created_at,
            closed_at=datetime.now(timezone.utc).isoformat(),
            entries=list(self._entries),
            summary=summary,
        )

        # Write to disk
        os.makedirs(self._data_dir, exist_ok=True)
        filepath = os.path.join(self._data_dir, f"batch_{self._batch_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(receipt.to_dict(), f, indent=2)

        # Reset buffer for next batch
        self._entries = []
        self._total_elapsed_ms = 0.0
        self._batch_id = str(uuid.uuid4())
        self._created_at = datetime.now(timezone.utc).isoformat()

        return receipt

    def flush_if_tier_boundary(self, upcoming_tier: RiskTier) -> Optional[BatchReceipt]:
        """Flush buffer if a T2/T3 action is about to execute.

        Args:
            upcoming_tier: The risk tier of the next action.

        Returns:
            The flushed BatchReceipt, or None if no flush needed.
        """
        if not self._config.flush_on_tier_boundary:
            return None
        if upcoming_tier < RiskTier.T2_CONTROLLED:
            return None
        if not self._entries:
            return None
        return self.flush()

    @property
    def size(self) -> int:
        """Current number of entries in the buffer."""
        return len(self._entries)

    @property
    def is_empty(self) -> bool:
        """Whether the buffer has no entries."""
        return len(self._entries) == 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if not self.is_empty:
            self.flush()
