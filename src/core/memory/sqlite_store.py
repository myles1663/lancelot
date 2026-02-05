"""
Memory vNext SQLite Store — Tiered memory persistence with FTS5 search.

This module provides SQLite-backed storage for:
- Working Memory (short-lived, task-scoped)
- Episodic Memory (conversation timeline, summaries)
- Archival Memory (long-term knowledge base)

Features:
- Full-text search via FTS5
- Thread-safe connection handling
- Automatic schema migration
- TTL/expiration management
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import (
    MEMORY_DIR,
    WORKING_MEMORY_DB,
    EPISODIC_DB,
    ARCHIVAL_DB,
    DEFAULT_WORKING_MEMORY_TTL_HOURS,
)
from .schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
    Provenance,
)

logger = logging.getLogger(__name__)


class MemoryItemStore:
    """
    SQLite-backed store for tiered memory items with FTS5 full-text search.

    Provides thread-safe CRUD operations with automatic expiration handling.
    Each tier (working, episodic, archival) uses a separate database file.
    """

    SCHEMA_VERSION = 1

    # Schema for memory items table
    CREATE_ITEMS_TABLE = """
    CREATE TABLE IF NOT EXISTS memory_items (
        id TEXT PRIMARY KEY,
        tier TEXT NOT NULL,
        namespace TEXT NOT NULL DEFAULT 'global',
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL DEFAULT 0.5,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        decay_half_life_days INTEGER,
        provenance TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active',
        token_count INTEGER NOT NULL DEFAULT 0,
        metadata TEXT NOT NULL DEFAULT '{}'
    );
    """

    # FTS5 virtual table for full-text search
    CREATE_FTS_TABLE = """
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
        id,
        title,
        content,
        tags,
        namespace,
        content='memory_items',
        content_rowid='rowid'
    );
    """

    # Triggers to keep FTS index in sync
    CREATE_FTS_TRIGGERS = """
    CREATE TRIGGER IF NOT EXISTS memory_items_ai AFTER INSERT ON memory_items BEGIN
        INSERT INTO memory_items_fts(rowid, id, title, content, tags, namespace)
        VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags, NEW.namespace);
    END;

    CREATE TRIGGER IF NOT EXISTS memory_items_ad AFTER DELETE ON memory_items BEGIN
        INSERT INTO memory_items_fts(memory_items_fts, rowid, id, title, content, tags, namespace)
        VALUES('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags, OLD.namespace);
    END;

    CREATE TRIGGER IF NOT EXISTS memory_items_au AFTER UPDATE ON memory_items BEGIN
        INSERT INTO memory_items_fts(memory_items_fts, rowid, id, title, content, tags, namespace)
        VALUES('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags, OLD.namespace);
        INSERT INTO memory_items_fts(rowid, id, title, content, tags, namespace)
        VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags, NEW.namespace);
    END;
    """

    # Indexes for common queries
    CREATE_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_items_tier ON memory_items(tier);
    CREATE INDEX IF NOT EXISTS idx_items_namespace ON memory_items(namespace);
    CREATE INDEX IF NOT EXISTS idx_items_status ON memory_items(status);
    CREATE INDEX IF NOT EXISTS idx_items_expires ON memory_items(expires_at);
    CREATE INDEX IF NOT EXISTS idx_items_created ON memory_items(created_at);
    """

    # Schema version tracking
    CREATE_META_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(
        self,
        data_dir: str | Path,
        tier: MemoryTier,
    ):
        """
        Initialize the memory item store for a specific tier.

        Args:
            data_dir: Base directory for lancelot_data
            tier: Memory tier (working, episodic, or archival)
        """
        self.data_dir = Path(data_dir)
        self.tier = tier
        self.memory_dir = self.data_dir / MEMORY_DIR

        # Select database file based on tier
        db_files = {
            MemoryTier.working: WORKING_MEMORY_DB,
            MemoryTier.episodic: EPISODIC_DB,
            MemoryTier.archival: ARCHIVAL_DB,
        }
        self.db_file = self.memory_dir / db_files.get(tier, "memory.sqlite")

        # Thread-local connections
        self._local = threading.local()
        self._initialized = False
        self._init_lock = threading.Lock()

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Get a thread-local database connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                str(self.db_file),
                timeout=30.0,
                check_same_thread=False,
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")

        try:
            yield self._local.connection
        except Exception:
            self._local.connection.rollback()
            raise

    def initialize(self) -> None:
        """Initialize the database schema."""
        with self._init_lock:
            if self._initialized:
                return

            # Create directory
            self.memory_dir.mkdir(parents=True, exist_ok=True)

            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Create tables
                cursor.executescript(self.CREATE_META_TABLE)
                cursor.executescript(self.CREATE_ITEMS_TABLE)
                cursor.executescript(self.CREATE_FTS_TABLE)
                cursor.executescript(self.CREATE_FTS_TRIGGERS)
                cursor.executescript(self.CREATE_INDEXES)

                # Set schema version
                cursor.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                    ("schema_version", str(self.SCHEMA_VERSION)),
                )

                conn.commit()

            self._initialized = True
            logger.info(
                "MemoryItemStore initialized for tier=%s at %s",
                self.tier.value, self.db_file
            )

    def _ensure_initialized(self) -> None:
        """Ensure the store is initialized."""
        if not self._initialized:
            self.initialize()

    def _escape_fts5_query(self, query: str) -> str:
        """
        Escape a query string for safe use with FTS5 MATCH.

        FTS5 has special syntax characters that need handling.
        This escapes the query by:
        1. Replacing double quotes (FTS5 phrase delimiter)
        2. Removing other problematic characters
        3. Wrapping in double quotes for literal matching

        Args:
            query: Raw search query

        Returns:
            Escaped query safe for FTS5 MATCH
        """
        # Remove or escape FTS5 special characters
        # FTS5 operators: AND, OR, NOT, NEAR, *, ^, :, -, +
        # Also need to handle quotes and parentheses
        escaped = query.replace('"', '""')

        # Remove other FTS5 special characters that could cause syntax errors
        for char in ["'", "(", ")", "{", "}", "[", "]", "^", "*", ":", "-", "+"]:
            escaped = escaped.replace(char, " ")

        # Collapse multiple spaces
        escaped = " ".join(escaped.split())

        # Return empty query protection
        if not escaped.strip():
            return '""'

        return f'"{escaped}"'

    def _item_to_row(self, item: MemoryItem) -> dict[str, Any]:
        """Convert a MemoryItem to a database row."""
        return {
            "id": item.id,
            "tier": item.tier.value,
            "namespace": item.namespace,
            "title": item.title,
            "content": item.content,
            "tags": json.dumps(item.tags),
            "confidence": item.confidence,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            "decay_half_life_days": item.decay_half_life_days,
            "provenance": json.dumps([p.model_dump(mode="json") for p in item.provenance]),
            "status": item.status.value,
            "token_count": item.token_count,
            "metadata": json.dumps(item.metadata),
        }

    def _row_to_item(self, row: sqlite3.Row) -> MemoryItem:
        """Convert a database row to a MemoryItem."""
        return MemoryItem(
            id=row["id"],
            tier=MemoryTier(row["tier"]),
            namespace=row["namespace"],
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            confidence=row["confidence"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            decay_half_life_days=row["decay_half_life_days"],
            provenance=[Provenance.model_validate(p) for p in json.loads(row["provenance"])],
            status=MemoryStatus(row["status"]),
            token_count=row["token_count"],
            metadata=json.loads(row["metadata"]),
        )

    def insert(self, item: MemoryItem) -> str:
        """
        Insert a new memory item.

        Args:
            item: The MemoryItem to insert

        Returns:
            The item ID

        Raises:
            ValueError: If an item with this ID already exists
        """
        self._ensure_initialized()
        row = self._item_to_row(item)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO memory_items (
                        id, tier, namespace, title, content, tags, confidence,
                        created_at, updated_at, expires_at, decay_half_life_days,
                        provenance, status, token_count, metadata
                    ) VALUES (
                        :id, :tier, :namespace, :title, :content, :tags, :confidence,
                        :created_at, :updated_at, :expires_at, :decay_half_life_days,
                        :provenance, :status, :token_count, :metadata
                    )
                    """,
                    row,
                )
                conn.commit()
                logger.debug("Inserted memory item %s", item.id)
                return item.id
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Item with ID {item.id} already exists") from e

    def get(self, item_id: str) -> Optional[MemoryItem]:
        """
        Get a memory item by ID.

        Args:
            item_id: The item ID

        Returns:
            The MemoryItem or None if not found
        """
        self._ensure_initialized()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM memory_items WHERE id = ?",
                (item_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_item(row)
            return None

    def update(self, item: MemoryItem) -> bool:
        """
        Update an existing memory item.

        Args:
            item: The MemoryItem with updated fields

        Returns:
            True if updated, False if not found
        """
        self._ensure_initialized()
        row = self._item_to_row(item)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE memory_items SET
                    tier = :tier, namespace = :namespace, title = :title,
                    content = :content, tags = :tags, confidence = :confidence,
                    updated_at = :updated_at, expires_at = :expires_at,
                    decay_half_life_days = :decay_half_life_days,
                    provenance = :provenance, status = :status,
                    token_count = :token_count, metadata = :metadata
                WHERE id = :id
                """,
                row,
            )
            conn.commit()
            updated = cursor.rowcount > 0
            if updated:
                logger.debug("Updated memory item %s", item.id)
            return updated

    def delete(self, item_id: str) -> bool:
        """
        Delete a memory item.

        Args:
            item_id: The item ID to delete

        Returns:
            True if deleted, False if not found
        """
        self._ensure_initialized()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM memory_items WHERE id = ?",
                (item_id,),
            )
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.debug("Deleted memory item %s", item_id)
            return deleted

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

        Returns:
            List of matching MemoryItem objects ranked by relevance
        """
        self._ensure_initialized()

        # Build the search query
        # FTS5 requires special handling for the query
        # Escape special characters and wrap in quotes for phrase matching
        safe_query = self._escape_fts5_query(query)

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

        where_clause = " AND ".join(conditions)
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT mi.* FROM memory_items mi
                JOIN memory_items_fts fts ON mi.id = fts.id
                WHERE fts.memory_items_fts MATCH ?
                AND {where_clause}
                ORDER BY rank
                LIMIT ?
                """,
                [safe_query, *params],
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

    def delete_expired(self) -> int:
        """
        Delete all expired items.

        Returns:
            Number of items deleted
        """
        self._ensure_initialized()
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM memory_items
                WHERE tier = ? AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                [self.tier.value, now],
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info("Deleted %d expired items from %s", count, self.tier.value)
            return count

    def update_status(
        self,
        item_id: str,
        status: MemoryStatus,
    ) -> bool:
        """
        Update just the status of an item.

        Args:
            item_id: The item ID
            status: New status

        Returns:
            True if updated, False if not found
        """
        self._ensure_initialized()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE memory_items
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                [status.value, datetime.utcnow().isoformat(), item_id],
            )
            conn.commit()
            return cursor.rowcount > 0

    def count(
        self,
        namespace: Optional[str] = None,
        status: Optional[MemoryStatus] = None,
    ) -> int:
        """
        Count items matching filters.

        Args:
            namespace: Filter by namespace
            status: Filter by status

        Returns:
            Count of matching items
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

        where_clause = " AND ".join(conditions)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) FROM memory_items WHERE {where_clause}",
                params,
            )
            return cursor.fetchone()[0]

    def apply_decay(self, days_elapsed: int = 1) -> int:
        """
        Apply confidence decay to items with decay_half_life_days set.

        Args:
            days_elapsed: Number of days to decay

        Returns:
            Number of items updated
        """
        self._ensure_initialized()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Calculate decay factor and update in one query
            cursor.execute(
                """
                UPDATE memory_items
                SET confidence = confidence * POWER(0.5, ? * 1.0 / decay_half_life_days),
                    updated_at = ?
                WHERE tier = ?
                AND decay_half_life_days IS NOT NULL
                AND decay_half_life_days > 0
                AND status = 'active'
                """,
                [days_elapsed, datetime.utcnow().isoformat(), self.tier.value],
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info(
                    "Applied %d-day decay to %d items in %s",
                    days_elapsed, count, self.tier.value
                )
            return count

    def get_items_by_ids(self, item_ids: list[str]) -> list[MemoryItem]:
        """
        Get multiple items by their IDs.

        Args:
            item_ids: List of item IDs

        Returns:
            List of MemoryItem objects (in no particular order)
        """
        self._ensure_initialized()
        if not item_ids:
            return []

        placeholders = ",".join("?" * len(item_ids))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM memory_items WHERE id IN ({placeholders})",
                item_ids,
            )
            return [self._row_to_item(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection for this thread."""
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None


