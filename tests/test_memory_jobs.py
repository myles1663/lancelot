"""
Tests for Memory Scheduler Jobs.

These tests validate:
- Working memory compaction
- Episodic summarization
- Archival decay
- Integrity audit
- Job result structure
"""

import os
import pytest
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.core.memory.jobs import (
    JobResult,
    MemoryJobExecutor,
    execute_memory_job,
    get_memory_job_specs,
)
from src.core.memory.store import CoreBlockStore
from src.core.memory.sqlite_store import MemoryStoreManager
from src.core.memory.commits import CommitManager
from src.core.memory.schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def core_store(temp_data_dir):
    """Create and initialize a core block store."""
    store = CoreBlockStore(data_dir=temp_data_dir)
    store.initialize()
    return store


@pytest.fixture
def store_manager(temp_data_dir):
    """Create a memory store manager."""
    return MemoryStoreManager(data_dir=temp_data_dir)


@pytest.fixture
def commit_manager(core_store, store_manager, temp_data_dir):
    """Create a commit manager."""
    return CommitManager(core_store, store_manager, temp_data_dir)


@pytest.fixture
def job_executor(core_store, store_manager, commit_manager, temp_data_dir):
    """Create a job executor with all dependencies."""
    return MemoryJobExecutor(
        core_store=core_store,
        store_manager=store_manager,
        commit_manager=commit_manager,
        data_dir=temp_data_dir,
    )


# ---------------------------------------------------------------------------
# JobResult Tests
# ---------------------------------------------------------------------------
class TestJobResult:
    """Tests for JobResult dataclass."""

    def test_job_result_creation(self):
        """Test creating a job result."""
        started = datetime.utcnow()
        completed = started + timedelta(seconds=5)

        result = JobResult(
            job_name="test_job",
            success=True,
            started_at=started,
            completed_at=completed,
            items_processed=100,
            items_affected=10,
        )

        assert result.job_name == "test_job"
        assert result.success is True
        assert result.items_processed == 100
        assert result.items_affected == 10

    def test_job_result_duration(self):
        """Test duration calculation."""
        started = datetime.utcnow()
        completed = started + timedelta(milliseconds=1500)

        result = JobResult(
            job_name="test",
            success=True,
            started_at=started,
            completed_at=completed,
        )

        assert result.duration_ms >= 1500

    def test_job_result_to_dict(self):
        """Test serialization to dict."""
        result = JobResult(
            job_name="test",
            success=True,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            items_processed=50,
            errors=["error1", "error2"],
            details={"key": "value"},
        )

        data = result.to_dict()

        assert data["job_name"] == "test"
        assert data["success"] is True
        assert "started_at" in data
        assert "completed_at" in data
        assert "duration_ms" in data
        assert data["errors"] == ["error1", "error2"]
        assert data["details"] == {"key": "value"}


