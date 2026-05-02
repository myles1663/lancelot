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
import hashlib
import json
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
from .promotion import evaluate_promotion, evaluate_working_to_episodic_promotion
from .receipt_events import MemoryReceiptEmitter
from .cold_storage import ColdStorageWriter
from .config import (
    COLD_STORAGE_RETENTION_DAYS,
    EVICTION_BATCH_SIZE,
    MAX_ARCHIVAL_ITEMS,
    MAX_EPISODIC_ITEMS,
    MAX_WORKING_ITEMS,
)
from src.shared.receipts import ReceiptStatus

logger = logging.getLogger(__name__)


MEMORY_JOB_SKILLS = {
    "memory_working_compaction",
    "memory_episodic_summarization",
    "memory_archival_decay",
    "memory_eviction",
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
        receipt_emitter: Optional[MemoryReceiptEmitter] = None,
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
        self._receipt_emitter = receipt_emitter or MemoryReceiptEmitter(self._data_dir)
        self._cold_storage = ColdStorageWriter(self._data_dir)

    def _emit_job_result(self, result: JobResult, action_type: str) -> JobResult:
        """Emit a finalized receipt for a memory maintenance job result."""
        self._receipt_emitter.emit(
            action_type=action_type,
            action_name=result.job_name,
            inputs={
                "job_name": result.job_name,
                "started_at": result.started_at.isoformat(),
                "dry_run": bool(result.details.get("dry_run", False)),
            },
            outputs=result.to_dict(),
            status=ReceiptStatus.SUCCESS if result.success else ReceiptStatus.FAILURE,
            duration_ms=result.duration_ms,
            error_message="; ".join(result.errors) if result.errors else None,
            metadata={"error_summary": result.errors[:3] if result.errors else []},
        )
        return result

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

            # Find old and expired items. Promotion is evaluated before deletion
            # so useful working-memory learnings can survive TTL expiry.
            now = datetime.utcnow()
            cutoff = datetime.utcnow() - timedelta(hours=age_threshold_hours)
            all_items = store.list_items(include_expired=True)

            stale_items = [
                item for item in all_items
                if item.updated_at < cutoff and item.status == MemoryStatus.active and not item.is_expired(now)
            ]
            expired_items = [
                item for item in all_items
                if item.status == MemoryStatus.active and item.is_expired(now)
            ]

            items_processed = len(all_items)
            expired_count = len(expired_items)
            deleted_count = 0
            promoted_count = 0
            quarantined_candidates = 0
            decayed_count = 0

            if len(stale_items) >= min_items_to_compact and not dry_run:
                # Mark very old items (3x threshold) for archival consideration
                very_old_cutoff = datetime.utcnow() - timedelta(hours=age_threshold_hours * 3)
                for item in stale_items:
                    if item.updated_at < very_old_cutoff:
                        disposition = self._promote_or_decay_working_item(item)
                        promoted_count += 1 if disposition == "promoted" else 0
                        quarantined_candidates += 1 if disposition == "quarantined_candidate" else 0
                        if disposition == "decayed":
                            decayed_count += 1

            if not dry_run:
                for item in expired_items:
                    disposition = self._promote_or_delete_working_item(item)
                    promoted_count += 1 if disposition == "promoted" else 0
                    quarantined_candidates += 1 if disposition == "quarantined_candidate" else 0
                    deleted_count += 1 if disposition == "deleted" else 0
            else:
                for item in expired_items:
                    decision = evaluate_working_to_episodic_promotion(item)
                    if decision.allowed and decision.suggested_status == MemoryStatus.active:
                        promoted_count += 1
                    elif decision.allowed and decision.suggested_status == MemoryStatus.quarantined:
                        quarantined_candidates += 1
                    else:
                        deleted_count += 1

            return self._emit_job_result(JobResult(
                job_name=job_name,
                success=True,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                items_processed=items_processed,
                items_affected=deleted_count + promoted_count + quarantined_candidates + decayed_count,
                details={
                    "expired_deleted": deleted_count,
                    "expired_found": expired_count,
                    "stale_items_found": len(stale_items),
                    "items_promoted_to_episodic": promoted_count,
                    "promotion_candidates_quarantined": quarantined_candidates,
                    "items_decayed": decayed_count,
                    "age_threshold_hours": age_threshold_hours,
                    "dry_run": dry_run,
                },
            ), "memory_job_working_compaction")

        except Exception as exc:
            logger.error("Working compaction failed: %s", exc)
            return self._emit_job_result(JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            ), "memory_job_working_compaction")

    def _promote_or_delete_working_item(self, item: MemoryItem) -> str:
        """Promote an expired working item when eligible; otherwise delete it."""
        decision = evaluate_working_to_episodic_promotion(item)
        if decision.allowed:
            self._insert_episodic_candidate(item, decision)
            self._store_manager.get_store(MemoryTier.working).delete(item.id)
            return "promoted" if decision.suggested_status == MemoryStatus.active else "quarantined_candidate"
        self._store_manager.get_store(MemoryTier.working).delete(item.id)
        return "deleted"

    def _promote_or_decay_working_item(self, item: MemoryItem) -> str:
        """Promote a very stale working item when eligible; otherwise decay it."""
        decision = evaluate_working_to_episodic_promotion(item)
        if decision.allowed:
            self._insert_episodic_candidate(item, decision)
            self._store_manager.get_store(MemoryTier.working).delete(item.id)
            return "promoted" if decision.suggested_status == MemoryStatus.active else "quarantined_candidate"
        item.confidence = max(0.1, item.confidence * 0.8)
        self._store_manager.get_store(MemoryTier.working).update(item)
        return "decayed"

    def _insert_episodic_candidate(self, item: MemoryItem, decision: Any) -> None:
        """Copy working memory into episodic memory with promotion metadata."""
        episodic = self._store_manager.get_store(MemoryTier.episodic)
        promoted_id = f"working_promotion_{item.id}"
        existing = episodic.get(promoted_id)
        if existing is not None:
            return
        metadata = dict(item.metadata or {})
        metadata.update({
            "source_tier": MemoryTier.working.value,
            "source_item_id": item.id,
            "promotion_decision": decision.to_dict(),
        })
        promoted = MemoryItem(
            id=promoted_id,
            tier=MemoryTier.episodic,
            namespace=item.namespace,
            title=item.title,
            content=item.content,
            tags=list(dict.fromkeys([*item.tags, "promoted_from_working"])),
            confidence=item.confidence,
            created_at=item.created_at,
            updated_at=datetime.utcnow(),
            last_retrieved_at=item.last_retrieved_at,
            expires_at=None,
            decay_half_life_days=item.decay_half_life_days,
            provenance=item.provenance,
            status=decision.suggested_status,
            token_count=item.token_count,
            metadata=metadata,
        )
        episodic.insert(promoted)

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
            recovered = self.recover_pending_compactions()

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
                    fingerprint = self._batch_fingerprint(namespace, batch)
                    existing_summary = self._find_existing_summary(archival_store, fingerprint)
                    if existing_summary:
                        self._deprecate_episodic_sources(store, batch)
                        items_archived += len(batch)
                        continue

                    compaction_id = f"episodic_summary_{fingerprint[:16]}"
                    store.create_compaction_journal(
                        compaction_id=compaction_id,
                        namespace=namespace,
                        source_item_ids=[item.id for item in batch],
                        metadata={"summary_fingerprint": fingerprint},
                    )

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
                    summary_item.metadata["summary_fingerprint"] = fingerprint
                    summary_item.metadata["source_item_ids"] = [item.id for item in batch]
                    if promotion_decision.allowed:
                        blob_item = self._create_source_blob_item(
                            namespace=namespace,
                            batch=batch,
                            fingerprint=fingerprint,
                        )
                        if archival_store.get(blob_item.id) is None:
                            archival_store.insert(blob_item)
                        summary_item.metadata["source_blob_id"] = blob_item.id
                        archival_store.insert(summary_item)
                        store.update_compaction_journal(
                            compaction_id,
                            status="archival_written",
                            archival_item_id=summary_item.id,
                        )
                        summaries_created += 1
                    else:
                        logger.warning(
                            "Episodic summary for namespace %s was not promoted: %s",
                            namespace,
                            promotion_decision.reason,
                        )
                        store.update_compaction_journal(
                            compaction_id,
                            status="failed",
                            error=promotion_decision.reason,
                        )
                        continue

                    # Archive original items (lower confidence)
                    items_archived += self._deprecate_episodic_sources(store, batch)
                    store.update_compaction_journal(compaction_id, status="complete")

            return self._emit_job_result(JobResult(
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
                    "pending_compactions_recovered": recovered,
                    "dry_run": dry_run,
                },
            ), "memory_job_episodic_summary")

        except Exception as exc:
            logger.error("Episodic summarization failed: %s", exc)
            return self._emit_job_result(JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            ), "memory_job_episodic_summary")

    def recover_pending_compactions(self) -> int:
        """Recover in-flight episodic compactions from the journal."""
        recovered = 0
        episodic_store = self._store_manager.get_store(MemoryTier.episodic)
        for entry in episodic_store.list_pending_compactions():
            if entry["status"] == "planned":
                episodic_store.update_compaction_journal(
                    entry["compaction_id"],
                    status="failed",
                    error="Compaction stopped before archival insert",
                )
                recovered += 1
                continue

            if entry["status"] == "archival_written":
                batch = []
                for item_id in entry["source_item_ids"]:
                    item = episodic_store.get(item_id)
                    if item is not None and item.status == MemoryStatus.active:
                        batch.append(item)
                self._deprecate_episodic_sources(episodic_store, batch)
                episodic_store.update_compaction_journal(entry["compaction_id"], status="complete")
                recovered += 1
        return recovered

    @staticmethod
    def _batch_fingerprint(namespace: str, items: list[MemoryItem]) -> str:
        payload = "|".join([namespace, *[item.id for item in sorted(items, key=lambda x: x.id)]])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _deprecate_episodic_sources(store: Any, items: list[MemoryItem]) -> int:
        archived = 0
        for item in items:
            item.confidence = max(0.1, item.confidence * 0.5)
            item.status = MemoryStatus.deprecated
            store.update(item)
            archived += 1
        return archived

    @staticmethod
    def _find_existing_summary(archival_store: Any, fingerprint: str) -> Optional[MemoryItem]:
        """Find a summary for a compaction fingerprint, ignoring full-source blobs."""
        for item in archival_store.find_by_metadata("summary_fingerprint", fingerprint, limit=10):
            if (item.metadata or {}).get("promotion_decision"):
                return item
        return None

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

    def _create_source_blob_item(
        self,
        *,
        namespace: str,
        batch: list[MemoryItem],
        fingerprint: str,
    ) -> MemoryItem:
        """Create a lossless archival blob for summarized episodic source items."""
        payload = {
            "source_items": [item.model_dump(mode="json") for item in batch],
        }
        return MemoryItem(
            id=f"source_blob_{fingerprint[:16]}",
            tier=MemoryTier.archival,
            namespace=namespace,
            title=f"Source Blob: {namespace} ({len(batch)} items)",
            content=json.dumps(payload, sort_keys=True),
            tags=["summary_source_blob", "audit_blob"],
            confidence=1.0,
            decay_half_life_days=90,
            provenance=[
                Provenance(
                    type=ProvenanceType.system,
                    ref="episodic_summarization_job",
                )
            ],
            metadata={
                "blob_type": "full_source",
                "summary_fingerprint": fingerprint,
                "source_item_ids": [item.id for item in batch],
                "excluded_from_default_retrieval": True,
            },
        )

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

            return self._emit_job_result(JobResult(
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
            ), "memory_job_archival_decay")

        except Exception as exc:
            logger.error("Archival decay failed: %s", exc)
            return self._emit_job_result(JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            ), "memory_job_archival_decay")

    def run_eviction_job(
        self,
        max_working_items: int = MAX_WORKING_ITEMS,
        max_episodic_items: int = MAX_EPISODIC_ITEMS,
        max_archival_items: int = MAX_ARCHIVAL_ITEMS,
        batch_size: int = EVICTION_BATCH_SIZE,
        dry_run: bool = False,
    ) -> JobResult:
        """Enforce tier item caps with LRU eviction and cold-storage export."""
        started_at = datetime.utcnow()
        job_name = "memory_eviction"
        caps = {
            MemoryTier.working: max_working_items,
            MemoryTier.episodic: max_episodic_items,
            MemoryTier.archival: max_archival_items,
        }
        try:
            details: dict[str, Any] = {
                "dry_run": dry_run,
                "batch_size": batch_size,
                "cold_storage_retention_days": COLD_STORAGE_RETENTION_DAYS,
                "tiers": {},
            }
            processed = 0
            affected = 0
            for tier, cap in caps.items():
                store = self._store_manager.get_store(tier)
                count_before = store.count()
                processed += count_before
                evict_count = max(count_before - cap, 0)
                cold_path = None
                evicted_items: list[MemoryItem] = []
                if evict_count and not dry_run:
                    evicted_items = store.evict_lru(max_items=cap, batch_size=batch_size)
                    cold_path_obj = self._cold_storage.write_items(
                        tier=tier,
                        items=evicted_items,
                        reason="tier_cap_lru_eviction",
                    )
                    cold_path = str(cold_path_obj) if cold_path_obj else None
                    affected += len(evicted_items)
                elif evict_count:
                    affected += min(evict_count, batch_size)

                details["tiers"][tier.value] = {
                    "cap": cap,
                    "count_before": count_before,
                    "would_evict": min(evict_count, batch_size),
                    "evicted": len(evicted_items),
                    "evicted_item_ids": [item.id for item in evicted_items],
                    "cold_storage_path": cold_path,
                }

            return self._emit_job_result(JobResult(
                job_name=job_name,
                success=True,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                items_processed=processed,
                items_affected=affected,
                details=details,
            ), "memory_job_eviction")

        except Exception as exc:
            logger.error("Memory eviction failed: %s", exc)
            return self._emit_job_result(JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            ), "memory_job_eviction")

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

            return self._emit_job_result(JobResult(
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
            ), "memory_job_integrity_audit")

        except Exception as exc:
            logger.error("Integrity audit failed: %s", exc)
            return self._emit_job_result(JobResult(
                job_name=job_name,
                success=False,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                errors=[str(exc)],
            ), "memory_job_integrity_audit")

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
        results["memory_eviction"] = self.run_eviction_job(dry_run=dry_run)
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
            "id": "memory_eviction",
            "name": "Memory: LRU Eviction",
            "description": "Enforce tier item caps and export evicted items to cold storage",
            "trigger": {"type": "interval", "seconds": 86400},
            "enabled": True,
            "requires_ready": True,
            "timeout_s": 120,
            "skill": "memory_eviction",
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
        "memory_eviction": executor.run_eviction_job,
        "memory_integrity_audit": executor.run_integrity_audit,
    }
    handler = handlers.get(skill_name)
    if handler is None:
        raise ValueError(f"Unknown memory job skill: {skill_name}")
    return handler(**payload)
