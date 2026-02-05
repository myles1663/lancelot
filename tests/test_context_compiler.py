"""
Tests for Memory vNext Context Compiler.

These tests validate:
- Deterministic compilation order
- Token budget enforcement
- Core block inclusion/exclusion
- Working memory filtering
- Retrieval integration
- Receipt data generation
"""

import os
import pytest
from datetime import datetime, timedelta

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.core.memory.schemas import (
    CoreBlock,
    CoreBlockType,
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)
from src.core.memory.store import CoreBlockStore
from src.core.memory.compiler import (
    ContextCompiler,
    ContextCompilerService,
    CORE_BLOCK_ORDER,
)
from src.core.memory.config import (
    MemoryConfig,
    MAX_CORE_BLOCKS_TOTAL_TOKENS,
)


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
def populated_core_store(core_store):
    """Provide a core store with sample content."""
    prov = Provenance(type=ProvenanceType.system, ref="test")

    core_store.set_block(
        block_type=CoreBlockType.persona,
        content="I am Lancelot, a helpful AI assistant.",
        updated_by="system",
        provenance=[prov],
    )
    core_store.set_block(
        block_type=CoreBlockType.human,
        content="User prefers concise responses.",
        updated_by="owner",
        provenance=[prov],
    )
    core_store.set_block(
        block_type=CoreBlockType.operating_rules,
        content="Always verify before taking action.",
        updated_by="system",
        provenance=[prov],
    )
    core_store.set_block(
        block_type=CoreBlockType.mission,
        content="Complete the project by end of week.",
        updated_by="owner",
        provenance=[prov],
    )

    return core_store


@pytest.fixture
def compiler(populated_core_store):
    """Provide a context compiler."""
    return ContextCompiler(
        core_store=populated_core_store,
        soul_version="v1.0.0",
    )


def create_working_item(
    title: str = "Task",
    content: str = "Task content",
    namespace: str = "global",
    confidence: float = 0.7,
    tags: list[str] | None = None,
) -> MemoryItem:
    """Create a working memory item for testing."""
    return MemoryItem(
        tier=MemoryTier.working,
        title=title,
        content=content,
        namespace=namespace,
        confidence=confidence,
        tags=tags or [],
        token_count=len(content) // 4,
        provenance=[Provenance(type=ProvenanceType.system, ref="test")],
    )


def create_archival_item(
    title: str = "Fact",
    content: str = "Archived knowledge",
    confidence: float = 0.8,
) -> MemoryItem:
    """Create an archival memory item for testing."""
    return MemoryItem(
        tier=MemoryTier.archival,
        title=title,
        content=content,
        confidence=confidence,
        token_count=len(content) // 4,
        provenance=[Provenance(type=ProvenanceType.system, ref="test")],
    )


# ---------------------------------------------------------------------------
# Core Block Order Tests
# ---------------------------------------------------------------------------
class TestCoreBlockOrder:
    """Tests for core block compilation order."""

    def test_canonical_order(self):
        """Test that CORE_BLOCK_ORDER is defined correctly."""
        assert CORE_BLOCK_ORDER == [
            CoreBlockType.persona,
            CoreBlockType.human,
            CoreBlockType.operating_rules,
            CoreBlockType.mission,
            CoreBlockType.workspace_state,
        ]

    def test_blocks_compiled_in_order(self, compiler):
        """Test that blocks appear in canonical order in output."""
        ctx = compiler.compile(objective="Test task")

        # Check included blocks are in order
        expected_order = [
            CoreBlockType.persona,
            CoreBlockType.human,
            CoreBlockType.operating_rules,
            CoreBlockType.mission,
        ]
        assert ctx.included_blocks == expected_order

    def test_rendered_prompt_contains_blocks_in_order(self, compiler):
        """Test that rendered prompt has blocks in correct order."""
        ctx = compiler.compile(objective="Test task")

        # Find positions in rendered prompt
        persona_pos = ctx.rendered_prompt.find("[PERSONA]")
        human_pos = ctx.rendered_prompt.find("[HUMAN]")
        rules_pos = ctx.rendered_prompt.find("[OPERATING_RULES]")
        mission_pos = ctx.rendered_prompt.find("[MISSION]")

        # Verify order
        assert persona_pos < human_pos < rules_pos < mission_pos


