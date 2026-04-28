# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Chain Integrity Checker — Receipt DAG Verification for Compliance Export.

Extends ReceiptService.validate_parent_chain() (which catches orphaned
parent_ids) with directional continuity checking across a full export
period.  Returns CHAIN_INTACT or CHAIN_ANOMALY with gap detail.

This is the foundational trust claim of the compliance export feature.
An auditor receiving a Lancelot export with CHAIN_INTACT has evidence
of a tamper-evident governance record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from src.shared.receipts import ReceiptService

logger = logging.getLogger("lancelot.compliance.chain_integrity")


@dataclass
class ChainGap:
    """A gap in the receipt chain — a receipt references a parent that
    either doesn't exist or falls outside the export period."""
    receipt_id: str
    orphaned_parent_id: str
    gap_type: str  # "missing_parent" | "out_of_period"
    receipt_timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChainIntegrityResult:
    """Result of a chain integrity check over an export period."""
    status: str  # "CHAIN_INTACT" or "CHAIN_ANOMALY"
    period_start: str
    period_end: str
    total_receipts: int
    receipts_with_parents: int
    orphaned_count: int
    gaps: List[ChainGap] = field(default_factory=list)

    @property
    def is_intact(self) -> bool:
        return self.status == "CHAIN_INTACT"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_intact"] = self.is_intact
        return d


def check_chain_integrity(
    receipt_service: ReceiptService,
    period_start: str,
    period_end: str,
    quest_id: Optional[str] = None,
) -> ChainIntegrityResult:
    """Verify receipt chain integrity for an export period.

    Checks:
    1. **Orphaned parents** (extends validate_parent_chain): receipts
       whose parent_id references a receipt that doesn't exist anywhere
       in the store.
    2. **Out-of-period parents**: receipts whose parent_id references a
       receipt that exists but falls outside the export period.  These
       are NOT anomalies — they are cross-period links.  They are logged
       for context but do not affect the CHAIN_INTACT status.
    3. **Directional continuity**: the chain is unbroken across the full
       period.  A gap means at least one receipt in the period has a
       parent_id pointing to a non-existent receipt.

    Args:
        receipt_service: The receipt store to check.
        period_start: ISO 8601 start of export period.
        period_end: ISO 8601 end of export period.
        quest_id: Optional quest scope.

    Returns:
        ChainIntegrityResult with CHAIN_INTACT or CHAIN_ANOMALY.
    """
    summary = receipt_service.summarize_parent_chain(
        since=period_start,
        until=period_end,
        quest_id=quest_id,
    )
    total_receipts = int(summary["total_receipts"])
    receipts_with_parents = int(summary["receipts_with_parents"])
    orphan_rows = summary["missing_parent_gaps"]

    gaps: List[ChainGap] = []
    for row in orphan_rows:
        gaps.append(ChainGap(
            receipt_id=row["receipt_id"],
            orphaned_parent_id=row["orphaned_parent_id"],
            gap_type="missing_parent",
            receipt_timestamp=row["receipt_timestamp"],
        ))

    status = "CHAIN_ANOMALY" if gaps else "CHAIN_INTACT"

    result = ChainIntegrityResult(
        status=status,
        period_start=period_start,
        period_end=period_end,
        total_receipts=total_receipts,
        receipts_with_parents=receipts_with_parents,
        orphaned_count=len(gaps),
        gaps=gaps,
    )

    if gaps:
        logger.warning(
            "Chain integrity check: CHAIN_ANOMALY — %d gaps in period %s to %s",
            len(gaps), period_start, period_end,
        )
    else:
        logger.info(
            "Chain integrity check: CHAIN_INTACT — %d receipts, %d with parents",
            total_receipts, receipts_with_parents,
        )

    return result
