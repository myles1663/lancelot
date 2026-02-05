"""
Tests for Memory vNext SQLite stores with FTS5 search.

These tests validate:
- SQLite persistence for tiered memory
- FTS5 full-text search functionality
- TTL/expiration handling
- Confidence decay
- Multi-tier search
"""

import os
import pytest
from datetime import datetime, timedelta
from pathlib import Path

# Enable feature flag for testing
os.environ["FEATURE_MEMORY_VNEXT"] = "true"

from src.core.memory.schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
    ProvenanceType,
)
from src.core.memory.sqlite_store import (
    MemoryItemStore,
    MemoryStoreManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def working_store(tmp_data_dir):
    """Provide an initialized working memory store."""
    store = MemoryItemStore(data_dir=tmp_data_dir, tier=MemoryTier.working)
    store.initialize()
    yield store
    store.close()


@pytest.fixture
def archival_store(tmp_data_dir):
    """Provide an initialized archival memory store."""
    store = MemoryItemStore(data_dir=tmp_data_dir, tier=MemoryTier.archival)
    store.initialize()
    yield store
    store.close()


@pytest.fixture
def store_manager(tmp_data_dir):
    """Provide an initialized store manager."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    yield manager
    manager.close_all()


def create_test_item(
    tier: MemoryTier = MemoryTier.working,
    title: str = "Test Item",
    content: str = "Test content for the item",
    namespace: str = "global",
    tags: list[str] | None = None,
    confidence: float = 0.5,
    expires_at: datetime | None = None,
    decay_half_life_days: int | None = None,
) -> MemoryItem:
    """Create a test memory item."""
    return MemoryItem(
        tier=tier,
        title=title,
        content=content,
        namespace=namespace,
        tags=tags or [],
        confidence=confidence,
        expires_at=expires_at,
        decay_half_life_days=decay_half_life_days,
        provenance=[
            Provenance(
                type=ProvenanceType.system,
                ref="test",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Basic CRUD Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStoreCRUD:
    """Tests for basic CRUD operations."""

    def test_store_initialization(self, tmp_data_dir):
        """Test store creates database file."""
        store = MemoryItemStore(data_dir=tmp_data_dir, tier=MemoryTier.working)
        store.initialize()

        db_file = tmp_data_dir / "memory" / "working_memory.sqlite"
        assert db_file.exists()
        store.close()

    def test_insert_and_get(self, working_store):
        """Test inserting and retrieving an item."""
        item = create_test_item(title="My Task", content="Do something important")
        item_id = working_store.insert(item)

        retrieved = working_store.get(item_id)
        assert retrieved is not None
        assert retrieved.title == "My Task"
        assert retrieved.content == "Do something important"

    def test_insert_duplicate_fails(self, working_store):
        """Test that inserting duplicate ID fails."""
        item = create_test_item()
        working_store.insert(item)

        with pytest.raises(ValueError, match="already exists"):
            working_store.insert(item)

    def test_update(self, working_store):
        """Test updating an item."""
        item = create_test_item(title="Original")
        working_store.insert(item)

        # Modify and update
        item.title = "Updated"
        item.content = "New content"
        result = working_store.update(item)

        assert result is True
        retrieved = working_store.get(item.id)
        assert retrieved.title == "Updated"
        assert retrieved.content == "New content"

    def test_update_nonexistent(self, working_store):
        """Test updating a nonexistent item returns False."""
        item = create_test_item()
        result = working_store.update(item)
        assert result is False

    def test_delete(self, working_store):
        """Test deleting an item."""
        item = create_test_item()
        working_store.insert(item)

        result = working_store.delete(item.id)
        assert result is True

        retrieved = working_store.get(item.id)
        assert retrieved is None

    def test_delete_nonexistent(self, working_store):
        """Test deleting a nonexistent item returns False."""
        result = working_store.delete("nonexistent_id")
        assert result is False

    def test_get_nonexistent(self, working_store):
        """Test getting a nonexistent item returns None."""
        retrieved = working_store.get("nonexistent_id")
        assert retrieved is None


# ---------------------------------------------------------------------------
# List and Filter Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStoreList:
    """Tests for listing and filtering items."""

    def test_list_all(self, working_store):
        """Test listing all items."""
        for i in range(5):
            item = create_test_item(title=f"Item {i}")
            working_store.insert(item)

        items = working_store.list_items()
        assert len(items) == 5

    def test_list_by_namespace(self, working_store):
        """Test filtering by namespace."""
        item1 = create_test_item(namespace="global")
        item2 = create_test_item(namespace="quest:abc")
        item3 = create_test_item(namespace="quest:abc")

        working_store.insert(item1)
        working_store.insert(item2)
        working_store.insert(item3)

        global_items = working_store.list_items(namespace="global")
        assert len(global_items) == 1

        quest_items = working_store.list_items(namespace="quest:abc")
        assert len(quest_items) == 2

    def test_list_by_status(self, working_store):
        """Test filtering by status."""
        item1 = create_test_item()
        item2 = create_test_item()

        working_store.insert(item1)
        working_store.insert(item2)

        # Quarantine one item
        working_store.update_status(item2.id, MemoryStatus.quarantined)

        active = working_store.list_items(status=MemoryStatus.active)
        assert len(active) == 1

        quarantined = working_store.list_items(status=MemoryStatus.quarantined)
        assert len(quarantined) == 1

    def test_list_by_tags(self, working_store):
        """Test filtering by tags."""
        item1 = create_test_item(tags=["task", "urgent"])
        item2 = create_test_item(tags=["task"])
        item3 = create_test_item(tags=["note"])

        working_store.insert(item1)
        working_store.insert(item2)
        working_store.insert(item3)

        task_items = working_store.list_items(tags=["task"])
        assert len(task_items) == 2

        urgent_items = working_store.list_items(tags=["urgent"])
        assert len(urgent_items) == 1

    def test_list_excludes_expired(self, working_store):
        """Test that expired items are excluded by default."""
        valid_item = create_test_item(title="Valid")
        expired_item = create_test_item(
            title="Expired",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )

        working_store.insert(valid_item)
        working_store.insert(expired_item)

        items = working_store.list_items()
        assert len(items) == 1
        assert items[0].title == "Valid"

        # Include expired
        all_items = working_store.list_items(include_expired=True)
        assert len(all_items) == 2

    def test_list_pagination(self, working_store):
        """Test pagination with limit and offset."""
        for i in range(10):
            item = create_test_item(title=f"Item {i}")
            working_store.insert(item)

        page1 = working_store.list_items(limit=3, offset=0)
        assert len(page1) == 3

        page2 = working_store.list_items(limit=3, offset=3)
        assert len(page2) == 3

        # No overlap
        page1_ids = {item.id for item in page1}
        page2_ids = {item.id for item in page2}
        assert page1_ids.isdisjoint(page2_ids)


# ---------------------------------------------------------------------------
# FTS5 Search Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStoreSearch:
    """Tests for FTS5 full-text search."""

    def test_search_by_title(self, working_store):
        """Test searching by title."""
        item1 = create_test_item(title="Python programming guide")
        item2 = create_test_item(title="JavaScript tutorial")
        item3 = create_test_item(title="Python data science")

        working_store.insert(item1)
        working_store.insert(item2)
        working_store.insert(item3)

        results = working_store.search("Python")
        assert len(results) == 2

    def test_search_by_content(self, working_store):
        """Test searching by content."""
        item1 = create_test_item(
            title="Task 1",
            content="Need to refactor the authentication module"
        )
        item2 = create_test_item(
            title="Task 2",
            content="Add new user interface components"
        )

        working_store.insert(item1)
        working_store.insert(item2)

        results = working_store.search("authentication")
        assert len(results) == 1
        assert results[0].title == "Task 1"

    def test_search_with_namespace_filter(self, working_store):
        """Test search with namespace filter."""
        item1 = create_test_item(
            title="Quest task",
            namespace="quest:abc",
            content="Complete the mission"
        )
        item2 = create_test_item(
            title="Global task",
            namespace="global",
            content="Complete global mission"
        )

        working_store.insert(item1)
        working_store.insert(item2)

        results = working_store.search("mission", namespace="quest:abc")
        assert len(results) == 1
        assert results[0].namespace == "quest:abc"

    def test_search_excludes_quarantined(self, working_store):
        """Test that search excludes quarantined items by default."""
        item = create_test_item(title="Sensitive data", content="Secret information")
        working_store.insert(item)
        working_store.update_status(item.id, MemoryStatus.quarantined)

        results = working_store.search("sensitive")
        assert len(results) == 0

    def test_search_similar_with_scores(self, working_store):
        """Test search with relevance scores."""
        item1 = create_test_item(
            title="Python Python Python",
            content="Python programming language"
        )
        item2 = create_test_item(
            title="Python basics",
            content="Learn programming"
        )

        working_store.insert(item1)
        working_store.insert(item2)

        results = working_store.search_similar("Python")
        assert len(results) == 2
        # Results should have scores
        assert all(isinstance(r[1], float) for r in results)


# ---------------------------------------------------------------------------
# Expiration Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStoreExpiration:
    """Tests for TTL and expiration handling."""

    def test_delete_expired(self, working_store):
        """Test deleting expired items."""
        valid = create_test_item(title="Valid")
        expired1 = create_test_item(
            title="Expired 1",
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        expired2 = create_test_item(
            title="Expired 2",
            expires_at=datetime.utcnow() - timedelta(days=1),
        )

        working_store.insert(valid)
        working_store.insert(expired1)
        working_store.insert(expired2)

        count = working_store.delete_expired()
        assert count == 2

        items = working_store.list_items(include_expired=True)
        assert len(items) == 1
        assert items[0].title == "Valid"


# ---------------------------------------------------------------------------
# Confidence Decay Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStoreDecay:
    """Tests for confidence decay."""

    def test_apply_decay(self, archival_store):
        """Test applying confidence decay."""
        item = create_test_item(
            tier=MemoryTier.archival,
            confidence=1.0,
            decay_half_life_days=30,
        )
        archival_store.insert(item)

        # Apply 30 days of decay (should halve confidence)
        count = archival_store.apply_decay(days_elapsed=30)
        assert count == 1

        retrieved = archival_store.get(item.id)
        assert retrieved is not None
        assert 0.45 < retrieved.confidence < 0.55  # ~0.5 with some tolerance

    def test_decay_only_affects_items_with_half_life(self, archival_store):
        """Test that decay only affects items with decay_half_life_days."""
        with_decay = create_test_item(
            tier=MemoryTier.archival,
            title="With decay",
            confidence=1.0,
            decay_half_life_days=30,
        )
        without_decay = create_test_item(
            tier=MemoryTier.archival,
            title="No decay",
            confidence=1.0,
            decay_half_life_days=None,
        )

        archival_store.insert(with_decay)
        archival_store.insert(without_decay)

        archival_store.apply_decay(days_elapsed=30)

        retrieved_with = archival_store.get(with_decay.id)
        retrieved_without = archival_store.get(without_decay.id)

        assert retrieved_with.confidence < 1.0
        assert retrieved_without.confidence == 1.0


# ---------------------------------------------------------------------------
# Count Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStoreCount:
    """Tests for count operations."""

    def test_count_all(self, working_store):
        """Test counting all items."""
        for i in range(5):
            working_store.insert(create_test_item(title=f"Item {i}"))

        count = working_store.count()
        assert count == 5

    def test_count_by_status(self, working_store):
        """Test counting by status."""
        item1 = create_test_item()
        item2 = create_test_item()

        working_store.insert(item1)
        working_store.insert(item2)
        working_store.update_status(item2.id, MemoryStatus.quarantined)

        active_count = working_store.count(status=MemoryStatus.active)
        quarantined_count = working_store.count(status=MemoryStatus.quarantined)

        assert active_count == 1
        assert quarantined_count == 1


# ---------------------------------------------------------------------------
# Batch Operations Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStoreBatch:
    """Tests for batch operations."""

    def test_get_items_by_ids(self, working_store):
        """Test getting multiple items by IDs."""
        items = []
        for i in range(5):
            item = create_test_item(title=f"Item {i}")
            working_store.insert(item)
            items.append(item)

        # Get subset
        ids_to_fetch = [items[0].id, items[2].id, items[4].id]
        results = working_store.get_items_by_ids(ids_to_fetch)

        assert len(results) == 3
        result_ids = {r.id for r in results}
        assert result_ids == set(ids_to_fetch)

    def test_get_items_by_ids_empty(self, working_store):
        """Test getting items with empty ID list."""
        results = working_store.get_items_by_ids([])
        assert results == []


# ---------------------------------------------------------------------------
# Store Manager Tests
# ---------------------------------------------------------------------------
class TestMemoryStoreManager:
    """Tests for MemoryStoreManager."""

    def test_get_stores(self, store_manager):
        """Test getting stores for different tiers."""
        working = store_manager.working
        episodic = store_manager.episodic
        archival = store_manager.archival

        assert working.tier == MemoryTier.working
        assert episodic.tier == MemoryTier.episodic
        assert archival.tier == MemoryTier.archival

    def test_core_tier_raises(self, store_manager):
        """Test that core tier raises an error."""
        with pytest.raises(ValueError, match="Core tier"):
            store_manager.get_store(MemoryTier.core)

    def test_search_all(self, store_manager):
        """Test searching across all tiers."""
        # Add items to different tiers
        working_item = create_test_item(
            tier=MemoryTier.working,
            title="Working memory task",
            content="Need to do Python work"
        )
        archival_item = create_test_item(
            tier=MemoryTier.archival,
            title="Archival fact",
            content="Python is a programming language"
        )

        store_manager.working.insert(working_item)
        store_manager.archival.insert(archival_item)

        results = store_manager.search_all("Python")
        assert len(results) == 2

    def test_stores_are_cached(self, store_manager):
        """Test that stores are cached and reused."""
        store1 = store_manager.working
        store2 = store_manager.working

        assert store1 is store2


# ---------------------------------------------------------------------------
# Persistence Tests
# ---------------------------------------------------------------------------
class TestMemoryItemStorePersistence:
    """Tests for data persistence."""

    def test_persistence_across_instances(self, tmp_data_dir):
        """Test that data persists across store instances."""
        # Create and populate store
        store1 = MemoryItemStore(data_dir=tmp_data_dir, tier=MemoryTier.working)
        store1.initialize()
        item = create_test_item(title="Persistent item")
        store1.insert(item)
        store1.close()

        # Create new instance and verify data
        store2 = MemoryItemStore(data_dir=tmp_data_dir, tier=MemoryTier.working)
        store2.initialize()

        retrieved = store2.get(item.id)
        assert retrieved is not None
        assert retrieved.title == "Persistent item"
        store2.close()

    def test_fts_index_rebuilds(self, tmp_data_dir):
        """Test that FTS index works after reopening."""
        # Create and populate store
        store1 = MemoryItemStore(data_dir=tmp_data_dir, tier=MemoryTier.working)
        store1.initialize()
        item = create_test_item(title="Searchable content", content="Unique keyword")
        store1.insert(item)
        store1.close()

        # Reopen and search
        store2 = MemoryItemStore(data_dir=tmp_data_dir, tier=MemoryTier.working)
        store2.initialize()

        results = store2.search("Unique")
        assert len(results) == 1
        store2.close()