# ---------------------------------------------------------------------------
# Basic Compilation Tests
# ---------------------------------------------------------------------------
class TestBasicCompilation:
    """Tests for basic context compilation."""

    def test_compile_produces_context(self, compiler):
        """Test that compile produces a valid context."""
        ctx = compiler.compile(objective="Help with coding")

        assert ctx.objective == "Help with coding"
        assert ctx.context_id is not None
        assert ctx.compiler_version == "1.0.0"
        assert ctx.soul_version == "v1.0.0"

    def test_compile_with_quest_id(self, compiler):
        """Test compilation with quest ID."""
        ctx = compiler.compile(
            objective="Complete quest",
            quest_id="quest_123",
        )

        assert ctx.quest_id == "quest_123"
        assert "Quest: quest_123" in ctx.rendered_prompt

    def test_compile_with_crusader_mode(self, compiler):
        """Test compilation in crusader mode."""
        ctx = compiler.compile(
            objective="Execute task",
            mode="crusader",
        )

        assert ctx.mode == "crusader"
        assert "Mode: CRUSADER" in ctx.rendered_prompt

    def test_token_estimate_calculated(self, compiler):
        """Test that token estimate is calculated."""
        ctx = compiler.compile(objective="Test")

        assert ctx.token_estimate > 0
        assert "core_blocks" in ctx.token_breakdown
        assert ctx.token_breakdown["core_blocks"] > 0


# ---------------------------------------------------------------------------
# Core Block Exclusion Tests
# ---------------------------------------------------------------------------
class TestCoreBlockExclusion:
    """Tests for core block exclusion logic."""

    def test_empty_blocks_excluded(self, core_store):
        """Test that empty blocks are not included."""
        # workspace_state is empty by default
        compiler = ContextCompiler(core_store=core_store)
        ctx = compiler.compile(objective="Test")

        assert CoreBlockType.workspace_state not in ctx.included_blocks

    def test_quarantined_blocks_excluded(self, populated_core_store):
        """Test that quarantined blocks are excluded."""
        # Quarantine the human block
        populated_core_store.update_block_status(
            CoreBlockType.human,
            MemoryStatus.quarantined,
        )

        compiler = ContextCompiler(core_store=populated_core_store)
        ctx = compiler.compile(objective="Test")

        assert CoreBlockType.human not in ctx.included_blocks

        # Check exclusion recorded
        exclusions = [e for e in ctx.excluded_candidates if e["item_id"] == "core:human"]
        assert len(exclusions) == 1
        assert exclusions[0]["reason"] == "quarantined"


# ---------------------------------------------------------------------------
# Working Memory Tests
# ---------------------------------------------------------------------------
class TestWorkingMemoryCompilation:
    """Tests for working memory compilation."""

    def test_working_memory_included(self, compiler):
        """Test that working memory items are included."""
        items = [
            create_working_item(title="Task 1", content="First task"),
            create_working_item(title="Task 2", content="Second task"),
        ]

        ctx = compiler.compile(
            objective="Do tasks",
            working_items=items,
        )

        assert len(ctx.included_memory_item_ids) == 2
        assert "WORKING MEMORY" in ctx.rendered_prompt

    def test_quest_scoped_items_prioritized(self, compiler):
        """Test that quest-scoped items come first."""
        items = [
            create_working_item(
                title="Global Task",
                content="Global content",
                namespace="global",
                confidence=0.9,
            ),
            create_working_item(
                title="Quest Task",
                content="Quest content",
                namespace="quest:abc",
                confidence=0.5,  # Lower confidence but quest-scoped
            ),
        ]

        ctx = compiler.compile(
            objective="Do quest",
            quest_id="abc",
            working_items=items,
        )

        # Quest item should be included first (before global)
        prompt = ctx.rendered_prompt
        quest_pos = prompt.find("Quest Task")
        global_pos = prompt.find("Global Task")

        assert quest_pos < global_pos

    def test_expired_items_excluded(self, compiler):
        """Test that expired items are excluded."""
        expired = create_working_item(title="Expired")
        expired.expires_at = datetime.utcnow() - timedelta(hours=1)

        valid = create_working_item(title="Valid")

        ctx = compiler.compile(
            objective="Test",
            working_items=[expired, valid],
        )

        assert len(ctx.included_memory_item_ids) == 1

    def test_low_confidence_items_excluded(self, compiler):
        """Test that low confidence items are excluded."""
        low_conf = create_working_item(
            title="Low Confidence",
            confidence=0.1,  # Below threshold
        )
        high_conf = create_working_item(
            title="High Confidence",
            confidence=0.8,
        )

        ctx = compiler.compile(
            objective="Test",
            working_items=[low_conf, high_conf],
        )

        assert len(ctx.included_memory_item_ids) == 1
        # Check exclusion recorded
        exclusions = [e for e in ctx.excluded_candidates if e["reason"] == "low_confidence"]
        assert len(exclusions) == 1