# ---------------------------------------------------------------------------
# Working Compaction Tests
# ---------------------------------------------------------------------------
class TestWorkingCompaction:
    """Tests for working memory compaction job."""

    def test_compaction_empty_store(self, job_executor):
        """Test compaction on empty store."""
        result = job_executor.run_working_compaction()

        assert result.job_name == "working_compaction"
        assert result.success is True
        assert result.items_processed == 0

    def test_compaction_with_items(self, job_executor, store_manager):
        """Test compaction with items present."""
        # Add some items
        store = store_manager.get_store(MemoryTier.working)
        for i in range(5):
            item = MemoryItem(
                id=f"item_{i}",
                tier=MemoryTier.working,
                namespace="test",
                title=f"Item {i}",
                content=f"Content {i}",
                confidence=0.9,
            )
            store.insert(item)

        result = job_executor.run_working_compaction()

        assert result.success is True
        assert result.items_processed == 5

    def test_compaction_dry_run(self, job_executor, store_manager):
        """Test compaction dry run mode."""
        # Add items
        store = store_manager.get_store(MemoryTier.working)
        item = MemoryItem(
            id="dry_run_item",
            tier=MemoryTier.working,
            namespace="test",
            title="Dry Run Test",
            content="Content",
            confidence=0.9,
        )
        store.insert(item)

        result = job_executor.run_working_compaction(dry_run=True)

        assert result.success is True
        assert result.details["dry_run"] is True

    def test_compaction_dry_run_does_not_delete_expired(self, job_executor, store_manager):
        """Dry-run reports expired rows without mutating working memory."""
        store = store_manager.get_store(MemoryTier.working)
        item = MemoryItem(
            id="dry_run_expired_item",
            tier=MemoryTier.working,
            namespace="test",
            title="Expired dry run",
            content="Content",
            confidence=0.9,
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        store.insert(item)

        result = job_executor.run_working_compaction(dry_run=True)

        assert result.details["expired_deleted"] == 1
        assert store.get("dry_run_expired_item") is not None

    def test_compaction_deletes_expired(self, job_executor, store_manager):
        """Test compaction deletes expired items."""
        store = store_manager.get_store(MemoryTier.working)

        # Add expired item
        item = MemoryItem(
            id="expired_item",
            tier=MemoryTier.working,
            namespace="test",
            title="Expired",
            content="Content",
            confidence=0.9,
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        store.insert(item)

        result = job_executor.run_working_compaction()

        assert result.success is True
        assert result.details["expired_deleted"] >= 1

    def test_compaction_promotes_eligible_expired_working_item(self, job_executor, store_manager):
        """Eligible working learnings are promoted to episodic before TTL deletion."""
        working = store_manager.get_store(MemoryTier.working)
        episodic = store_manager.get_store(MemoryTier.episodic)
        item = MemoryItem(
            id="working_learning",
            tier=MemoryTier.working,
            namespace="quest:alpha",
            title="Useful learning",
            content="The release checklist requires receipt verification.",
            confidence=0.7,
            tags=["learning"],
            expires_at=datetime.utcnow() - timedelta(minutes=1),
            last_retrieved_at=datetime.utcnow() - timedelta(minutes=2),
            provenance=[Provenance(type=ProvenanceType.receipt, ref="r1")],
        )
        working.insert(item)

        result = job_executor.run_working_compaction()

        promoted = episodic.get("working_promotion_working_learning")
        assert result.details["items_promoted_to_episodic"] == 1
        assert result.details["expired_deleted"] == 0
        assert working.get("working_learning") is None
        assert promoted is not None
        assert promoted.status == MemoryStatus.active
        assert promoted.metadata["source_item_id"] == "working_learning"

    def test_compaction_quarantines_reviewable_working_item(self, job_executor, store_manager):
        """Working items with evidence but no promotion signal become review candidates."""
        working = store_manager.get_store(MemoryTier.working)
        episodic = store_manager.get_store(MemoryTier.episodic)
        item = MemoryItem(
            id="working_review",
            tier=MemoryTier.working,
            title="Reviewable learning",
            content="Potentially useful context with no explicit promotion tag.",
            confidence=0.7,
            expires_at=datetime.utcnow() - timedelta(minutes=1),
            last_retrieved_at=datetime.utcnow() - timedelta(minutes=2),
            provenance=[Provenance(type=ProvenanceType.user_message, ref="m1")],
        )
        working.insert(item)

        result = job_executor.run_working_compaction()

        candidate = episodic.get("working_promotion_working_review")
        assert result.details["promotion_candidates_quarantined"] == 1
        assert candidate is not None
        assert candidate.status == MemoryStatus.quarantined


# ---------------------------------------------------------------------------
# Episodic Summarization Tests
# ---------------------------------------------------------------------------
class TestEpisodicSummarization:
    """Tests for episodic summarization job."""

    def test_summarization_empty_store(self, job_executor):
        """Test summarization on empty store."""
        result = job_executor.run_episodic_summarization()

        assert result.job_name == "episodic_summarization"
        assert result.success is True
        assert result.items_processed == 0

    def test_summarization_with_items(self, job_executor, store_manager):
        """Test summarization with episodic items."""
        store = store_manager.get_store(MemoryTier.episodic)
        archival = store_manager.get_store(MemoryTier.archival)

        # Add enough items to trigger summarization
        for i in range(10):
            item = MemoryItem(
                id=f"episodic_{i}",
                tier=MemoryTier.episodic,
                namespace="project:test",
                title=f"Episodic Memory {i}",
                content=f"Memory content {i}",
                confidence=0.8,
            )
            store.insert(item)

        result = job_executor.run_episodic_summarization(min_items_for_summary=5)

        assert result.success is True
        assert result.items_processed == 10
        assert result.details["namespaces_found"] >= 1
        archived_summaries = archival.list_items()
        summary = next(item for item in archived_summaries if item.metadata.get("promotion_decision"))
        blob = archival.get(summary.metadata["source_blob_id"])
        assert summary.tier == MemoryTier.archival
        assert summary.metadata["promotion_decision"]["allowed"] is True
        assert "summary_fingerprint" in summary.metadata
        assert "Memory content 0" in summary.content
        assert "Time range:" in summary.content
        assert blob is not None
        assert blob.metadata["blob_type"] == "full_source"
        assert "Memory content 0" in blob.content

    def test_summarization_emits_receipt(self, job_executor):
        """Summarization completion emits a queryable memory job receipt."""
        result = job_executor.run_episodic_summarization(dry_run=True)

        receipts = job_executor._receipt_emitter.receipt_service.list(
            action_type="memory_job_episodic_summary"
        )
        assert result.success is True
        assert len(receipts) == 1
        assert receipts[0].outputs["details"]["dry_run"] is True

    def test_summarization_recovers_archival_written_journal(self, job_executor, store_manager):
        """Recovery completes source deprecation after a partial archival write."""
        episodic = store_manager.get_store(MemoryTier.episodic)
        archival = store_manager.get_store(MemoryTier.archival)
        item = MemoryItem(
            id="journal-source",
            tier=MemoryTier.episodic,
            namespace="journal",
            title="Journal Source",
            content="Journal source content",
            confidence=0.9,
        )
        summary = MemoryItem(
            id="journal-summary",
            tier=MemoryTier.archival,
            namespace="journal",
            title="Summary",
            content="Summary content",
            confidence=0.9,
            metadata={"summary_fingerprint": "fp-test"},
        )
        episodic.insert(item)
        archival.insert(summary)
        episodic.create_compaction_journal(
            compaction_id="journal-test",
            namespace="journal",
            source_item_ids=["journal-source"],
            metadata={"summary_fingerprint": "fp-test"},
        )
        episodic.update_compaction_journal(
            "journal-test",
            status="archival_written",
            archival_item_id="journal-summary",
        )

        recovered = job_executor.recover_pending_compactions()

        assert recovered == 1
        assert episodic.get("journal-source").status == MemoryStatus.deprecated

    def test_summarization_includes_tags_and_content_snippets(self, job_executor, store_manager):
        """Summaries should reflect actual episodic content, not only titles."""
        store = store_manager.get_store(MemoryTier.episodic)
        archival = store_manager.get_store(MemoryTier.archival)

        for i in range(5):
            item = MemoryItem(
                id=f"episodic_tagged_{i}",
                tier=MemoryTier.episodic,
                namespace="project:tagged",
                title=f"Tagged Memory {i}",
                content=f"Detailed content for memory {i} about incident review and follow-up actions.",
                tags=["incident", "review"] if i < 3 else ["review"],
                confidence=0.8,
            )
            store.insert(item)

        result = job_executor.run_episodic_summarization(min_items_for_summary=5)

        assert result.success is True
        summary = next(item for item in archival.list_items() if item.metadata.get("promotion_decision"))
        assert "Common tags: review, incident." in summary.content
        assert "Detailed content for memory 0" in summary.content

    def test_summarization_retry_ignores_existing_source_blob(self, job_executor, store_manager):
        """A retry after blob creation still creates the missing summary."""
        store = store_manager.get_store(MemoryTier.episodic)
        archival = store_manager.get_store(MemoryTier.archival)
        batch = []
        for i in range(5):
            item = MemoryItem(
                id=f"retry_episodic_{i}",
                tier=MemoryTier.episodic,
                namespace="project:retry",
                title=f"Retry Memory {i}",
                content=f"Retry content {i}",
                confidence=0.8,
            )
            store.insert(item)
            batch.append(item)

        fingerprint = job_executor._batch_fingerprint("project:retry", batch)
        blob = job_executor._create_source_blob_item(
            namespace="project:retry",
            batch=batch,
            fingerprint=fingerprint,
        )
        archival.insert(blob)

        result = job_executor.run_episodic_summarization(min_items_for_summary=5)

        summaries = [
            item for item in archival.find_by_metadata("summary_fingerprint", fingerprint, limit=10)
            if item.metadata.get("promotion_decision")
        ]
        blobs = [
            item for item in archival.find_by_metadata("summary_fingerprint", fingerprint, limit=10)
            if item.metadata.get("blob_type") == "full_source"
        ]
        assert result.success is True
        assert len(summaries) == 1
        assert len(blobs) == 1
        assert summaries[0].metadata["source_blob_id"] == blob.id

    def test_summarization_dry_run(self, job_executor, store_manager):
        """Test summarization dry run."""
        store = store_manager.get_store(MemoryTier.episodic)

        for i in range(7):
            item = MemoryItem(
                id=f"dry_episodic_{i}",
                tier=MemoryTier.episodic,
                namespace="test",
                title=f"Memory {i}",
                content=f"Content {i}",
                confidence=0.8,
            )
            store.insert(item)

        result = job_executor.run_episodic_summarization(
            min_items_for_summary=5,
            dry_run=True,
        )

        assert result.success is True
        assert result.details["dry_run"] is True
        # No items should be archived in dry run
        assert result.details["items_archived"] == 0


# ---------------------------------------------------------------------------
# Archival Decay Tests
# ---------------------------------------------------------------------------
class TestArchivalDecay:
    """Tests for archival decay job."""

    def test_decay_empty_store(self, job_executor):
        """Test decay on empty store."""
        result = job_executor.run_archival_decay()

        assert result.job_name == "archival_decay"
        assert result.success is True

    def test_decay_with_items(self, job_executor, store_manager):
        """Test decay with archival items."""
        store = store_manager.get_store(MemoryTier.archival)

        # Add item with decay enabled
        item = MemoryItem(
            id="decaying_item",
            tier=MemoryTier.archival,
            namespace="archive",
            title="Old Memory",
            content="Ancient content",
            confidence=0.9,
            decay_half_life_days=30,
            updated_at=datetime.utcnow() - timedelta(days=60),
        )
        store.insert(item)

        result = job_executor.run_archival_decay()

        assert result.success is True
        assert "items_decayed" in result.details

    def test_decay_dry_run(self, job_executor, store_manager):
        """Test decay dry run mode."""
        store = store_manager.get_store(MemoryTier.archival)

        item = MemoryItem(
            id="dry_decay_item",
            tier=MemoryTier.archival,
            namespace="test",
            title="Test",
            content="Content",
            confidence=0.9,
            decay_half_life_days=30,
        )
        store.insert(item)

        result = job_executor.run_archival_decay(dry_run=True)

        assert result.success is True
        assert result.details["dry_run"] is True


# ---------------------------------------------------------------------------
# Eviction Tests
# ---------------------------------------------------------------------------
class TestMemoryEviction:
    """Tests for LRU tier-cap eviction."""

    def test_eviction_exports_lru_items_to_cold_storage(self, job_executor, store_manager, temp_data_dir):
        """Eviction removes oldest excess items and writes them to cold storage."""
        store = store_manager.get_store(MemoryTier.episodic)
        old = MemoryItem(
            id="evict-old",
            tier=MemoryTier.episodic,
            title="Old item",
            content="Old item content",
            confidence=0.9,
            last_retrieved_at=datetime.utcnow() - timedelta(days=5),
        )
        new = MemoryItem(
            id="evict-new",
            tier=MemoryTier.episodic,
            title="New item",
            content="New item content",
            confidence=0.9,
            last_retrieved_at=datetime.utcnow() - timedelta(hours=1),
        )
        store.insert(old)
        store.insert(new)

        result = job_executor.run_eviction_job(
            max_working_items=100,
            max_episodic_items=1,
            max_archival_items=100,
        )

        tier_details = result.details["tiers"]["episodic"]
        assert result.success is True
        assert tier_details["evicted_item_ids"] == ["evict-old"]
        assert store.get("evict-old") is None
        assert store.get("evict-new") is not None
        cold_path = Path(tier_details["cold_storage_path"])
        assert cold_path.exists()
        assert "evict-old" in cold_path.read_text(encoding="utf-8")

    def test_eviction_dry_run_does_not_delete(self, job_executor, store_manager):
        """Dry-run eviction reports pressure without mutating stores."""
        store = store_manager.get_store(MemoryTier.working)
        store.insert(MemoryItem(
            id="dry-evict",
            tier=MemoryTier.working,
            title="Dry evict",
            content="Dry evict content",
            confidence=0.9,
        ))

        result = job_executor.run_eviction_job(
            max_working_items=0,
            max_episodic_items=100,
            max_archival_items=100,
            dry_run=True,
        )

        assert result.details["tiers"]["working"]["would_evict"] == 1
        assert result.details["tiers"]["working"]["evicted"] == 0
        assert store.get("dry-evict") is not None


# ---------------------------------------------------------------------------
# Integrity Audit Tests
# ---------------------------------------------------------------------------
class TestIntegrityAudit:
    """Tests for integrity audit job."""

    def test_audit_clean_state(self, job_executor):
        """Test audit on clean state."""
        result = job_executor.run_integrity_audit()

        assert result.job_name == "integrity_audit"
        assert result.success is True
        assert result.details["issues_found"] == 0

    def test_audit_with_items(self, job_executor, store_manager):
        """Test audit with items across tiers."""
        # Add items to each tier
        for tier in [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]:
            store = store_manager.get_store(tier)
            item = MemoryItem(
                id=f"audit_{tier.value}",
                tier=tier,
                namespace="test",
                title=f"Test {tier.value}",
                content="Content",
                confidence=0.8,
            )
            store.insert(item)

        result = job_executor.run_integrity_audit()

        assert result.success is True
        assert "tier_counts" in result.details
        assert result.details["tier_counts"]["working"] >= 1
        assert result.details["tier_counts"]["episodic"] >= 1
        assert result.details["tier_counts"]["archival"] >= 1

    def test_audit_reports_issues(self, job_executor, store_manager):
        """Test that audit reports detected issues."""
        result = job_executor.run_integrity_audit()

        # With default state, should have no issues
        assert "budget_issues" in result.details
        assert "core_block_count" in result.details


# ---------------------------------------------------------------------------
# Run All Maintenance Tests
# ---------------------------------------------------------------------------
class TestRunAllMaintenance:
    """Tests for running all maintenance jobs."""

    def test_run_all_maintenance(self, job_executor):
        """Test running all maintenance jobs."""
        results = job_executor.run_all_maintenance()

        assert "working_compaction" in results
        assert "episodic_summarization" in results
        assert "archival_decay" in results
        assert "memory_eviction" in results
        assert "integrity_audit" in results

        # All should succeed on clean state
        for result in results.values():
            assert result.success is True

    def test_run_all_maintenance_dry_run(self, job_executor):
        """Test running all maintenance in dry run mode."""
        results = job_executor.run_all_maintenance(dry_run=True)

        assert len(results) == 5
        for job_name in ["working_compaction", "episodic_summarization", "archival_decay", "memory_eviction"]:
            assert results[job_name].details.get("dry_run", False) is True


# ---------------------------------------------------------------------------
# Job Specs Tests
# ---------------------------------------------------------------------------
class TestJobSpecs:
    """Tests for job specification helpers."""

    def test_get_memory_job_specs(self):
        """Test getting job specs."""
        specs = get_memory_job_specs()

        assert isinstance(specs, list)
        assert len(specs) == 5

        job_ids = [s["id"] for s in specs]
        assert "memory_working_compaction" in job_ids
        assert "memory_episodic_summarization" in job_ids
        assert "memory_archival_decay" in job_ids
        assert "memory_eviction" in job_ids
        assert "memory_integrity_audit" in job_ids

    def test_job_spec_structure(self):
        """Test job spec has required fields."""
        specs = get_memory_job_specs()

        for spec in specs:
            assert "id" in spec
            assert "name" in spec
            assert "description" in spec
            assert "trigger" in spec
            assert "enabled" in spec
            assert "timeout_s" in spec
            assert "skill" in spec

    def test_job_spec_triggers(self):
        """Test job spec triggers are valid."""
        specs = get_memory_job_specs()

        for spec in specs:
            trigger = spec["trigger"]
            assert "type" in trigger
            assert trigger["type"] == "interval"
            assert "seconds" in trigger
            assert trigger["seconds"] > 0


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------
class TestErrorHandling:
    """Tests for error handling in jobs."""

    def test_compaction_handles_errors(self, core_store, temp_data_dir):
        """Test compaction handles store errors gracefully."""
        # Create executor with broken store manager
        executor = MemoryJobExecutor(
            core_store=core_store,
            store_manager=None,  # This will cause errors
            data_dir=temp_data_dir,
        )

        result = executor.run_working_compaction()

        assert result.success is False
        assert len(result.errors) > 0

    def test_audit_handles_errors(self, temp_data_dir):
        """Test audit handles errors gracefully."""
        # Create executor with broken stores
        executor = MemoryJobExecutor(
            core_store=None,
            store_manager=None,
            data_dir=temp_data_dir,
        )

        result = executor.run_integrity_audit()

        assert result.success is False
        assert len(result.errors) > 0


class TestMemoryJobDispatch:
    """Tests for scheduler dispatch into memory maintenance jobs."""

    def test_execute_memory_job_dispatches_by_skill(self, job_executor):
        result = execute_memory_job(job_executor, "memory_integrity_audit", {})
        assert result.job_name == "integrity_audit"
        assert result.success is True

    def test_execute_memory_job_rejects_unknown_skill(self, job_executor):
        with pytest.raises(ValueError, match="Unknown memory job skill"):
            execute_memory_job(job_executor, "not_a_memory_job", {})
