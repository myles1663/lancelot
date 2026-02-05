"""
Tests for Memory vNext Commit Manager.

These tests validate:
- Staged edit workflow
- Atomic commit application
- Diff tracking
- Rollback functionality
- Receipt data generation
"""

import os
import pytest
from datetime import datetime

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.core.memory.schemas import (
    CommitStatus,
    CoreBlockType,
    MemoryEditOp,
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)
from src.core.memory.store import CoreBlockStore
from src.core.memory.sqlite_store import MemoryStoreManager
from src.core.memory.commits import CommitManager, CommitError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def core_store(tmp_data_dir):
    """Provide an initialized core block store."""
    store = CoreBlockStore(data_dir=tmp_data_dir)
    store.initialize()
    return store


@pytest.fixture
def store_manager(tmp_data_dir):
    """Provide an initialized store manager."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    yield manager
    manager.close_all()


@pytest.fixture
def commit_manager(core_store, store_manager, tmp_data_dir):
    """Provide a commit manager."""
    return CommitManager(
        core_store=core_store,
        store_manager=store_manager,
        data_dir=tmp_data_dir,
    )


@pytest.fixture
def populated_core_store(core_store):
    """Provide a core store with initial content."""
    prov = Provenance(type=ProvenanceType.system, ref="test")
    core_store.set_block(
        block_type=CoreBlockType.human,
        content="User likes Python",
        updated_by="owner",
        provenance=[prov],
    )
    core_store.set_block(
        block_type=CoreBlockType.mission,
        content="Complete the project",
        updated_by="owner",
        provenance=[prov],
    )
    return core_store


# ---------------------------------------------------------------------------
# Basic Workflow Tests
# ---------------------------------------------------------------------------
class TestBasicWorkflow:
    """Tests for basic commit workflow."""

    def test_begin_edits(self, commit_manager):
        """Test beginning a staged commit."""
        commit_id = commit_manager.begin_edits(
            created_by="test_agent",
            message="Test commit",
        )

        assert commit_id is not None
        assert len(commit_id) == 16

    def test_begin_edits_creates_snapshot(self, commit_manager, core_store):
        """Test that begin_edits creates a snapshot."""
        # Set some initial content
        prov = Provenance(type=ProvenanceType.system, ref="test")
        core_store.set_block(
            block_type=CoreBlockType.human,
            content="Initial content",
            updated_by="owner",
            provenance=[prov],
        )

        commit_id = commit_manager.begin_edits(created_by="test")

        # Snapshot should exist
        assert commit_id in commit_manager._snapshots

    def test_add_edit(self, commit_manager):
        """Test adding an edit to a staged commit."""
        commit_id = commit_manager.begin_edits(created_by="test")

        edit_id = commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="New content",
            reason="Update user info",
        )

        assert edit_id is not None

        # Check edit was added
        staged = commit_manager.get_staged_commit(commit_id)
        assert len(staged.edits) == 1

    def test_finish_edits_applies_changes(self, commit_manager, core_store):
        """Test that finish_edits applies all changes."""
        commit_id = commit_manager.begin_edits(created_by="test")

        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="Updated user preferences",
            reason="Update preferences",
        )

        result_id = commit_manager.finish_edits(commit_id)

        assert result_id == commit_id

        # Verify change was applied
        block = core_store.get_block(CoreBlockType.human)
        assert block.content == "Updated user preferences"

    def test_cancel_edits(self, commit_manager):
        """Test cancelling a staged commit."""
        commit_id = commit_manager.begin_edits(created_by="test")

        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="New content",
            reason="Test",
        )

        result = commit_manager.cancel_edits(commit_id)

        assert result is True
        assert commit_manager.get_staged_commit(commit_id) is None


# ---------------------------------------------------------------------------
# Core Block Edit Tests
# ---------------------------------------------------------------------------
class TestCoreBlockEdits:
    """Tests for core block edits."""

    def test_replace_core_block(self, commit_manager, core_store):
        """Test replacing a core block's content."""
        prov = Provenance(type=ProvenanceType.system, ref="test")
        core_store.set_block(
            block_type=CoreBlockType.human,
            content="Old content",
            updated_by="owner",
            provenance=[prov],
        )

        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="New content",
            reason="Update",
        )
        commit_manager.finish_edits(commit_id)

        block = core_store.get_block(CoreBlockType.human)
        assert block.content == "New content"

    def test_insert_to_core_block(self, commit_manager, core_store):
        """Test inserting content to a core block."""
        prov = Provenance(type=ProvenanceType.system, ref="test")
        core_store.set_block(
            block_type=CoreBlockType.human,
            content="Base content",
            updated_by="owner",
            provenance=[prov],
        )

        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.insert,
            target="core:human",
            after="Additional content",
            reason="Add more info",
        )
        commit_manager.finish_edits(commit_id)

        block = core_store.get_block(CoreBlockType.human)
        assert "Base content" in block.content
        assert "Additional content" in block.content

    def test_delete_core_block_content(self, commit_manager, core_store):
        """Test deleting a core block's content."""
        prov = Provenance(type=ProvenanceType.system, ref="test")
        core_store.set_block(
            block_type=CoreBlockType.mission,
            content="Old mission",
            updated_by="owner",
            provenance=[prov],
        )

        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.delete,
            target="core:mission",
            reason="Clear mission",
        )
        commit_manager.finish_edits(commit_id)

        block = core_store.get_block(CoreBlockType.mission)
        assert block.content == ""


