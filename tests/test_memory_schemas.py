"""
Tests for Memory vNext schemas and core block store.

These tests validate:
- Pydantic schema validation
- Core block budget enforcement
- JSON persistence
- Snapshot/restore functionality
- Token estimation
"""

import json
import os
import pytest
from datetime import datetime, timedelta
from pathlib import Path

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.core.memory.schemas import (
    CoreBlock,
    CoreBlockType,
    CoreBlocksSnapshot,
    MemoryCommit,
    MemoryEdit,
    MemoryEditOp,
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
    CompiledContext,
    CommitStatus,
    generate_id,
)
from src.core.memory.config import (
    MemoryConfig,
    default_config,
    DEFAULT_CORE_BLOCK_BUDGETS,
)
from src.core.memory.store import CoreBlockStore, estimate_tokens


# ---------------------------------------------------------------------------
# Provenance Tests
# ---------------------------------------------------------------------------
class TestProvenance:
    """Tests for Provenance schema."""

    def test_create_provenance(self):
        """Test basic provenance creation."""
        prov = Provenance(
            type=ProvenanceType.user_message,
            ref="msg_123",
            snippet="User said something important",
        )
        assert prov.type == ProvenanceType.user_message
        assert prov.ref == "msg_123"
        assert prov.snippet == "User said something important"
        assert prov.timestamp is not None

    def test_provenance_immutable(self):
        """Test that Provenance is immutable (frozen)."""
        prov = Provenance(
            type=ProvenanceType.receipt,
            ref="rcpt_456",
        )
        with pytest.raises(Exception):  # ValidationError for frozen model
            prov.ref = "changed"

    def test_all_provenance_types(self):
        """Test all provenance types are valid."""
        for ptype in ProvenanceType:
            prov = Provenance(type=ptype, ref=f"test_{ptype.value}")
            assert prov.type == ptype


# ---------------------------------------------------------------------------
# CoreBlock Tests
# ---------------------------------------------------------------------------
class TestCoreBlock:
    """Tests for CoreBlock schema."""

    def test_create_core_block(self):
        """Test basic core block creation."""
        block = CoreBlock(
            block_type=CoreBlockType.persona,
            content="I am Lancelot, a helpful assistant.",
            token_budget=500,
            token_count=10,
            updated_by="system",
        )
        assert block.block_type == CoreBlockType.persona
        assert block.token_budget == 500
        assert block.status == MemoryStatus.active
        assert block.confidence == 1.0
        assert block.version == 1

    def test_block_within_budget(self):
        """Test budget validation."""
        block = CoreBlock(
            block_type=CoreBlockType.human,
            content="User preferences",
            token_budget=100,
            token_count=50,
            updated_by="owner",
        )
        assert block.within_budget() is True

        over_budget = CoreBlock(
            block_type=CoreBlockType.human,
            content="Too much content",
            token_budget=100,
            token_count=150,
            updated_by="owner",
        )
        assert over_budget.within_budget() is False

    def test_budget_utilization(self):
        """Test budget utilization calculation."""
        block = CoreBlock(
            block_type=CoreBlockType.mission,
            content="Mission content",
            token_budget=200,
            token_count=100,
            updated_by="agent",
        )
        assert block.budget_utilization() == 0.5

    def test_invalid_updater(self):
        """Test that invalid updater raises error."""
        with pytest.raises(ValueError, match="updated_by must be one of"):
            CoreBlock(
                block_type=CoreBlockType.persona,
                content="Content",
                token_budget=100,
                updated_by="invalid_user",
            )

    def test_all_block_types(self):
        """Test all core block types are valid."""
        for btype in CoreBlockType:
            block = CoreBlock(
                block_type=btype,
                content=f"Content for {btype.value}",
                token_budget=100,
                updated_by="system",
            )
            assert block.block_type == btype


