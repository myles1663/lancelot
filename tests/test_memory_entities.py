"""Tests for entity-aware structured memory retrieval."""

from __future__ import annotations

from src.core.memory.entities import extract_entities, normalize_entity
from src.core.memory.schemas import MemoryItem, MemoryTier
from src.core.memory.sqlite_store import MemoryStoreManager


def test_extract_entities_from_query():
    """The local extractor recognizes project, file, and date-like references."""
    entities = extract_entities("What changed for Project Atlas in src/core/memory/api.py on 2026-05-02?")
    values = {(entity.entity_type, entity.entity_normalized) for entity in entities}

    assert ("project", "atlas in src/core/memory/api.py on 2026-05-02") not in values
    assert ("file", "src/core/memory/api.py") in values
    assert ("date", "2026-05-02") in values
    assert any(entity.entity_normalized == "project atlas" or entity.entity_normalized == "atlas" for entity in entities)


def test_normalize_entity_is_case_and_accent_insensitive():
    """Entity normalization makes common spelling variants matchable."""
    assert normalize_entity("LancéLot  Alpha") == "lancelot alpha"


def test_metadata_entity_search_surfaces_fts_miss(tmp_data_dir):
    """Entity lookup finds project-scoped memories even when content lacks the project name."""
    manager = MemoryStoreManager(data_dir=tmp_data_dir)
    item = MemoryItem(
        id="entity-alpha",
        tier=MemoryTier.episodic,
        title="Deployment decision",
        content="The release train should wait for receipt verification.",
        confidence=0.9,
        metadata={"project_id": "Atlas"},
    )
    manager.episodic.insert(item)

    fts_results = manager.episodic.search("Atlas")
    entity_results = manager.episodic.search_by_entities("Atlas")
    blended_results = manager.search_all("Atlas", tiers=[MemoryTier.episodic])

    assert fts_results == []
    assert [result.id for result in entity_results] == ["entity-alpha"]
    assert [result.id for result in blended_results] == ["entity-alpha"]
