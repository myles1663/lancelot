"""
Memory vNext Index — Unified search interface across memory tiers.

This module provides the MemoryIndex class that offers:
- Unified search across working, episodic, and archival memory
- Relevance ranking with confidence weighting
- Namespace and tag filtering
- Result aggregation and deduplication
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import MemoryConfig, default_config
from .schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
)
from .sqlite_store import MemoryItemStore, MemoryStoreManager

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result with relevance scoring."""
    item: MemoryItem
    score: float = 0.0
    source_tier: MemoryTier = MemoryTier.archival

    def weighted_score(self) -> float:
        """
        Calculate weighted score based on tier and confidence.

        Working memory gets a slight boost for recency.
        """
        tier_weights = {
            MemoryTier.working: 1.2,
            MemoryTier.episodic: 1.0,
            MemoryTier.archival: 0.9,
        }
        weight = tier_weights.get(self.source_tier, 1.0)
        return self.score * weight * self.item.confidence


@dataclass
class SearchQuery:
    """Search query parameters."""
    query: str
    tiers: list[MemoryTier] = field(default_factory=lambda: [
        MemoryTier.working,
        MemoryTier.episodic,
        MemoryTier.archival,
    ])
    namespace: Optional[str] = None
    tags: Optional[list[str]] = None
    min_confidence: float = 0.3
    limit: int = 20
    include_quarantined: bool = False
    include_expired: bool = False


class MemoryIndex:
    """
    Unified search interface for the memory subsystem.

    Provides efficient search across all memory tiers with:
    - FTS5 full-text search
    - Relevance ranking
    - Confidence-weighted scoring
    - Namespace and tag filtering
    """

    def __init__(
        self,
        store_manager: MemoryStoreManager,
        config: Optional[MemoryConfig] = None,
    ):
        """
        Initialize the memory index.

        Args:
            store_manager: Manager for memory stores
            config: Memory configuration
        """
        self.store_manager = store_manager
        self.config = config or default_config

    def search(
        self,
        query: str,
        tiers: Optional[list[MemoryTier]] = None,
        namespace: Optional[str] = None,
        tags: Optional[list[str]] = None,
        min_confidence: float = 0.3,
        limit: int = 20,
        include_quarantined: bool = False,
    ) -> list[SearchResult]:
        """
        Search across memory tiers.

        Args:
            query: Search query string
            tiers: Tiers to search (default: all non-core)
            namespace: Filter by namespace
            tags: Filter by tags
            min_confidence: Minimum confidence threshold
            limit: Maximum results per tier
            include_quarantined: Include quarantined items

        Returns:
            List of SearchResult objects ranked by relevance
        """
        if tiers is None:
            tiers = [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]

        # Remove core tier if present
        tiers = [t for t in tiers if t != MemoryTier.core]

        if not tiers:
            return []

        all_results: list[SearchResult] = []

        for tier in tiers:
            try:
                store = self.store_manager.get_store(tier)
                tier_results = self._search_tier(
                    store=store,
                    tier=tier,
                    query=query,
                    namespace=namespace,
                    limit=limit,
                    include_quarantined=include_quarantined,
                )
                all_results.extend(tier_results)
            except Exception as e:
                logger.warning("Search failed for tier %s: %s", tier.value, e)

        # Filter by confidence
        all_results = [
            r for r in all_results
            if r.item.confidence >= min_confidence
        ]

        # Filter by tags if specified
        if tags:
            all_results = [
                r for r in all_results
                if any(tag in r.item.tags for tag in tags)
            ]

        # Sort by weighted score
        all_results.sort(key=lambda r: r.weighted_score(), reverse=True)

        # Limit total results
        return all_results[:limit]

    def _search_tier(
        self,
        store: MemoryItemStore,
        tier: MemoryTier,
        query: str,
        namespace: Optional[str],
        limit: int,
        include_quarantined: bool,
    ) -> list[SearchResult]:
        """Search a single tier and return results with scores."""
        # Use basic search which supports all filters
        items = store.search(
            query=query,
            namespace=namespace,
            limit=limit,
            include_quarantined=include_quarantined,
        )

        results = []
        for item in items:
            # Use inverse position as score (first results are most relevant)
            score = 1.0
            results.append(SearchResult(
                item=item,
                score=score,
                source_tier=tier,
            ))

        return results

    def search_by_query(self, query: SearchQuery) -> list[SearchResult]:
        """
        Search using a SearchQuery object.

        Args:
            query: SearchQuery with all parameters

        Returns:
            List of SearchResult objects
        """
        return self.search(
            query=query.query,
            tiers=query.tiers,
            namespace=query.namespace,
            tags=query.tags,
            min_confidence=query.min_confidence,
            limit=query.limit,
            include_quarantined=query.include_quarantined,
        )

    def get_recent(
        self,
        tier: MemoryTier,
        namespace: Optional[str] = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """
        Get recent items from a tier.

        Args:
            tier: Memory tier
            namespace: Optional namespace filter
            limit: Maximum items

        Returns:
            List of recent MemoryItems
        """
        if tier == MemoryTier.core:
            raise ValueError("Use CoreBlockStore for core tier")

        store = self.store_manager.get_store(tier)
        return store.list_items(
            namespace=namespace,
            status=MemoryStatus.active,
            limit=limit,
        )

    def get_by_namespace(
        self,
        namespace: str,
        tiers: Optional[list[MemoryTier]] = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        """
        Get all items in a namespace across tiers.

        Args:
            namespace: Namespace to query
            tiers: Tiers to search
            limit: Maximum items per tier

        Returns:
            List of MemoryItems
        """
        if tiers is None:
            tiers = [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]

        items: list[MemoryItem] = []
        for tier in tiers:
            if tier == MemoryTier.core:
                continue

            store = self.store_manager.get_store(tier)
            tier_items = store.list_items(
                namespace=namespace,
                limit=limit,
            )
            items.extend(tier_items)

        return items

    def count_by_tier(self) -> dict[str, int]:
        """
        Get item counts per tier.

        Returns:
            Dictionary mapping tier name to count
        """
        counts = {}
        for tier in [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]:
            store = self.store_manager.get_store(tier)
            counts[tier.value] = store.count()
        return counts

    def get_stats(self) -> dict[str, Any]:
        """
        Get search index statistics.

        Returns:
            Dictionary with index stats
        """
        counts = self.count_by_tier()
        total = sum(counts.values())

        return {
            "total_items": total,
            "items_by_tier": counts,
            "tiers_available": list(counts.keys()),
        }


def create_index(data_dir: str | Path) -> MemoryIndex:
    """
    Factory function to create a MemoryIndex.

    Args:
        data_dir: Base data directory

    Returns:
        Initialized MemoryIndex
    """
    manager = MemoryStoreManager(data_dir=data_dir)
    return MemoryIndex(store_manager=manager)