# ---------------------------------------------------------------------------
# MemoryItem Tests
# ---------------------------------------------------------------------------
class TestMemoryItem:
    """Tests for MemoryItem schema."""

    def test_create_memory_item(self):
        """Test basic memory item creation."""
        item = MemoryItem(
            tier=MemoryTier.working,
            title="Task notes",
            content="Working on feature X",
            tags=["task", "feature"],
        )
        assert item.tier == MemoryTier.working
        assert item.namespace == "global"
        assert len(item.id) == 16
        assert item.status == MemoryStatus.active

    def test_item_expiration(self):
        """Test expiration checking."""
        now = datetime.utcnow()
        expired_item = MemoryItem(
            tier=MemoryTier.working,
            title="Expired",
            content="Old content",
            expires_at=now - timedelta(hours=1),
        )
        assert expired_item.is_expired(now) is True

        valid_item = MemoryItem(
            tier=MemoryTier.working,
            title="Valid",
            content="Current content",
            expires_at=now + timedelta(hours=1),
        )
        assert valid_item.is_expired(now) is False

    def test_item_decay(self):
        """Test confidence decay calculation."""
        item = MemoryItem(
            tier=MemoryTier.archival,
            title="Fact",
            content="Some fact",
            confidence=1.0,
            decay_half_life_days=30,
        )
        # After 30 days, confidence should be 0.5
        new_conf = item.apply_decay(30)
        assert abs(new_conf - 0.5) < 0.01

        # After 60 days, confidence should be 0.25
        new_conf = item.apply_decay(60)
        assert abs(new_conf - 0.25) < 0.01

    def test_namespace_scoping(self):
        """Test namespace patterns."""
        global_item = MemoryItem(
            tier=MemoryTier.working,
            title="Global",
            content="Global scope",
            namespace="global",
        )
        assert global_item.namespace == "global"

        quest_item = MemoryItem(
            tier=MemoryTier.working,
            title="Quest",
            content="Quest scope",
            namespace="quest:abc123",
        )
        assert quest_item.namespace == "quest:abc123"


# ---------------------------------------------------------------------------
# MemoryEdit Tests
# ---------------------------------------------------------------------------
class TestMemoryEdit:
    """Tests for MemoryEdit schema."""

    def test_create_edit(self):
        """Test basic edit creation."""
        edit = MemoryEdit(
            op=MemoryEditOp.insert,
            target="core:human",
            after="New preference",
            reason="User stated preference",
        )
        assert edit.op == MemoryEditOp.insert
        assert edit.is_core_edit() is True

    def test_target_validation(self):
        """Test that target must have colon separator."""
        with pytest.raises(ValueError, match="must be in format"):
            MemoryEdit(
                op=MemoryEditOp.insert,
                target="invalid_target",
                reason="Test",
            )

    def test_get_target_parts(self):
        """Test target parsing."""
        edit = MemoryEdit(
            op=MemoryEditOp.replace,
            target="archival:item123",
            reason="Update",
        )
        tier, id_ = edit.get_target_parts()
        assert tier == "archival"
        assert id_ == "item123"


# ---------------------------------------------------------------------------
# MemoryCommit Tests
# ---------------------------------------------------------------------------
class TestMemoryCommit:
    """Tests for MemoryCommit schema."""

    def test_create_commit(self):
        """Test basic commit creation."""
        commit = MemoryCommit(
            created_by="agent:planner",
            message="Update user preferences",
        )
        assert commit.status == CommitStatus.staged
        assert len(commit.commit_id) == 16
        assert commit.edits == []

    def test_add_edits(self):
        """Test adding edits to commit."""
        commit = MemoryCommit(created_by="agent")
        edit1 = MemoryEdit(
            op=MemoryEditOp.insert,
            target="core:human",
            reason="Add preference",
        )
        edit2 = MemoryEdit(
            op=MemoryEditOp.insert,
            target="working:task1",
            reason="Add task note",
        )

        commit.add_edit(edit1)
        commit.add_edit(edit2)

        assert len(commit.edits) == 2
        assert commit.has_core_edits() is True
        assert commit.get_affected_targets() == {"core:human", "working:task1"}


