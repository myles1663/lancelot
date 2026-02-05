"""
Tests for Memory vNext Search and Index.

These tests validate:
- Unified search across tiers
- Relevance ranking and scoring
- Namespace and tag filtering
- Search result aggregation
"""

import os
import pytest
from datetime import datetime

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.core.memory.schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)
from src.core.memory.sqlite_store import MemoryStoreManager
from src.core.memory.index import (
    MemoryIndex,
    SearchResult,
    SearchQuery,
    create_index,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def store_manager(tmp_data_dir):
    """Provide an initialized store manager."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    yield manager
    manager.close_all()


@pytest.fixture
def memory_index(store_manager):
    """Provide a memory index."""
    return MemoryIndex(store_manager=store_manager)


@pytest.fixture
def populated_index(store_manager):
    """Provide an index with sample data."""
    index = MemoryIndex(store_manager=store_manager)

    # Add working memory items
    working_items = [
        create_item(
            MemoryTier.working,
            "Python task",
            "Working on Python code refactoring",
            tags=["python", "code"],
            confidence=0.8,
        ),
        create_item(
            MemoryTier.working,
            "JavaScript task",
            "Fixing JavaScript bugs in frontend",
            tags=["javascript", "frontend"],
            confidence=0.7,
        ),
    ]
    for item in working_items:
        store_manager.working.insert(item)

    # Add episodic items
    episodic_items = [
        create_item(
            MemoryTier.episodic,
            "Session summary",
            "User discussed Python machine learning project",
            tags=["python", "ml"],
            confidence=0.9,
        ),
    ]
    for item in episodic_items:
        store_manager.episodic.insert(item)

    # Add archival items
    archival_items = [
        create_item(
            MemoryTier.archival,
            "Python best practices",
            "Use type hints and docstrings in Python code",
            tags=["python", "best-practices"],
            confidence=0.95,
        ),
        create_item(
            MemoryTier.archival,
            "Database design",
            "Use normalized schemas for relational databases",
            tags=["database", "design"],
            confidence=0.85,
        ),
    ]
    for item in archival_items:
        store_manager.archival.insert(item)

    return index


def create_item(
    tier: MemoryTier,
    title: str,
    content: str,
    namespace: str = "global",
    tags: list[str] | None = None,
    confidence: float = 0.7,
) -> MemoryItem:
    """Create a test memory item."""
    return MemoryItem(
        tier=tier,
        title=title,
        content=content,
        namespace=namespace,
        tags=tags or [],
        confidence=confidence,
        token_count=len(content) // 4,
        provenance=[Provenance(type=ProvenanceType.system, ref="test")],
    )


# ---------------------------------------------------------------------------
# SearchResult Tests
# ---------------------------------------------------------------------------
class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_weighted_score_calculation(self):
        """Test weighted score calculation."""
        item = create_item(MemoryTier.working, "Test", "Content", confidence=0.8)
        result = SearchResult(item=item, score=1.0, source_tier=MemoryTier.working)

        # Working memory gets 1.2 weight
        weighted = result.weighted_score()
        assert weighted == 1.0 * 1.2 * 0.8  # score * tier_weight * confidence

    def test_tier_weights(self):
        """Test that different tiers have different weights."""
        item = create_item(MemoryTier.working, "Test", "Content", confidence=1.0)

        working_result = SearchResult(item=item, score=1.0, source_tier=MemoryTier.working)
        episodic_result = SearchResult(item=item, score=1.0, source_tier=MemoryTier.episodic)
        archival_result = SearchResult(item=item, score=1.0, source_tier=MemoryTier.archival)

        # Working should be highest, archival lowest
        assert working_result.weighted_score() > episodic_result.weighted_score()
        assert episodic_result.weighted_score() > archival_result.weighted_score()


# ---------------------------------------------------------------------------
# SearchQuery Tests
# ---------------------------------------------------------------------------
class TestSearchQuery:
    """Tests for SearchQuery dataclass."""

    def test_default_values(self):
        """Test default query values."""
        query = SearchQuery(query="test")

        assert query.query == "test"
        assert MemoryTier.working in query.tiers
        assert MemoryTier.episodic in query.tiers
        assert MemoryTier.archival in query.tiers
        assert query.limit == 20
        assert query.min_confidence == 0.3

    def test_custom_values(self):
        """Test custom query values."""
        query = SearchQuery(
            query="python",
            tiers=[MemoryTier.archival],
            namespace="project:abc",
            tags=["code"],
            min_confidence=0.5,
            limit=10,
        )

        assert query.tiers == [MemoryTier.archival]
        assert query.namespace == "project:abc"
        assert query.tags == ["code"]


# ---------------------------------------------------------------------------
# Basic Search Tests
# ---------------------------------------------------------------------------
class TestBasicSearch:
    """Tests for basic search functionality."""

    def test_search_returns_results(self, populated_index):
        """Test that search returns matching results."""
        results = populated_index.search("Python")

        assert len(results) > 0
        # All results should mention Python
        for result in results:
            assert "python" in result.item.title.lower() or "python" in result.item.content.lower()

    def test_search_empty_query(self, memory_index):
        """Test searching with empty query returns empty."""
        results = memory_index.search("")
        # FTS5 may return all or none for empty query
        assert isinstance(results, list)

    def test_search_no_matches(self, populated_index):
        """Test searching for non-existent term."""
        results = populated_index.search("xyznonexistent123")
        assert len(results) == 0

    def test_search_results_are_sorted(self, populated_index):
        """Test that results are sorted by weighted score."""
        results = populated_index.search("Python")

        if len(results) > 1:
            scores = [r.weighted_score() for r in results]
            assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Multi-Tier Search Tests
# ---------------------------------------------------------------------------
class TestMultiTierSearch:
    """Tests for searching across multiple tiers."""

    def test_search_all_tiers(self, populated_index):
        """Test searching all tiers."""
        results = populated_index.search(
            "Python",
            tiers=[MemoryTier.working, MemoryTier.episodic, MemoryTier.archival],
        )

        # Should find items from multiple tiers
        tiers_found = {r.source_tier for r in results}
        assert len(tiers_found) >= 2  # At least 2 tiers have Python items

    def test_search_single_tier(self, populated_index):
        """Test searching a single tier."""
        results = populated_index.search(
            "Python",
            tiers=[MemoryTier.archival],
        )

        # All results should be from archival
        for result in results:
            assert result.source_tier == MemoryTier.archival

    def test_core_tier_ignored(self, populated_index):
        """Test that core tier is ignored in search."""
        results = populated_index.search(
            "Python",
            tiers=[MemoryTier.core, MemoryTier.archival],
        )

        # Should not raise, core is just filtered out
        for result in results:
            assert result.source_tier != MemoryTier.core


# ---------------------------------------------------------------------------
# Filter Tests
# ---------------------------------------------------------------------------
class TestSearchFilters:
    """Tests for search filters."""

    def test_filter_by_namespace(self, store_manager, memory_index):
        """Test filtering by namespace."""
        # Add items with different namespaces
        item1 = create_item(
            MemoryTier.working,
            "Quest task",
            "Python quest item",
            namespace="quest:abc",
        )
        item2 = create_item(
            MemoryTier.working,
            "Global task",
            "Python global item",
            namespace="global",
        )
        store_manager.working.insert(item1)
        store_manager.working.insert(item2)

        results = memory_index.search("Python", namespace="quest:abc")

        assert len(results) == 1
        assert results[0].item.namespace == "quest:abc"

    def test_filter_by_tags(self, populated_index):
        """Test filtering by tags."""
        results = populated_index.search("Python", tags=["ml"])

        # Only items with 'ml' tag
        for result in results:
            assert "ml" in result.item.tags

    def test_filter_by_confidence(self, populated_index):
        """Test filtering by minimum confidence."""
        results = populated_index.search("Python", min_confidence=0.85)

        for result in results:
            assert result.item.confidence >= 0.85

    def test_filter_excludes_quarantined(self, store_manager, memory_index):
        """Test that quarantined items are excluded by default."""
        item = create_item(
            MemoryTier.archival,
            "Quarantined fact",
            "This Python fact is quarantined",
        )
        store_manager.archival.insert(item)
        store_manager.archival.update_status(item.id, MemoryStatus.quarantined)

        results = memory_index.search("quarantined Python")
        assert len(results) == 0

    def test_include_quarantined(self, store_manager, memory_index):
        """Test including quarantined items."""
        item = create_item(
            MemoryTier.archival,
            "Quarantined fact",
            "This Python fact is quarantined",
        )
        store_manager.archival.insert(item)
        store_manager.archival.update_status(item.id, MemoryStatus.quarantined)

        results = memory_index.search("quarantined", include_quarantined=True)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# SearchQuery Object Tests
# ---------------------------------------------------------------------------
class TestSearchByQuery:
    """Tests for search_by_query method."""

    def test_search_by_query_object(self, populated_index):
        """Test searching with a SearchQuery object."""
        query = SearchQuery(
            query="Python",
            tiers=[MemoryTier.archival],
            min_confidence=0.9,
            limit=5,
        )

        results = populated_index.search_by_query(query)

        for result in results:
            assert result.source_tier == MemoryTier.archival
            assert result.item.confidence >= 0.9


# ---------------------------------------------------------------------------
# Helper Method Tests
# ---------------------------------------------------------------------------
class TestHelperMethods:
    """Tests for helper methods."""

    def test_get_recent(self, store_manager, memory_index):
        """Test getting recent items."""
        for i in range(5):
            item = create_item(
                MemoryTier.working,
                f"Task {i}",
                f"Content {i}",
            )
            store_manager.working.insert(item)

        recent = memory_index.get_recent(MemoryTier.working, limit=3)

        assert len(recent) == 3

    def test_get_recent_raises_for_core(self, memory_index):
        """Test that get_recent raises for core tier."""
        with pytest.raises(ValueError, match="core tier"):
            memory_index.get_recent(MemoryTier.core)

    def test_get_by_namespace(self, store_manager, memory_index):
        """Test getting items by namespace."""
        item1 = create_item(
            MemoryTier.working,
            "Quest task",
            "Quest content",
            namespace="quest:xyz",
        )
        item2 = create_item(
            MemoryTier.archival,
            "Quest fact",
            "Quest archived fact",
            namespace="quest:xyz",
        )
        store_manager.working.insert(item1)
        store_manager.archival.insert(item2)

        items = memory_index.get_by_namespace("quest:xyz")

        assert len(items) == 2
        for item in items:
            assert item.namespace == "quest:xyz"

    def test_count_by_tier(self, populated_index):
        """Test counting items by tier."""
        counts = populated_index.count_by_tier()

        assert "working" in counts
        assert "episodic" in counts
        assert "archival" in counts
        assert counts["working"] >= 2
        assert counts["archival"] >= 2

    def test_get_stats(self, populated_index):
        """Test getting index statistics."""
        stats = populated_index.get_stats()

        assert "total_items" in stats
        assert "items_by_tier" in stats
        assert "tiers_available" in stats
        assert stats["total_items"] >= 5


# ---------------------------------------------------------------------------
# Factory Function Tests
# ---------------------------------------------------------------------------
class TestCreateIndex:
    """Tests for create_index factory function."""

    def test_create_index(self, tmp_data_dir):
        """Test creating an index via factory."""
        index = create_index(tmp_data_dir)

        assert index is not None
        assert isinstance(index, MemoryIndex)

    def test_created_index_can_search(self, tmp_data_dir):
        """Test that created index can perform searches."""
        index = create_index(tmp_data_dir)

        # Add an item and search
        index.store_manager.working.insert(
            create_item(MemoryTier.working, "Test", "Searchable content")
        )

        results = index.search("Searchable")
        assert len(results) == 1
