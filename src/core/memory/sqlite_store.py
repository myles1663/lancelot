"""
Structured Memory SQLite Store — Tiered memory persistence with FTS5 search.

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

import atexit
import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import (
    MEMORY_DIR,
    WORKING_MEMORY_DB,
    EPISODIC_DB,
    ARCHIVAL_DB,
)
from .schemas import (
    MemoryItem,
    MemoryStatus,
    MemoryTier,
)
from .ethics import MemoryEthicsEvaluator
from .receipt_events import MemoryReceiptEmitter
from .sqlite_codec import item_to_row, row_to_item
from .sqlite_schema import (
    CREATE_CLAIMS_INDEXES,
    CREATE_CLAIMS_TABLE,
    CREATE_COMMIT_UNDO_LOG_INDEXES,
    CREATE_COMMIT_UNDO_LOG_TABLE,
    CREATE_COMPACTION_JOURNAL_INDEXES,
    CREATE_COMPACTION_JOURNAL_TABLE,
    CREATE_ENTITIES_INDEXES,
    CREATE_ENTITIES_TABLE,
    CREATE_FTS_TABLE,
    CREATE_FTS_TRIGGERS,
    CREATE_INDEXES,
    CREATE_ITEMS_TABLE,
    CREATE_META_TABLE,
    SCHEMA_VERSION,
    apply_base_schema,
    record_schema_version,
)
from .sqlite_sidecars import MemorySidecarMixin
from .sqlite_search import MemorySearchMixin
from .sqlite_maintenance import MemoryMaintenanceMixin
from .sqlite_manager import MemoryStoreManagerSearchMixin

logger = logging.getLogger(__name__)


class MemoryItemStore(MemorySidecarMixin, MemorySearchMixin, MemoryMaintenanceMixin):
    """
    SQLite-backed store for tiered memory items with FTS5 full-text search.

    Provides thread-safe CRUD operations with automatic expiration handling.
    Each tier (working, episodic, archival) uses a separate database file.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    CREATE_ITEMS_TABLE = CREATE_ITEMS_TABLE
    CREATE_FTS_TABLE = CREATE_FTS_TABLE
    CREATE_FTS_TRIGGERS = CREATE_FTS_TRIGGERS
    CREATE_INDEXES = CREATE_INDEXES
    CREATE_META_TABLE = CREATE_META_TABLE
    CREATE_COMPACTION_JOURNAL_TABLE = CREATE_COMPACTION_JOURNAL_TABLE
    CREATE_COMPACTION_JOURNAL_INDEXES = CREATE_COMPACTION_JOURNAL_INDEXES
    CREATE_COMMIT_UNDO_LOG_TABLE = CREATE_COMMIT_UNDO_LOG_TABLE
    CREATE_COMMIT_UNDO_LOG_INDEXES = CREATE_COMMIT_UNDO_LOG_INDEXES
    CREATE_ENTITIES_TABLE = CREATE_ENTITIES_TABLE
    CREATE_ENTITIES_INDEXES = CREATE_ENTITIES_INDEXES
    CREATE_CLAIMS_TABLE = CREATE_CLAIMS_TABLE
    CREATE_CLAIMS_INDEXES = CREATE_CLAIMS_INDEXES

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
        self._ethics = MemoryEthicsEvaluator()
        self._receipt_emitter = MemoryReceiptEmitter(self.data_dir)

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

                apply_base_schema(cursor)
                self._migrate_schema(cursor)
                self._backfill_missing_entities(cursor)
                self._backfill_missing_claims(cursor)
                record_schema_version(cursor, self.SCHEMA_VERSION)

                conn.commit()

            self._initialized = True
            logger.info(
                "MemoryItemStore initialized for tier=%s at %s",
                self.tier.value, self.db_file
            )

    def _migrate_schema(self, cursor: sqlite3.Cursor) -> None:
        """Apply additive schema migrations for existing memory databases."""
        cursor.execute("PRAGMA table_info(memory_items)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "last_retrieved_at" not in columns:
            cursor.execute("ALTER TABLE memory_items ADD COLUMN last_retrieved_at TEXT")

    def _backfill_missing_entities(self, cursor: sqlite3.Cursor) -> None:
        """Populate entity sidecar rows for items created before the entity index."""
        cursor.execute(
            """
            SELECT mi.* FROM memory_items mi
            WHERE NOT EXISTS (
                SELECT 1 FROM memory_entities me WHERE me.item_id = mi.id
            )
            """
        )
        for row in cursor.fetchall():
            item = self._row_to_item(row)
            self._replace_entities(cursor, item)

    def _backfill_missing_claims(self, cursor: sqlite3.Cursor) -> None:
        """Populate claim rows for items created before the claim index."""
        cursor.execute(
            """
            SELECT mi.* FROM memory_items mi
            WHERE NOT EXISTS (
                SELECT 1 FROM memory_claims mc WHERE mc.item_id = mi.id
            )
            """
        )
        for row in cursor.fetchall():
            item = self._row_to_item(row)
            self._replace_claims(cursor, item)
            cursor.execute(
                "UPDATE memory_items SET status = ?, metadata = ? WHERE id = ?",
                (item.status.value, json.dumps(item.metadata), item.id),
            )

    def _ensure_initialized(self) -> None:
        """Ensure the store is initialized."""
        if not self._initialized:
            self.initialize()

    def _escape_fts5_query(self, query: str) -> str:
        """
        Escape a query string for safe use with FTS5 MATCH.

        Search favors recall for long-running work: split natural language into
        safe tokens and search them as prefix terms instead of requiring one
        exact phrase match.

        Args:
            query: Raw search query

        Returns:
            Escaped query safe for FTS5 MATCH
        """
        tokens = re.findall(r"[A-Za-z0-9_]{2,}", str(query or "").lower())
        deduped = list(dict.fromkeys(tokens))[:12]
        if not deduped:
            return ""
        return " OR ".join(f"{token}*" for token in deduped)

    def _item_to_row(self, item: MemoryItem) -> dict[str, Any]:
        return item_to_row(item)

    def _row_to_item(self, row: sqlite3.Row) -> MemoryItem:
        return row_to_item(row)

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
        decision = self._ethics.evaluate_write(item)
        self._emit_ethics_receipt(item, decision, operation="insert")
        item = self._ethics.apply_write_decision(item, decision)
        row = self._item_to_row(item)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO memory_items (
                        id, tier, namespace, title, content, tags, confidence,
                        created_at, updated_at, last_retrieved_at, expires_at, decay_half_life_days,
                        provenance, status, token_count, metadata
                    ) VALUES (
                        :id, :tier, :namespace, :title, :content, :tags, :confidence,
                        :created_at, :updated_at, :last_retrieved_at, :expires_at, :decay_half_life_days,
                        :provenance, :status, :token_count, :metadata
                    )
                    """,
                    row,
                )
                self._replace_entities(cursor, item)
                self._replace_claims(cursor, item)
                cursor.execute(
                    "UPDATE memory_items SET status = ?, metadata = ? WHERE id = ?",
                    (item.status.value, json.dumps(item.metadata), item.id),
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
        decision = self._ethics.evaluate_write(item)
        self._emit_ethics_receipt(item, decision, operation="update")
        item = self._ethics.apply_write_decision(item, decision)
        row = self._item_to_row(item)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE memory_items SET
                    tier = :tier, namespace = :namespace, title = :title,
                    content = :content, tags = :tags, confidence = :confidence,
                    updated_at = :updated_at, last_retrieved_at = :last_retrieved_at,
                    expires_at = :expires_at,
                    decay_half_life_days = :decay_half_life_days,
                    provenance = :provenance, status = :status,
                    token_count = :token_count, metadata = :metadata
                WHERE id = :id
                """,
                row,
            )
            updated = cursor.rowcount > 0
            if updated:
                self._replace_entities(cursor, item)
                self._replace_claims(cursor, item)
                cursor.execute(
                    "UPDATE memory_items SET status = ?, metadata = ? WHERE id = ?",
                    (item.status.value, json.dumps(item.metadata), item.id),
                )
            conn.commit()
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
            cursor.execute("DELETE FROM memory_entities WHERE item_id = ?", (item_id,))
            cursor.execute("DELETE FROM memory_claims WHERE item_id = ?", (item_id,))
            cursor.execute(
                "DELETE FROM memory_items WHERE id = ?",
                (item_id,),
            )
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.debug("Deleted memory item %s", item_id)
            return deleted

    def close(self) -> None:
        """Close the database connection for this thread."""
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None


class MemoryStoreManager(MemoryStoreManagerSearchMixin):
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
        atexit.register(self.close_all)

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

    def close_all(self) -> None:
        """Close all store connections."""
        for store in self._stores.values():
            store.close()
        self._stores.clear()