# ---------------------------------------------------------------------------
# Retrieval Tests
# ---------------------------------------------------------------------------
class TestRetrievalCompilation:
    """Tests for retrieval compilation."""

    def test_retrieved_items_included(self, compiler):
        """Test that retrieved items are included."""
        items = [
            create_archival_item(title="Fact 1", content="Important fact"),
            create_archival_item(title="Fact 2", content="Another fact"),
        ]

        ctx = compiler.compile(
            objective="Learn facts",
            retrieved_items=items,
        )

        assert "RELEVANT MEMORIES" in ctx.rendered_prompt
        assert len(ctx.included_memory_item_ids) == 2

    def test_quarantined_retrieval_excluded(self, compiler):
        """Test that quarantined retrieved items are excluded."""
        quarantined = create_archival_item(title="Bad Fact")
        quarantined.status = MemoryStatus.quarantined

        valid = create_archival_item(title="Good Fact")

        ctx = compiler.compile(
            objective="Test",
            retrieved_items=[quarantined, valid],
        )

        assert len(ctx.included_memory_item_ids) == 1
        exclusions = [e for e in ctx.excluded_candidates if e["reason"] == "quarantined"]
        assert len(exclusions) == 1

    def test_duplicate_items_not_repeated(self, compiler):
        """Test that items in working memory aren't duplicated in retrieval."""
        item = create_working_item(title="Shared")

        ctx = compiler.compile(
            objective="Test",
            working_items=[item],
            retrieved_items=[item],  # Same item
        )

        # Should only appear once
        assert ctx.included_memory_item_ids.count(item.id) == 1


# ---------------------------------------------------------------------------
# Token Budget Tests
# ---------------------------------------------------------------------------
class TestTokenBudgets:
    """Tests for token budget enforcement."""

    def test_token_breakdown_calculated(self, compiler):
        """Test that token breakdown is calculated."""
        ctx = compiler.compile(objective="Test")

        assert "objective" in ctx.token_breakdown
        assert ctx.token_breakdown["objective"] > 0

    def test_working_memory_budget_enforced(self, compiler):
        """Test that working memory respects budget."""
        # Create many items that would exceed budget
        items = [
            create_working_item(
                title=f"Task {i}",
                content="x" * 1000,  # Large content
            )
            for i in range(100)
        ]

        ctx = compiler.compile(
            objective="Test",
            working_items=items,
        )

        # Should have budget exclusions
        budget_exclusions = [
            e for e in ctx.excluded_candidates
            if e["reason"] == "exceeded_budget"
        ]
        assert len(budget_exclusions) > 0


