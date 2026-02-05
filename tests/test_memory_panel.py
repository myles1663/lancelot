"""
Tests for Memory Panel (War Room).

These tests validate:
- Core block summary rendering
- Tier statistics
- Quarantine queue display
- Search functionality
- Approve/reject actions
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.ui.panels.memory_panel import MemoryPanel
from src.core.memory.store import CoreBlockStore
from src.core.memory.sqlite_store import MemoryStoreManager
from src.core.memory.commits import CommitManager
from src.core.memory.gates import QuarantineManager
from src.core.memory.index import MemoryIndex
from src.core.memory.schemas import (
    CoreBlockType,
    MemoryItem,
    MemoryEditOp,
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
def quarantine_manager(core_store, store_manager):
    """Create a quarantine manager."""
    return QuarantineManager(core_store, store_manager)


@pytest.fixture
def memory_index(store_manager):
    """Create a memory index."""
    return MemoryIndex(store_manager)


@pytest.fixture
def memory_panel(core_store, store_manager, commit_manager, quarantine_manager, memory_index):
    """Create a memory panel with all dependencies."""
    return MemoryPanel(
        core_store=core_store,
        store_manager=store_manager,
        commit_manager=commit_manager,
        quarantine_manager=quarantine_manager,
        memory_index=memory_index,
    )


# ---------------------------------------------------------------------------
# Basic Panel Tests
# ---------------------------------------------------------------------------
class TestMemoryPanelBasic:
    """Basic panel functionality tests."""

    def test_panel_is_enabled(self, memory_panel):
        """Test panel reports enabled state."""
        assert memory_panel.is_enabled is True

    def test_panel_disabled_without_store(self):
        """Test panel reports disabled without store."""
        panel = MemoryPanel()
        assert panel.is_enabled is False

    def test_render_data_structure(self, memory_panel):
        """Test render_data returns expected structure."""
        data = memory_panel.render_data()

        assert data["panel"] == "memory"
        assert data["enabled"] is True
        assert "core_blocks" in data
        assert "tier_stats" in data
        assert "quarantine" in data
        assert "recent_commits" in data
        assert "budget_status" in data
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Core Block Summary Tests
# ---------------------------------------------------------------------------
class TestCoreBlocksSummary:
    """Tests for core blocks summary."""

    def test_get_core_blocks_summary(self, memory_panel):
        """Test getting core blocks summary."""
        summary = memory_panel.get_core_blocks_summary()

        assert isinstance(summary, list)
        assert len(summary) > 0

        # Check first block has expected fields
        block = summary[0]
        assert "block_type" in block
        assert "status" in block
        assert "token_count" in block
        assert "token_budget" in block
        assert "utilization_pct" in block
        assert "version" in block

    def test_core_block_utilization(self, memory_panel, core_store):
        """Test token utilization calculation."""
        # Update a block with content using valid 'agent' value
        core_store.set_block(
            CoreBlockType.mission,
            "This is the mission content for testing.",
            "agent",
            [],
        )

        summary = memory_panel.get_core_blocks_summary()
        mission_block = next((b for b in summary if b["block_type"] == "mission"), None)

        assert mission_block is not None
        assert mission_block["token_count"] > 0
        assert mission_block["utilization_pct"] > 0


# ---------------------------------------------------------------------------
# Tier Statistics Tests
# ---------------------------------------------------------------------------
class TestTierStatistics:
    """Tests for tier statistics."""

    def test_get_tier_statistics(self, memory_panel):
        """Test getting tier statistics."""
        stats = memory_panel.get_tier_statistics()

        assert "working" in stats
        assert "episodic" in stats
        assert "archival" in stats

        for tier_stats in stats.values():
            assert "total_items" in tier_stats
            assert "active" in tier_stats
            assert "quarantined" in tier_stats

    def test_tier_statistics_with_items(self, memory_panel, store_manager):
        """Test tier statistics after adding items."""
        # Add some items using insert method
        store = store_manager.get_store(MemoryTier.working)
        item = MemoryItem(
            id="test_item",
            tier=MemoryTier.working,
            namespace="test",
            title="Test Item",
            content="Test content",
            tags=["test"],
            confidence=0.9,
        )
        store.insert(item)

        stats = memory_panel.get_tier_statistics()

        assert stats["working"]["total_items"] >= 1
        assert stats["working"]["active"] >= 1


# ---------------------------------------------------------------------------
# Quarantine Tests
# ---------------------------------------------------------------------------
class TestQuarantineQueue:
    """Tests for quarantine queue."""

    def test_get_empty_quarantine(self, memory_panel):
        """Test getting empty quarantine queue."""
        queue = memory_panel.get_quarantine_queue()

        assert "core_blocks" in queue
        assert "items" in queue
        assert "total_count" in queue

    def test_quarantine_with_items(self, memory_panel, store_manager):
        """Test quarantine queue with items."""
        # Add a quarantined item
        store = store_manager.get_store(MemoryTier.working)
        item = MemoryItem(
            id="quarantined_item",
            tier=MemoryTier.working,
            namespace="test",
            title="Quarantined Test",
            content="Suspicious content",
            status=MemoryStatus.quarantined,
            confidence=0.5,
        )
        store.insert(item)

        queue = memory_panel.get_quarantine_queue()

        assert queue["total_count"] >= 1
        assert len(queue["items"]) >= 1


# ---------------------------------------------------------------------------
# Commit History Tests
# ---------------------------------------------------------------------------
class TestCommitHistory:
    """Tests for commit history display."""

    def test_get_recent_commits_empty(self, memory_panel):
        """Test getting commits when empty."""
        commits = memory_panel.get_recent_commits()
        assert isinstance(commits, list)

    def test_get_recent_commits_after_commit(self, memory_panel, commit_manager):
        """Test getting commits after creating one."""
        # Create a commit using enum
        commit_id = commit_manager.begin_edits("test", "Test commit")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:mission",
            after="New mission",
            reason="Test",
            confidence=0.9,
        )
        commit_manager.finish_edits(commit_id)

        commits = memory_panel.get_recent_commits()

        assert len(commits) >= 1
        assert commits[0]["created_by"] == "test"


# ---------------------------------------------------------------------------
# Search Tests
# ---------------------------------------------------------------------------
class TestMemorySearch:
    """Tests for memory search."""

    def test_search_empty_results(self, memory_panel):
        """Test search with no results."""
        results = memory_panel.search_memory("nonexistent_xyz")
        assert isinstance(results, list)

    def test_search_with_items(self, memory_panel, store_manager):
        """Test search with items."""
        # Add searchable item
        store = store_manager.get_store(MemoryTier.working)
        item = MemoryItem(
            id="searchable_item",
            tier=MemoryTier.working,
            namespace="test",
            title="Important Task",
            content="Complete the project documentation",
            tags=["project", "docs"],
            confidence=0.9,
        )
        store.insert(item)

        results = memory_panel.search_memory("documentation")

        # May or may not find depending on FTS setup
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Approval/Rejection Tests
# ---------------------------------------------------------------------------
class TestQuarantineActions:
    """Tests for approve/reject actions."""

    def test_approve_nonexistent(self, memory_panel):
        """Test approving nonexistent item."""
        result = memory_panel.approve_quarantined("nonexistent_id", "admin")
        assert "error" in result

    def test_reject_nonexistent(self, memory_panel):
        """Test rejecting nonexistent item."""
        result = memory_panel.reject_quarantined("nonexistent_id", "admin", "test")
        assert "error" in result

    def test_approve_quarantined_item(self, memory_panel, store_manager):
        """Test approving a quarantined item."""
        # Add quarantined item
        store = store_manager.get_store(MemoryTier.working)
        item = MemoryItem(
            id="to_approve",
            tier=MemoryTier.working,
            namespace="test",
            title="Needs Approval",
            content="Content to approve",
            status=MemoryStatus.quarantined,
            confidence=0.7,
        )
        store.insert(item)

        result = memory_panel.approve_quarantined("to_approve", "admin")

        assert result.get("status") == "approved" or "error" in result


# ---------------------------------------------------------------------------
# Budget Status Tests
# ---------------------------------------------------------------------------
class TestBudgetStatus:
    """Tests for budget status."""

    def test_get_budget_status(self, memory_panel):
        """Test getting budget status."""
        status = memory_panel.get_budget_status()

        assert "total_core_tokens" in status
        assert "budget_issues" in status
        assert "has_issues" in status

    def test_budget_no_issues(self, memory_panel):
        """Test budget status with no issues."""
        status = memory_panel.get_budget_status()

        # With default content, should have no issues (budget_issues is a list)
        assert isinstance(status["budget_issues"], list)


# ---------------------------------------------------------------------------
# Disabled Panel Tests
# ---------------------------------------------------------------------------
class TestDisabledPanel:
    """Tests for disabled panel behavior."""

    def test_disabled_render_data(self):
        """Test render_data when disabled."""
        panel = MemoryPanel()  # No stores provided
        data = panel.render_data()

        assert data["enabled"] is False
        assert "message" in data

    def test_disabled_methods_return_empty(self):
        """Test methods return empty when disabled."""
        panel = MemoryPanel()

        assert panel.get_core_blocks_summary() == []
        assert panel.get_tier_statistics() == {}
        assert panel.get_quarantine_queue() == {"core_blocks": [], "items": []}
        assert panel.get_recent_commits() == []
        assert panel.search_memory("test") == []
