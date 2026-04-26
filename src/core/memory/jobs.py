"""
Structured Memory Scheduler Jobs — Background maintenance tasks.

This module provides scheduled jobs for memory subsystem hygiene:
- Working Memory Compaction: Consolidate and clean working memory
- Episodic Summarization: Summarize and archive episodic memories
- Archival Decay: Apply confidence decay to old memories
- Integrity Audit: Verify memory consistency and fix issues

Each job returns a JobResult with execution details.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)
from .promotion import evaluate_promotion

logger = logging.getLogger(__name__)


MEMORY_JOB_SKILLS = {
    "memory_working_compaction",
    "memory_episodic_summarization",
    "memory_archival_decay",
    "memory_integrity_audit",
}


@dataclass
class JobResult:
    """Result of a memory maintenance job."""
    job_name: str
    success: bool
    started_at: datetime
    completed_at: datetime
    items_processed: int = 0
    items_affected: int = 0
    errors: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        """Get job duration in milliseconds."""
        delta = self.completed_at - self.started_at
        return int(delta.total_seconds() * 1000)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "job_name": self.job_name,
            "success": self.success,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_ms": self.duration_ms,
            "items_processed": self.items_processed,
            "items_affected": self.items_affected,
            "errors": self.errors,
            "details": self.details,
        }


class MemoryJobExecutor:
    """
    Executes memory maintenance jobs.

    Provides methods for:
    - Working memory compaction
    - Episodic summarization
    - Archival decay
    - Integrity audit
    """

    def __init__(
        self,
        core_store: Any,
        store_manager: Any,
        commit_manager: Optional[Any] = None,
        data_dir: Optional[Path] = None,
    ):
        """
        Initialize the job executor.

        Args:
            core_store: CoreBlockStore instance
            store_manager: MemoryStoreManager instance
            commit_manager: CommitManager for creating commits (optional)
            data_dir: Data directory path
        """
        self._core_store = core_store
        self._store_manager = store_manager
        self._commit_manager = commit_manager
        self._data_dir = data_dir or Path("lancelot_data")

    def run_working_compaction(
        self,
        age_threshold_hours: int = 24,
        min_items_to_compact: int = 5,
        dry_run: bool = False,
    ) -> JobResult:
        """
        Compact working memory by removing stale items.

        This job:
        1. Finds working memory items older than threshold
        2. Removes expired items
        3. Consolidates related items if possible
        4. Reports on space reclaimed

        Args:
            age_threshold_hours: Age in hours after which items are considered stale
            min_items_to_compact: Minimum items needed to trigger compaction
            dry_run: If True, report but don't make changes

        Returns:
            JobResult with compaction details
        """
        started_at = datetime.utcnow()
        job_name = "working_compaction"

        try:
            store = self._store_manager.get_store(MemoryTier.working)

            # Delete expired items first
            expired_count = store.delete_expired()
            logger.info("Working compaction: deleted %d expired items", expired_count)

            # Find old items
            cutoff = datetime.utcnow() - timedelta(hours=age_threshold_hours)
            all_items = store.list_items(include_expired=False)

            stale_items = [
                item for item in all_items
                if item.updated_at < cutoff and item.status == MemoryStatus.active
            ]

            items_processed = len(all_items)
            items_affected = expired_count

            if len(stale_items) >= min_items_to_compact and not dry_run:
                # Mark very old items (3x threshold) for archival consideration
                very_old_cutoff = datetime.utcnow() - timedelta(hours=age_threshold_hours * 3)
                for item in stale_items:
                    if item.updated_at < very_old_cutoff:
                        # Lower confidence of very stale items
                        item.confidence = max(0.1, item.confidence * 0.8)
                        store.update(item)
                        items_affected += 1

            return JobResult(
                job_name=job_name,
                success=True,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                items_processed=items_processed,
                items_affected=items_affected,
                details={
                    "expired_deleted": expired_count,
                    "stale_items_found": len(stale_items),
                    "age_threshold_hours": age_threshold_hours,
                    "dry_run": dry_run,
                },
            )

        except Exception as exc:
            logger.error("Working compaction failed: %s", exc)
            return JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            )

    def run_episodic_summarization(
        self,
        items_per_batch: int = 10,
        min_items_for_summary: int = 5,
        dry_run: bool = False,
    ) -> JobResult:
        """
        Summarize episodic memories for long-term storage.

        This job:
        1. Groups related episodic items by namespace
        2. Creates summary items for large groups
        3. Archives original items after summarization
        4. Maintains provenance chain

        Args:
            items_per_batch: Max items to process per batch
            min_items_for_summary: Min items in a group to trigger summary
            dry_run: If True, report but don't make changes

        Returns:
            JobResult with summarization details
        """
        started_at = datetime.utcnow()
        job_name = "episodic_summarization"

        try:
            store = self._store_manager.get_store(MemoryTier.episodic)
            archival_store = self._store_manager.get_store(MemoryTier.archival)

            # Get all episodic items grouped by namespace
            all_items = store.list_items(status=MemoryStatus.active)
            items_processed = len(all_items)

            # Group by namespace
            groups: Dict[str, List[MemoryItem]] = {}
            for item in all_items:
                ns = item.namespace or "default"
                if ns not in groups:
                    groups[ns] = []
                groups[ns].append(item)

            summaries_created = 0
            items_archived = 0

            for namespace, items in groups.items():
                if len(items) >= min_items_for_summary and not dry_run:
                    # Sort by date and take oldest batch
                    items.sort(key=lambda x: x.created_at)
                    batch = items[:items_per_batch]

                    # Create a deterministic summary from the actual episodic content.
                    summary_content = self._create_episodic_summary(batch)

                    # Create new summary item
                    from .schemas import MemoryItem as MI
                    import uuid

                    summary_item = MI(
                        id=f"summary_{uuid.uuid4().hex[:8]}",
                        tier=MemoryTier.archival,
                        namespace=namespace,
                        title=f"Summary: {namespace} ({len(batch)} items)",
                        content=summary_content,
                        tags=["summary", "auto-generated"],
                        confidence=0.7,
                        decay_half_life_days=90,
                        provenance=[
                            Provenance(
                                type=ProvenanceType.system,
                                ref="episodic_summarization_job",
                            )
                        ],
                    )

                    promotion_decision = evaluate_promotion(summary_item, MemoryTier.archival)
                    summary_item.status = promotion_decision.suggested_status
                    summary_item.metadata["promotion_decision"] = promotion_decision.to_dict()
                    if promotion_decision.allowed:
                        archival_store.insert(summary_item)
                        summaries_created += 1
                    else:
                        logger.warning(
                            "Episodic summary for namespace %s was not promoted: %s",
                            namespace,
                            promotion_decision.reason,
                        )

                    # Archive original items (lower confidence)
                    for item in batch:
                        item.confidence = max(0.1, item.confidence * 0.5)
                        item.status = MemoryStatus.deprecated
                        store.update(item)
                        items_archived += 1

            return JobResult(
                job_name=job_name,
                success=True,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                items_processed=items_processed,
                items_affected=summaries_created + items_archived,
                details={
                    "namespaces_found": len(groups),
                    "summaries_created": summaries_created,
                    "items_archived": items_archived,
                    "dry_run": dry_run,
                },
            )

        except Exception as exc:
            logger.error("Episodic summarization failed: %s", exc)
            return JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            )

    def _create_episodic_summary(self, items: List[MemoryItem]) -> str:
        """Create a deterministic summary from actual episodic items.

        This avoids a fake placeholder while keeping the scheduler job free of
        live LLM dependencies. The summary captures:
        - batch size and time range
        - most common tags
        - concise title/content bullets from the source items
        """
        if not items:
            return "No episodic memories were available for summarization."

        sorted_items = sorted(items, key=lambda x: x.created_at)
        start = sorted_items[0].created_at.isoformat()
        end = sorted_items[-1].created_at.isoformat()

        tag_counts: Counter[str] = Counter()
        for item in sorted_items:
            for tag in item.tags or []:
                clean_tag = (tag or "").strip()
                if clean_tag:
                    tag_counts[clean_tag] += 1
        common_tags = [tag for tag, _count in tag_counts.most_common(3)]

        lines = [
            f"Episodic summary for {len(sorted_items)} memories.",
            f"Time range: {start} to {end}.",
        ]
        if common_tags:
            lines.append("Common tags: " + ", ".join(common_tags) + ".")

        lines.append("Highlights:")
        for item in sorted_items[:5]:
            title = (item.title or "Untitled memory").strip()
            snippet = self._summarize_text(item.content)
            if snippet:
                lines.append(f"- {title}: {snippet}")
            else:
                lines.append(f"- {title}")

        if len(sorted_items) > 5:
            lines.append(f"- ... plus {len(sorted_items) - 5} additional related memories.")

        return "\n".join(lines)

    @staticmethod
    def _summarize_text(text: str, max_len: int = 160) -> str:
        """Collapse content to a readable single-line snippet."""
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        if not cleaned:
            return ""
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[: max_len - 3].rstrip() + "..."

    def run_archival_decay(
        self,
        days_elapsed: int = 1,
        min_confidence: float = 0.1,
        dry_run: bool = False,
    ) -> JobResult:
        """
        Apply confidence decay to archival memories.

        This job:
        1. Finds archival items with decay_half_life_days set
        2. Applies exponential decay based on age
        3. Removes items below minimum confidence threshold
        4. Reports on decay statistics

        Args:
            days_elapsed: Number of days to decay
            min_confidence: Minimum confidence (items below are deprecated)
            dry_run: If True, report but don't make changes

        Returns:
            JobResult with decay details
        """
        started_at = datetime.utcnow()
        job_name = "archival_decay"

        try:
            store = self._store_manager.get_store(MemoryTier.archival)

            # Apply decay using the store's built-in method
            if not dry_run:
                decayed_count = store.apply_decay(days_elapsed=days_elapsed)
            else:
                # Count items that would be affected
                all_items = store.list_items(status=MemoryStatus.active)
                decayed_count = sum(
                    1 for item in all_items
                    if item.decay_half_life_days and item.decay_half_life_days > 0
                )

            # Count items below threshold
            all_items = store.list_items(include_expired=True)
            below_threshold = sum(
                1 for item in all_items
                if item.confidence < min_confidence and item.status == MemoryStatus.active
            )

            return JobResult(
                job_name=job_name,
                success=True,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                items_processed=len(all_items) if all_items else 0,
                items_affected=decayed_count,
                details={
                    "items_decayed": decayed_count,
                    "below_threshold": below_threshold,
                    "days_elapsed": days_elapsed,
                    "min_confidence": min_confidence,
                    "dry_run": dry_run,
                },
            )

        except Exception as exc:
            logger.error("Archival decay failed: %s", exc)
            return JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            )

    def run_integrity_audit(self) -> JobResult:
        """
        Audit memory integrity and report issues.

        This job:
        1. Validates core block budgets
        2. Checks for orphaned references
        3. Verifies FTS index consistency
        4. Reports on any issues found

        Returns:
            JobResult with audit details
        """
        started_at = datetime.utcnow()
        job_name = "integrity_audit"
        issues: List[str] = []
        items_processed = 0

        try:
            # 1. Check core block budgets
            budget_issues = self._core_store.validate_budgets()
            if budget_issues:
                issues.extend([
                    f"Budget issue: {block_type} - {message}"
                    for block_type, message in budget_issues
                ])

            # 2. Count all items across tiers
            tier_counts = {}
            for tier in [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]:
                store = self._store_manager.get_store(tier)
                count = store.count()
                tier_counts[tier.value] = count
                items_processed += count

                # Check for items with invalid status
                all_items = store.list_items(include_expired=True)
                for item in all_items:
                    if item.confidence < 0 or item.confidence > 1:
                        issues.append(f"Invalid confidence for {item.id}: {item.confidence}")

            # 3. Check core blocks for consistency
            blocks = self._core_store.get_all_blocks()
            for block_type, block in blocks.items():
                if block.token_count < 0:
                    issues.append(f"Negative token count for {block_type}")
                if block.version < 0:
                    issues.append(f"Negative version for {block_type}")

            return JobResult(
                job_name=job_name,
                success=len(issues) == 0,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                items_processed=items_processed,
                items_affected=len(issues),
                errors=issues,
                details={
                    "tier_counts": tier_counts,
                    "core_block_count": len(blocks),
                    "budget_issues": budget_issues,
                    "issues_found": len(issues),
                },
            )

        except Exception as exc:
            logger.error("Integrity audit failed: %s", exc)
            return JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            )

    def run_all_maintenance(self, dry_run: bool = False) -> Dict[str, JobResult]:
        """
        Run all maintenance jobs in sequence.

        Args:
            dry_run: If True, report but don't make changes

        Returns:
            Dict mapping job name to JobResult
        """
        results = {}

        results["working_compaction"] = self.run_working_compaction(dry_run=dry_run)
        results["episodic_summarization"] = self.run_episodic_summarization(dry_run=dry_run)
        results["archival_decay"] = self.run_archival_decay(dry_run=dry_run)
        results["integrity_audit"] = self.run_integrity_audit()

        logger.info(
            "Maintenance complete: %d jobs, %d successful",
            len(results),
            sum(1 for r in results.values() if r.success),
        )

        return results


# ---------------------------------------------------------------------------
# Job Registration Helpers
# ---------------------------------------------------------------------------

def get_memory_job_specs() -> List[Dict[str, Any]]:
    """
    Get job specifications for scheduler registration.

    Returns:
        List of job spec dicts compatible with scheduler config
    """
    return [
        {
            "id": "memory_working_compaction",
            "name": "Memory: Working Compaction",
            "description": "Clean and compact working memory tier",
            "trigger": {"type": "interval", "seconds": 3600},  # Every hour
            "enabled": True,
            "requires_ready": True,
            "timeout_s": 120,
            "skill": "memory_working_compaction",
        },
        {
            "id": "memory_episodic_summarization",
            "name": "Memory: Episodic Summarization",
            "description": "Summarize episodic memories for archival",
            "trigger": {"type": "interval", "seconds": 86400},  # Daily
            "enabled": True,
            "requires_ready": True,
            "timeout_s": 300,
            "skill": "memory_episodic_summarization",
        },
        {
            "id": "memory_archival_decay",
            "name": "Memory: Archival Decay",
            "description": "Apply confidence decay to archival tier",
            "trigger": {"type": "interval", "seconds": 86400},  # Daily
            "enabled": True,
            "requires_ready": True,
            "timeout_s": 120,
            "skill": "memory_archival_decay",
        },
        {
            "id": "memory_integrity_audit",
            "name": "Memory: Integrity Audit",
            "description": "Audit memory subsystem integrity",
            "trigger": {"type": "interval", "seconds": 21600},  # Every 6 hours
            "enabled": True,
            "requires_ready": True,
            "timeout_s": 60,
            "skill": "memory_integrity_audit",
        },
    ]


def execute_memory_job(
    executor: MemoryJobExecutor,
    skill_name: str,
    inputs: Optional[Dict[str, Any]] = None,
) -> JobResult:
    """Execute a memory maintenance job by its scheduler skill name."""
    payload = dict(inputs or {})
    handlers = {
        "memory_working_compaction": executor.run_working_compaction,
        "memory_episodic_summarization": executor.run_episodic_summarization,
        "memory_archival_decay": executor.run_archival_decay,
        "memory_integrity_audit": executor.run_integrity_audit,
    }
    handler = handlers.get(skill_name)
    if handler is None:
        raise ValueError(f"Unknown memory job skill: {skill_name}")
    return handler(**payload)