# ---------------------------------------------------------------------------
# Receipt Data Tests
# ---------------------------------------------------------------------------
class TestReceiptData:
    """Tests for receipt data generation."""

    def test_create_receipt_data(self, compiler):
        """Test receipt data generation."""
        ctx = compiler.compile(
            objective="Test objective",
            quest_id="quest_001",
            mode="normal",
        )

        receipt_data = compiler.create_receipt_data(ctx)

        assert receipt_data["context_id"] == ctx.context_id
        assert receipt_data["objective"] == "Test objective"
        assert receipt_data["quest_id"] == "quest_001"
        assert receipt_data["compiler_version"] == "1.0.0"
        assert "token_estimate" in receipt_data
        assert "token_breakdown" in receipt_data

    def test_receipt_includes_counts(self, compiler):
        """Test that receipt includes item counts."""
        items = [create_working_item() for _ in range(3)]

        ctx = compiler.compile(
            objective="Test",
            working_items=items,
        )

        receipt_data = compiler.create_receipt_data(ctx)

        assert receipt_data["included_memory_count"] == 3


# ---------------------------------------------------------------------------
# Compiler Service Tests
# ---------------------------------------------------------------------------
class TestContextCompilerService:
    """Tests for the high-level compiler service."""

    def test_service_initialization(self, tmp_data_dir):
        """Test service initializes stores correctly."""
        service = ContextCompilerService(
            data_dir=tmp_data_dir,
            soul_version="v2.0.0",
        )

        assert service.core_store is not None
        assert service.memory_manager is not None
        assert service.compiler.soul_version == "v2.0.0"

    def test_compile_for_objective(self, tmp_data_dir):
        """Test compiling for an objective."""
        service = ContextCompilerService(data_dir=tmp_data_dir)

        # Set up some core content
        prov = Provenance(type=ProvenanceType.system, ref="test")
        service.core_store.set_block(
            block_type=CoreBlockType.persona,
            content="I am a test assistant.",
            updated_by="system",
            provenance=[prov],
        )

        ctx = service.compile_for_objective(
            objective="Help the user",
            mode="normal",
        )

        assert ctx.objective == "Help the user"
        assert CoreBlockType.persona in ctx.included_blocks

    def test_get_core_blocks_summary(self, tmp_data_dir):
        """Test getting core blocks summary."""
        service = ContextCompilerService(data_dir=tmp_data_dir)

        prov = Provenance(type=ProvenanceType.system, ref="test")
        service.core_store.set_block(
            block_type=CoreBlockType.mission,
            content="Test mission",
            updated_by="owner",
            provenance=[prov],
        )

        summary = service.get_core_blocks_summary()

        assert "mission" in summary
        assert summary["mission"]["status"] == "active"
        assert summary["mission"]["updated_by"] == "owner"


# ---------------------------------------------------------------------------
# Determinism Tests
# ---------------------------------------------------------------------------
class TestDeterminism:
    """Tests for deterministic compilation."""

    def test_same_inputs_same_output(self, populated_core_store):
        """Test that same inputs produce same structure."""
        compiler = ContextCompiler(core_store=populated_core_store)

        ctx1 = compiler.compile(objective="Same objective")
        ctx2 = compiler.compile(objective="Same objective")

        # Structure should be the same (IDs will differ)
        assert ctx1.included_blocks == ctx2.included_blocks
        assert ctx1.token_estimate == ctx2.token_estimate
        assert ctx1.token_breakdown == ctx2.token_breakdown

    def test_deterministic_block_order_with_different_store_order(self, core_store):
        """Test blocks are always in canonical order regardless of store order."""
        prov = Provenance(type=ProvenanceType.system, ref="test")

        # Add blocks in non-canonical order
        core_store.set_block(
            block_type=CoreBlockType.mission,
            content="Mission content",
            updated_by="system",
            provenance=[prov],
        )
        core_store.set_block(
            block_type=CoreBlockType.persona,
            content="Persona content",
            updated_by="system",
            provenance=[prov],
        )
        core_store.set_block(
            block_type=CoreBlockType.human,
            content="Human content",
            updated_by="system",
            provenance=[prov],
        )

        compiler = ContextCompiler(core_store=core_store)
        ctx = compiler.compile(objective="Test")

        # Should still be in canonical order
        assert ctx.included_blocks == [
            CoreBlockType.persona,
            CoreBlockType.human,
            CoreBlockType.mission,
        ]