# ---------------------------------------------------------------------------
# CompiledContext Tests
# ---------------------------------------------------------------------------
class TestCompiledContext:
    """Tests for CompiledContext schema."""

    def test_create_context(self):
        """Test basic compiled context creation."""
        ctx = CompiledContext(
            objective="Help user with task",
            included_blocks=[CoreBlockType.persona, CoreBlockType.human],
            token_estimate=1500,
        )
        assert ctx.mode == "normal"
        assert len(ctx.included_blocks) == 2
        assert ctx.compiler_version == "1.0.0"

    def test_add_exclusion(self):
        """Test recording exclusions."""
        ctx = CompiledContext(objective="Test")
        ctx.add_exclusion("item123", "low_confidence", confidence=0.2)
        ctx.add_exclusion("item456", "exceeded_budget", tokens=500)

        assert len(ctx.excluded_candidates) == 2
        assert ctx.excluded_candidates[0]["item_id"] == "item123"
        assert ctx.excluded_candidates[0]["reason"] == "low_confidence"


# ---------------------------------------------------------------------------
# CoreBlocksSnapshot Tests
# ---------------------------------------------------------------------------
class TestCoreBlocksSnapshot:
    """Tests for CoreBlocksSnapshot schema."""

    def test_create_snapshot(self):
        """Test snapshot creation with blocks."""
        block = CoreBlock(
            block_type=CoreBlockType.persona,
            content="Test content",
            token_budget=500,
            token_count=10,
            updated_by="system",
        )

        snapshot = CoreBlocksSnapshot()
        snapshot.set_block(block)

        assert snapshot.get_block(CoreBlockType.persona) is not None
        assert snapshot.total_tokens() == 10


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------
class TestMemoryConfig:
    """Tests for MemoryConfig."""

    def test_default_budgets(self):
        """Test default token budgets exist."""
        assert "persona" in DEFAULT_CORE_BLOCK_BUDGETS
        assert "human" in DEFAULT_CORE_BLOCK_BUDGETS
        assert DEFAULT_CORE_BLOCK_BUDGETS["persona"] == 500

    def test_validate_block_size(self):
        """Test block size validation."""
        config = MemoryConfig()

        # Within budget
        valid, msg = config.validate_block_size("persona", 100)
        assert valid is True
        assert msg == ""

        # Over budget
        valid, msg = config.validate_block_size("persona", 600)
        assert valid is False
        assert "exceeds budget" in msg

        # Near budget (warning)
        valid, msg = config.validate_block_size("persona", 450)
        assert valid is True
        assert "approaching budget" in msg