# ---------------------------------------------------------------------------
# Memory Item Edit Tests
# ---------------------------------------------------------------------------
class TestMemoryItemEdits:
    """Tests for memory item edits."""

    def test_insert_working_memory_item(self, commit_manager, store_manager):
        """Test inserting a working memory item."""
        commit_id = commit_manager.begin_edits(created_by="agent")

        item_id = "new_task_123"
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.insert,
            target=f"working:{item_id}",
            after="New task content",
            reason="Add task",
        )
        commit_manager.finish_edits(commit_id)

        # Verify item was created
        item = store_manager.working.get(item_id)
        assert item is not None
        assert item.content == "New task content"

    def test_replace_archival_item(self, commit_manager, store_manager):
        """Test replacing an archival item."""
        # Create initial item
        item = MemoryItem(
            tier=MemoryTier.archival,
            title="Fact",
            content="Old fact",
            provenance=[Provenance(type=ProvenanceType.system, ref="test")],
        )
        store_manager.archival.insert(item)

        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target=f"archival:{item.id}",
            after="Updated fact",
            reason="Correct fact",
        )
        commit_manager.finish_edits(commit_id)

        updated = store_manager.archival.get(item.id)
        assert updated.content == "Updated fact"

    def test_delete_working_item(self, commit_manager, store_manager):
        """Test deleting a working memory item."""
        # Create initial item
        item = MemoryItem(
            tier=MemoryTier.working,
            title="Task",
            content="Task content",
            provenance=[Provenance(type=ProvenanceType.system, ref="test")],
        )
        store_manager.working.insert(item)

        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.delete,
            target=f"working:{item.id}",
            reason="Task done",
        )
        commit_manager.finish_edits(commit_id)

        deleted = store_manager.working.get(item.id)
        assert deleted is None


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------
class TestErrorHandling:
    """Tests for error handling."""

    def test_add_edit_invalid_commit(self, commit_manager):
        """Test adding edit to invalid commit raises error."""
        with pytest.raises(CommitError, match="not found"):
            commit_manager.add_edit(
                commit_id="nonexistent",
                op=MemoryEditOp.replace,
                target="core:human",
                after="Content",
                reason="Test",
            )

    def test_finish_empty_commit_fails(self, commit_manager):
        """Test finishing commit with no edits fails."""
        commit_id = commit_manager.begin_edits(created_by="test")

        with pytest.raises(CommitError, match="no edits"):
            commit_manager.finish_edits(commit_id)

    def test_finish_already_committed_fails(self, commit_manager):
        """Test finishing already committed commit fails."""
        commit_id = commit_manager.begin_edits(created_by="test")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="Content",
            reason="Test",
        )
        commit_manager.finish_edits(commit_id)

        with pytest.raises(CommitError, match="not found"):
            commit_manager.finish_edits(commit_id)

    def test_invalid_target_format(self, commit_manager):
        """Test invalid target format raises error."""
        commit_id = commit_manager.begin_edits(created_by="test")

        # Invalid target format should fail fast during add_edit
        with pytest.raises(CommitError, match="Invalid target format"):
            commit_manager.add_edit(
                commit_id=commit_id,
                op=MemoryEditOp.replace,
                target="invalid_target",  # Missing colon
                after="Content",
                reason="Test",
            )


# ---------------------------------------------------------------------------
# Rollback Tests
# ---------------------------------------------------------------------------
class TestRollback:
    """Tests for rollback functionality."""

    def test_rollback_restores_core_blocks(self, commit_manager, core_store):
        """Test that rollback restores core blocks."""
        prov = Provenance(type=ProvenanceType.system, ref="test")
        core_store.set_block(
            block_type=CoreBlockType.human,
            content="Original content",
            updated_by="owner",
            provenance=[prov],
        )

        # Make a change
        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="Changed content",
            reason="Update",
        )
        commit_manager.finish_edits(commit_id)

        # Verify change
        block = core_store.get_block(CoreBlockType.human)
        assert block.content == "Changed content"

        # Rollback
        rollback_id = commit_manager.rollback(
            commit_id=commit_id,
            reason="Revert change",
            created_by="owner",
        )

        # Verify restoration
        block = core_store.get_block(CoreBlockType.human)
        assert block.content == "Original content"
        assert rollback_id is not None

    def test_rollback_creates_commit(self, commit_manager, core_store):
        """Test that rollback creates a new commit."""
        prov = Provenance(type=ProvenanceType.system, ref="test")
        core_store.set_block(
            block_type=CoreBlockType.human,
            content="Content",
            updated_by="owner",
            provenance=[prov],
        )

        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="New",
            reason="Update",
        )
        commit_manager.finish_edits(commit_id)

        rollback_id = commit_manager.rollback(
            commit_id=commit_id,
            reason="Revert",
            created_by="owner",
        )

        # Load and verify rollback commit
        rollback_commit = commit_manager.load_commit(rollback_id)
        assert rollback_commit is not None
        assert rollback_commit.rollback_of == commit_id

    def test_rollback_invalid_commit(self, commit_manager):
        """Test rollback of invalid commit raises error."""
        with pytest.raises(CommitError, match="not found"):
            commit_manager.rollback(
                commit_id="nonexistent",
                reason="Test",
                created_by="test",
            )