class MemoryStoreManager:
    """
    Manager for all memory tier stores.

    Provides a unified interface for accessing working, episodic, and archival stores.
    """

    def __init__(self, data_dir: str | Path):
        """
        Initialize the memory store manager.

        Args:
            data_dir: Base directory for lancelot_data
        """
        self.data_dir = Path(data_dir)
        self._stores: dict[MemoryTier, MemoryItemStore] = {}
        self._lock = threading.Lock()

    def get_store(self, tier: MemoryTier) -> MemoryItemStore:
        """
        Get or create a store for a specific tier.

        Args:
            tier: The memory tier

        Returns:
            The MemoryItemStore for that tier
        """
        if tier == MemoryTier.core:
            raise ValueError("Core tier uses CoreBlockStore, not MemoryItemStore")

        with self._lock:
            if tier not in self._stores:
                store = MemoryItemStore(self.data_dir, tier)
                store.initialize()
                self._stores[tier] = store

            return self._stores[tier]

    @property
    def working(self) -> MemoryItemStore:
        """Get the working memory store."""
        return self.get_store(MemoryTier.working)

    @property
    def episodic(self) -> MemoryItemStore:
        """Get the episodic memory store."""
        return self.get_store(MemoryTier.episodic)

    @property
    def archival(self) -> MemoryItemStore:
        """Get the archival memory store."""
        return self.get_store(MemoryTier.archival)

    def search_all(
        self,
        query: str,
        tiers: Optional[list[MemoryTier]] = None,
        namespace: Optional[str] = None,
        limit: int = 20,
    ) -> list[MemoryItem]:
        """
        Search across multiple tiers.

        Args:
            query: Search query
            tiers: Tiers to search (default: all non-core)
            namespace: Filter by namespace
            limit: Maximum results per tier

        Returns:
            Combined list of matching items
        """
        if tiers is None:
            tiers = [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]

        results = []
        for tier in tiers:
            if tier == MemoryTier.core:
                continue
            store = self.get_store(tier)
            results.extend(store.search(query, namespace=namespace, limit=limit))

        return results

    def close_all(self) -> None:
        """Close all store connections."""
        for store in self._stores.values():
            store.close()
        self._stores.clear()