# ---------------------------------------------------------------------------
# CoreBlockStore Tests
# ---------------------------------------------------------------------------
class TestCoreBlockStore:
    """Tests for CoreBlockStore persistence."""

    def test_store_initialization(self, tmp_data_dir):
        """Test store creates directories and default blocks."""
        store = CoreBlockStore(data_dir=tmp_data_dir)
        store.initialize()

        # Check directory created
        memory_dir = tmp_data_dir / "memory"
        assert memory_dir.exists()

        # Check blocks file created
        blocks_file = memory_dir / "core_blocks.json"
        assert blocks_file.exists()

        # Check default blocks exist
        blocks = store.get_all_blocks()
        assert len(blocks) == 5  # All CoreBlockType values

    def test_store_set_and_get_block(self, tmp_data_dir):
        """Test setting and getting a block."""
        store = CoreBlockStore(data_dir=tmp_data_dir)
        store.initialize()

        prov = Provenance(
            type=ProvenanceType.user_message,
            ref="msg_001",
        )

        block = store.set_block(
            block_type=CoreBlockType.human,
            content="User prefers dark mode",
            updated_by="owner",
            provenance=[prov],
        )

        assert block.content == "User prefers dark mode"
        assert block.version == 2  # Default was v1

        # Retrieve and verify
        retrieved = store.get_block(CoreBlockType.human)
        assert retrieved is not None
        assert retrieved.content == "User prefers dark mode"

    def test_store_persistence(self, tmp_data_dir):
        """Test that data persists across store instances."""
        # Create and populate store
        store1 = CoreBlockStore(data_dir=tmp_data_dir)
        store1.initialize()
        store1.set_block(
            block_type=CoreBlockType.mission,
            content="Complete the project",
            updated_by="owner",
            provenance=[],
        )

        # Create new store instance, should load persisted data
        store2 = CoreBlockStore(data_dir=tmp_data_dir)
        store2.initialize()

        block = store2.get_block(CoreBlockType.mission)
        assert block is not None
        assert block.content == "Complete the project"

    def test_store_budget_enforcement(self, tmp_data_dir):
        """Test that store enforces token budgets."""
        store = CoreBlockStore(data_dir=tmp_data_dir)
        store.initialize()

        # Try to set content that exceeds budget
        huge_content = "x" * 10000  # Way over any budget

        with pytest.raises(ValueError, match="exceeds token budget"):
            store.set_block(
                block_type=CoreBlockType.persona,
                content=huge_content,
                updated_by="owner",
                provenance=[],
            )

    def test_store_snapshot_restore(self, tmp_data_dir):
        """Test snapshot and restore functionality."""
        store = CoreBlockStore(data_dir=tmp_data_dir)
        store.initialize()

        # Set initial content
        store.set_block(
            block_type=CoreBlockType.human,
            content="Original content",
            updated_by="owner",
            provenance=[],
        )

        # Create snapshot
        snapshot = store.create_snapshot(commit_id="commit_001")

        # Modify content
        store.set_block(
            block_type=CoreBlockType.human,
            content="Modified content",
            updated_by="agent",
            provenance=[],
        )

        # Verify modification
        block = store.get_block(CoreBlockType.human)
        assert block.content == "Modified content"

        # Restore snapshot
        store.restore_snapshot(snapshot)

        # Verify restoration
        block = store.get_block(CoreBlockType.human)
        assert block.content == "Original content"

    def test_bootstrap_from_user_file(self, tmp_data_dir):
        """Test bootstrapping human block from USER.md."""
        # Create USER.md file
        user_file = tmp_data_dir / "USER.md"
        user_file.write_text("# User Profile\nName: Test User\nPreferences: Dark mode")

        store = CoreBlockStore(data_dir=tmp_data_dir)
        store.initialize()

        block = store.bootstrap_from_user_file(user_file)

        assert block is not None
        assert "Test User" in block.content
        assert block.block_type == CoreBlockType.human

    def test_total_tokens(self, tmp_data_dir):
        """Test total token calculation."""
        store = CoreBlockStore(data_dir=tmp_data_dir)
        store.initialize()

        store.set_block(
            block_type=CoreBlockType.persona,
            content="Short content",  # ~3 tokens
            updated_by="system",
            provenance=[],
        )

        total = store.total_tokens()
        assert total > 0


# ---------------------------------------------------------------------------
# Token Estimation Tests
# ---------------------------------------------------------------------------
class TestTokenEstimation:
    """Tests for token estimation utility."""

    def test_estimate_empty(self):
        """Test empty string estimation."""
        assert estimate_tokens("") == 0

    def test_estimate_basic(self):
        """Test basic estimation."""
        # ~4 chars per token
        text = "Hello world"  # 11 chars -> ~3 tokens
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert tokens < 10

    def test_estimate_longer(self):
        """Test longer text estimation."""
        text = "a" * 400  # 400 chars -> ~100 tokens
        tokens = estimate_tokens(text)
        assert 80 < tokens < 120


# ---------------------------------------------------------------------------
# ID Generation Tests
# ---------------------------------------------------------------------------
class TestIdGeneration:
    """Tests for ID generation utility."""

    def test_generate_id_length(self):
        """Test that generated IDs have correct length."""
        id1 = generate_id()
        assert len(id1) == 16

    def test_generate_id_unique(self):
        """Test that generated IDs are unique."""
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100  # All unique