# ---------------------------------------------------------------------------
# Persistence Tests
# ---------------------------------------------------------------------------
class TestCommitPersistence:
    """Tests for commit persistence."""

    def test_commit_persisted_to_disk(self, commit_manager, tmp_data_dir):
        """Test that commits are persisted."""
        commit_id = commit_manager.begin_edits(created_by="test")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="Content",
            reason="Test",
        )
        commit_manager.finish_edits(commit_id)

        # Check file exists
        commit_file = tmp_data_dir / "memory" / "commits" / f"{commit_id}.json"
        assert commit_file.exists()

    def test_load_commit(self, commit_manager):
        """Test loading a persisted commit."""
        commit_id = commit_manager.begin_edits(created_by="test")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="Content",
            reason="Test reason",
        )
        commit_manager.finish_edits(commit_id)

        loaded = commit_manager.load_commit(commit_id)

        assert loaded is not None
        assert loaded.commit_id == commit_id
        assert loaded.created_by == "test"
        assert len(loaded.edits) == 1

    def test_list_commits(self, commit_manager):
        """Test listing commits."""
        # Create multiple commits
        for i in range(3):
            commit_id = commit_manager.begin_edits(created_by=f"test_{i}")
            commit_manager.add_edit(
                commit_id=commit_id,
                op=MemoryEditOp.replace,
                target="core:human",
                after=f"Content {i}",
                reason="Test",
            )
            commit_manager.finish_edits(commit_id)

        commits = commit_manager.list_commits()

        assert len(commits) == 3


# ---------------------------------------------------------------------------
# Receipt Data Tests
# ---------------------------------------------------------------------------
class TestReceiptData:
    """Tests for receipt data generation."""

    def test_create_receipt_data(self, commit_manager):
        """Test receipt data generation."""
        commit_id = commit_manager.begin_edits(
            created_by="test_agent",
            message="Update preferences",
        )
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="Content",
            reason="Test",
        )
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:mission",
            after="Mission",
            reason="Test",
        )
        commit_manager.finish_edits(commit_id)

        loaded = commit_manager.load_commit(commit_id)
        receipt_data = commit_manager.create_receipt_data(loaded)

        assert receipt_data["commit_id"] == commit_id
        assert receipt_data["created_by"] == "test_agent"
        assert receipt_data["edit_count"] == 2
        assert receipt_data["has_core_edits"] is True
        assert "core:human" in receipt_data["affected_targets"]
        assert "core:mission" in receipt_data["affected_targets"]


# ---------------------------------------------------------------------------
# Multiple Edits Tests
# ---------------------------------------------------------------------------
class TestMultipleEdits:
    """Tests for commits with multiple edits."""

    def test_multiple_edits_applied_atomically(self, commit_manager, core_store):
        """Test that multiple edits are applied atomically."""
        prov = Provenance(type=ProvenanceType.system, ref="test")
        core_store.set_block(
            block_type=CoreBlockType.human,
            content="Human",
            updated_by="owner",
            provenance=[prov],
        )
        core_store.set_block(
            block_type=CoreBlockType.mission,
            content="Mission",
            updated_by="owner",
            provenance=[prov],
        )

        commit_id = commit_manager.begin_edits(created_by="agent")
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="New Human",
            reason="Update human",
        )
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:mission",
            after="New Mission",
            reason="Update mission",
        )
        commit_manager.finish_edits(commit_id)

        # Both should be updated
        human = core_store.get_block(CoreBlockType.human)
        mission = core_store.get_block(CoreBlockType.mission)

        assert human.content == "New Human"
        assert mission.content == "New Mission"

    def test_mixed_tier_edits(self, commit_manager, core_store, store_manager):
        """Test edits across core and item tiers."""
        commit_id = commit_manager.begin_edits(created_by="agent")

        # Core edit
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.replace,
            target="core:human",
            after="User content",
            reason="Update user",
        )

        # Working memory edit
        item_id = "task_001"
        commit_manager.add_edit(
            commit_id=commit_id,
            op=MemoryEditOp.insert,
            target=f"working:{item_id}",
            after="Task content",
            reason="Add task",
        )

        commit_manager.finish_edits(commit_id)

        # Verify both applied
        block = core_store.get_block(CoreBlockType.human)
        assert block.content == "User content"

        item = store_manager.working.get(item_id)
        assert item is not None
        assert item.content == "Task content"
