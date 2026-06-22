"""SQLite memory item search helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .entities import extract_entities
from .schemas import MemoryItem, MemoryStatus


class MemorySearchMixin:
    def list_items(
        self,
        namespace: Optional[str] = None,
        status: Optional[MemoryStatus] = None,
        tags: Optional[list[str]] = None,
        limit: int = 100,
        offset: int = 0,
        include_expired: bool = False,
    ) -> list[MemoryItem]:
        """
        List memory items with optional filters.

        Args:
            namespace: Filter by namespace
            status: Filter by status
            tags: Filter by tags (any match)
            limit: Maximum number of items to return
            offset: Offset for pagination
            include_expired: Whether to include expired items

        Returns:
            List of MemoryItem objects
        """
        self._ensure_initialized()

        conditions = ["tier = ?"]
        params: list[Any] = [self.tier.value]

        if namespace is not None:
            conditions.append("namespace = ?")
            params.append(namespace)

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)

        if not include_expired:
            conditions.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(datetime.utcnow().isoformat())

        if tags:
            # Match any of the provided tags
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            conditions.append(f"({' OR '.join(tag_conditions)})")

        where_clause = " AND ".join(conditions)
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT * FROM memory_items
                WHERE {where_clause}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            )
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def search(
        self,
        query: str,
        namespace: Optional[str] = None,
        status: Optional[MemoryStatus] = None,
        limit: int = 20,
        include_expired: bool = False,
        include_quarantined: bool = False,
        include_blobs: bool = False,
    ) -> list[MemoryItem]:
        """
        Full-text search using FTS5.

        Args:
            query: Search query (supports FTS5 syntax)
            namespace: Filter by namespace
            status: Filter by status
            limit: Maximum results
            include_expired: Whether to include expired items
            include_quarantined: Whether to include quarantined items
            include_blobs: Whether to include full-source audit blobs

        Returns:
            List of matching MemoryItem objects ranked by relevance
        """
        self._ensure_initialized()

        # Build a safe FTS5 query from natural-language input.
        safe_query = self._escape_fts5_query(query)
        if not safe_query:
            return []

        conditions = ["mi.tier = ?"]
        params: list[Any] = [self.tier.value]

        if namespace is not None:
            conditions.append("mi.namespace = ?")
            params.append(namespace)

        if status is not None:
            conditions.append("mi.status = ?")
            params.append(status.value)
        elif not include_quarantined:
            # Exclude quarantined items by default
            conditions.append("mi.status != ?")
            params.append(MemoryStatus.quarantined.value)

        if not include_expired:
            conditions.append("(mi.expires_at IS NULL OR mi.expires_at > ?)")
            params.append(datetime.utcnow().isoformat())

        if not include_blobs:
            conditions.append("(mi.metadata IS NULL OR mi.metadata NOT LIKE ?)")
            params.append('%"blob_type": "full_source"%')

        conditions.append(
            "NOT EXISTS (SELECT 1 FROM memory_claims mc WHERE mc.item_id = mi.id AND mc.superseded_by IS NOT NULL)"
        )

        where_clause = " AND ".join(conditions)
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT mi.*, bm25(memory_items_fts) as score FROM memory_items mi
                JOIN memory_items_fts fts ON mi.id = fts.id
                WHERE fts.memory_items_fts MATCH ?
                AND {where_clause}
                ORDER BY score, mi.confidence DESC, mi.updated_at DESC
                LIMIT ?
                """,
                [safe_query, *params],
            )
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def search_by_entities(
        self,
        query: str,
        namespace: Optional[str] = None,
        status: Optional[MemoryStatus] = None,
        limit: int = 20,
        include_expired: bool = False,
        include_quarantined: bool = False,
        include_blobs: bool = False,
    ) -> list[MemoryItem]:
        """Search items by normalized sidecar entities extracted from a query."""
        self._ensure_initialized()
        entities = extract_entities(query)
        normalized = list(dict.fromkeys(entity.entity_normalized for entity in entities))
        if not normalized:
            return []

        conditions = ["mi.tier = ?", f"me.entity_normalized IN ({','.join('?' * len(normalized))})"]
        params: list[Any] = [self.tier.value, *normalized]

        if namespace is not None:
            conditions.append("mi.namespace = ?")
            params.append(namespace)

        if status is not None:
            conditions.append("mi.status = ?")
            params.append(status.value)
        elif not include_quarantined:
            conditions.append("mi.status != ?")
            params.append(MemoryStatus.quarantined.value)

        if not include_expired:
            conditions.append("(mi.expires_at IS NULL OR mi.expires_at > ?)")
            params.append(datetime.utcnow().isoformat())

        if not include_blobs:
            conditions.append("(mi.metadata IS NULL OR mi.metadata NOT LIKE ?)")
            params.append('%"blob_type": "full_source"%')

        conditions.append(
            "NOT EXISTS (SELECT 1 FROM memory_claims mc WHERE mc.item_id = mi.id AND mc.superseded_by IS NOT NULL)"
        )

        where_clause = " AND ".join(conditions)
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT mi.*, COUNT(me.entity_normalized) AS entity_matches,
                       MAX(me.confidence) AS entity_confidence
                FROM memory_entities me
                JOIN memory_items mi ON mi.id = me.item_id
                WHERE {where_clause}
                GROUP BY mi.id
                ORDER BY entity_matches DESC, entity_confidence DESC,
                         mi.confidence DESC, mi.updated_at DESC
                LIMIT ?
                """,
                params,
            )
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def search_similar(
        self,
        query: str,
        limit: int = 10,
    ) -> list[tuple[MemoryItem, float]]:
        """
        Search with relevance scores using BM25.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of (MemoryItem, score) tuples
        """
        self._ensure_initialized()
        safe_query = self._escape_fts5_query(query)
        if not safe_query:
            return []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mi.*, bm25(memory_items_fts) as score
                FROM memory_items mi
                JOIN memory_items_fts fts ON mi.id = fts.id
                WHERE fts.memory_items_fts MATCH ?
                AND mi.status = 'active'
                ORDER BY score
                LIMIT ?
                """,
                [safe_query, limit],
            )
            results = []
            for row in cursor.fetchall():
                item = self._row_to_item(row)
                score = row["score"]
                results.append((item, score))
            return results
