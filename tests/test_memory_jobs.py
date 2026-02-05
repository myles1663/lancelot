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
    get_memory_job_specs,
)
from src.core.memory.store import CoreBlockStore
from src.core.memory.sqlite_store import MemoryStoreManager
from src.core.memory.commits import CommitManager
from src.core.memory.schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
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
        assert "integrity_audit" in results

        # All should succeed on clean state
        for result in results.values():
            assert result.success is True

    def test_run_all_maintenance_dry_run(self, job_executor):
        """Test running all maintenance in dry run mode."""
        results = job_executor.run_all_maintenance(dry_run=True)

        assert len(results) == 4
        for job_name in ["working_compaction", "episodic_summarization", "archival_decay"]:
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
        assert len(specs) == 4

        job_ids = [s["id"] for s in specs]
        assert "memory_working_compaction" in job_ids
        assert "memory_episodic_summarization" in job_ids
        assert "memory_archival_decay" in job_ids
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
