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
from datetime import datetime
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
    Provenance,
)
from .ethics import MemoryEthicsEvaluator
from .claims import extract_claims
from .entities import extract_entities, extract_entities_from_item_fields, normalize_entity
from .receipt_events import MemoryReceiptEmitter

logger = logging.getLogger(__name__)


class MemoryItemStore:
    """
    SQLite-backed store for tiered memory items with FTS5 full-text search.

    Provides thread-safe CRUD operations with automatic expiration handling.
    Each tier (working, episodic, archival) uses a separate database file.
    """

    SCHEMA_VERSION = 5

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
        last_retrieved_at TEXT,
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

    CREATE_COMPACTION_JOURNAL_TABLE = """
    CREATE TABLE IF NOT EXISTS compaction_journal (
        compaction_id TEXT PRIMARY KEY,
        namespace TEXT NOT NULL,
        source_item_ids TEXT NOT NULL DEFAULT '[]',
        archival_item_id TEXT,
        status TEXT NOT NULL,
        error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );
    """

    CREATE_COMPACTION_JOURNAL_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_compaction_journal_status ON compaction_journal(status);
    CREATE INDEX IF NOT EXISTS idx_compaction_journal_namespace ON compaction_journal(namespace);
    """

    CREATE_COMMIT_UNDO_LOG_TABLE = """
    CREATE TABLE IF NOT EXISTS commit_undo_log (
        commit_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        tier TEXT NOT NULL,
        pre_state TEXT NOT NULL,
        operation TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (commit_id, item_id)
    );
    """

    CREATE_COMMIT_UNDO_LOG_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_undo_commit ON commit_undo_log(commit_id);
    """

    CREATE_ENTITIES_TABLE = """
    CREATE TABLE IF NOT EXISTS memory_entities (
        item_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_value TEXT NOT NULL,
        entity_normalized TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.8,
        PRIMARY KEY (item_id, entity_type, entity_normalized)
    );
    """

    CREATE_ENTITIES_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_entities_normalized ON memory_entities(entity_normalized);
    CREATE INDEX IF NOT EXISTS idx_entities_type ON memory_entities(entity_type);
    CREATE INDEX IF NOT EXISTS idx_entities_item ON memory_entities(item_id);
    """

    CREATE_CLAIMS_TABLE = """
    CREATE TABLE IF NOT EXISTS memory_claims (
        item_id TEXT NOT NULL,
        entity_normalized TEXT NOT NULL,
        attribute TEXT NOT NULL,
        value TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        valid_until TEXT,
        superseded_by TEXT,
        PRIMARY KEY (item_id, entity_normalized, attribute)
    );
    """

    CREATE_CLAIMS_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_claims_entity_attr ON memory_claims(entity_normalized, attribute);
    CREATE INDEX IF NOT EXISTS idx_claims_valid ON memory_claims(valid_until);
    CREATE INDEX IF NOT EXISTS idx_claims_item ON memory_claims(item_id);
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

                # Create tables
                cursor.executescript(self.CREATE_META_TABLE)
                cursor.executescript(self.CREATE_ITEMS_TABLE)
                cursor.executescript(self.CREATE_COMPACTION_JOURNAL_TABLE)
                cursor.executescript(self.CREATE_COMMIT_UNDO_LOG_TABLE)
                cursor.executescript(self.CREATE_ENTITIES_TABLE)
                cursor.executescript(self.CREATE_CLAIMS_TABLE)
                cursor.executescript(self.CREATE_FTS_TABLE)
                cursor.executescript(self.CREATE_FTS_TRIGGERS)
                cursor.executescript(self.CREATE_INDEXES)
                cursor.executescript(self.CREATE_COMPACTION_JOURNAL_INDEXES)
                cursor.executescript(self.CREATE_COMMIT_UNDO_LOG_INDEXES)
                cursor.executescript(self.CREATE_ENTITIES_INDEXES)
                cursor.executescript(self.CREATE_CLAIMS_INDEXES)
                self._migrate_schema(cursor)
                self._backfill_missing_entities(cursor)
                self._backfill_missing_claims(cursor)

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
            "last_retrieved_at": item.last_retrieved_at.isoformat() if item.last_retrieved_at else None,
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
            last_retrieved_at=(
                datetime.fromisoformat(row["last_retrieved_at"])
                if "last_retrieved_at" in row.keys() and row["last_retrieved_at"]
                else None
            ),
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

    def _replace_entities(self, cursor: sqlite3.Cursor, item: MemoryItem) -> None:
        """Rebuild sidecar entity rows for an item."""
        cursor.execute("DELETE FROM memory_entities WHERE item_id = ?", (item.id,))
        entities = extract_entities_from_item_fields(
            title=item.title,
            content=item.content,
            namespace=item.namespace,
            tags=item.tags,
            metadata=item.metadata,
        )
        cursor.executemany(
            """
            INSERT OR REPLACE INTO memory_entities (
                item_id, entity_type, entity_value, entity_normalized, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item.id,
                    entity.entity_type,
                    entity.entity_value,
                    entity.entity_normalized,
                    entity.confidence,
                )
                for entity in entities
            ],
        )

    def _replace_claims(self, cursor: sqlite3.Cursor, item: MemoryItem) -> None:
        """Rebuild structured factual claims, quarantining contradictions until approval."""
        now = datetime.utcnow().isoformat()
        cursor.execute("DELETE FROM memory_claims WHERE item_id = ?", (item.id,))
        claims = extract_claims(item)
        pending_supersessions: list[dict[str, Any]] = []
        for claim in claims:
            cursor.execute(
                """
                SELECT item_id, value FROM memory_claims
                WHERE entity_normalized = ?
                  AND attribute = ?
                  AND item_id != ?
                  AND superseded_by IS NULL
                  AND value != ?
                """,
                [claim.entity_normalized, claim.attribute, item.id, claim.value],
            )
            superseded_claims = [
                {"item_id": row["item_id"], "value": row["value"]}
                for row in cursor.fetchall()
            ]
            if superseded_claims:
                pending_supersessions.append({
                    "entity_normalized": claim.entity_normalized,
                    "attribute": claim.attribute,
                    "new_value": claim.value,
                    "superseded_count": len(superseded_claims),
                    "superseded_item_ids": [entry["item_id"] for entry in superseded_claims],
                    "superseded_claims": superseded_claims,
                })

        metadata = dict(item.metadata or {})
        if pending_supersessions and not metadata.get("claim_supersession_approved"):
            metadata["claim_supersession_pending"] = True
            metadata["flagged_reason"] = "claim_supersession"
            metadata["superseded_claims"] = pending_supersessions
            item.metadata = metadata
            item.status = MemoryStatus.quarantined
            return

        metadata.pop("claim_supersession_pending", None)
        if metadata.get("flagged_reason") == "claim_supersession":
            metadata.pop("flagged_reason", None)
        item.metadata = metadata

        for claim in claims:
            cursor.execute(
                """
                SELECT item_id, value FROM memory_claims
                WHERE entity_normalized = ?
                  AND attribute = ?
                  AND item_id != ?
                  AND superseded_by IS NULL
                  AND value != ?
                """,
                [claim.entity_normalized, claim.attribute, item.id, claim.value],
            )
            superseded_claims = [
                {"item_id": row["item_id"], "value": row["value"]}
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                UPDATE memory_claims
                SET valid_until = ?, superseded_by = ?
                WHERE entity_normalized = ?
                  AND attribute = ?
                  AND item_id != ?
                  AND superseded_by IS NULL
                  AND value != ?
                """,
                [
                    now,
                    item.id,
                    claim.entity_normalized,
                    claim.attribute,
                    item.id,
                    claim.value,
                ],
            )
            cursor.execute(
                """
                INSERT OR REPLACE INTO memory_claims (
                    item_id, entity_normalized, attribute, value,
                    valid_from, valid_until, superseded_by
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL)
                """,
                [
                    item.id,
                    claim.entity_normalized,
                    claim.attribute,
                    claim.value,
                    claim.valid_from.isoformat(),
                ],
            )
            if superseded_claims:
                metadata = dict(item.metadata or {})
                metadata.setdefault("superseded_claims", [])
                metadata["superseded_claims"].append({
                    "entity_normalized": claim.entity_normalized,
                    "attribute": claim.attribute,
                    "new_value": claim.value,
                    "superseded_count": len(superseded_claims),
                    "superseded_item_ids": [entry["item_id"] for entry in superseded_claims],
                })
                item.metadata = metadata
                self._receipt_emitter.emit(
                    action_type="memory_claim_supersede",
                    action_name="memory_claim_contradiction",
                    inputs={
                        "item_id": item.id,
                        "tier": item.tier.value,
                        "entity_normalized": claim.entity_normalized,
                        "attribute": claim.attribute,
                        "value": claim.value,
                    },
                    outputs={
                        "superseded_claims": superseded_claims,
                        "superseded_by": item.id,
                    },
                )

    def _emit_ethics_receipt(self, item: MemoryItem, decision: Any, *, operation: str) -> None:
        """Emit a receipt for ethics decisions that changed memory behavior."""
        if not getattr(decision, "rule_name", ""):
            return
        self._receipt_emitter.emit(
            action_type="memory_ethics_evaluation",
            action_name=f"memory_{operation}_ethics",
            inputs={
                "item_id": item.id,
                "tier": item.tier.value,
                "operation": operation,
            },
            outputs={
                "item_id": item.id,
                "tier": item.tier.value,
                "rule_name": decision.rule_name,
                "action": decision.action.value,
                "reason": decision.reason,
            },
        )

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
                SELECT id FROM memory_items
                WHERE tier = ? AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                [self.tier.value, now],
            )
            expired_ids = [row["id"] for row in cursor.fetchall()]
            if not expired_ids:
                return 0

            placeholders = ",".join("?" * len(expired_ids))
            cursor.execute(
                f"DELETE FROM memory_entities WHERE item_id IN ({placeholders})",
                expired_ids,
            )
            cursor.execute(
                f"DELETE FROM memory_claims WHERE item_id IN ({placeholders})",
                expired_ids,
            )
            cursor.execute(
                f"DELETE FROM memory_items WHERE id IN ({placeholders})",
                expired_ids,
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
            item: MemoryItem | None = None
            if status == MemoryStatus.active:
                cursor.execute("SELECT * FROM memory_items WHERE id = ?", [item_id])
                row = cursor.fetchone()
                if row:
                    item = self._row_to_item(row)
                    if (item.metadata or {}).get("claim_supersession_pending"):
                        metadata = dict(item.metadata or {})
                        metadata["claim_supersession_approved"] = True
                        item.metadata = metadata
                        item.status = status
                        self._replace_claims(cursor, item)
            cursor.execute(
                """
                UPDATE memory_items
                SET status = ?, updated_at = ?, metadata = COALESCE(?, metadata)
                WHERE id = ?
                """,
                [
                    status.value,
                    datetime.utcnow().isoformat(),
                    json.dumps(item.metadata) if item is not None else None,
                    item_id,
                ],
            )
            conn.commit()
            return cursor.rowcount > 0

    def mark_retrieved(self, item_id: str, when: Optional[datetime] = None) -> bool:
        """Record that an item was included in compiled context."""
        self._ensure_initialized()
        retrieved_at = (when or datetime.utcnow()).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE memory_items
                SET last_retrieved_at = ?
                WHERE id = ?
                """,
                [retrieved_at, item_id],
            )
            conn.commit()
            return cursor.rowcount > 0

    def find_by_metadata(self, key: str, value: str, limit: int = 20) -> list[MemoryItem]:
        """Find items with an exact metadata key/value pair."""
        self._ensure_initialized()
        pattern = f'%"{key}": "{value}"%'
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM memory_items
                WHERE tier = ? AND metadata LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                [self.tier.value, pattern, limit],
            )
            matches = []
            for row in cursor.fetchall():
                item = self._row_to_item(row)
                if str((item.metadata or {}).get(key)) == value:
                    matches.append(item)
            return matches

    def get_claim_history(self, entity: str, attribute: str) -> list[dict[str, Any]]:
        """Return claim timeline entries for an entity/attribute pair."""
        self._ensure_initialized()
        entity_normalized = normalize_entity(entity)
        normalized_attribute = str(attribute or "").strip().lower().replace(" ", "_")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM memory_claims
                WHERE entity_normalized = ? AND attribute = ?
                ORDER BY valid_from ASC
                """,
                [entity_normalized, normalized_attribute],
            )
            return [
                {
                    "item_id": row["item_id"],
                    "entity_normalized": row["entity_normalized"],
                    "attribute": row["attribute"],
                    "value": row["value"],
                    "valid_from": row["valid_from"],
                    "valid_until": row["valid_until"],
                    "superseded_by": row["superseded_by"],
                }
                for row in cursor.fetchall()
            ]

    def create_compaction_journal(
        self,
        *,
        compaction_id: str,
        namespace: str,
        source_item_ids: list[str],
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create a journal entry for an in-flight memory compaction."""
        self._ensure_initialized()
        now = datetime.utcnow().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO compaction_journal (
                    compaction_id, namespace, source_item_ids, archival_item_id,
                    status, error, created_at, updated_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    compaction_id,
                    namespace,
                    json.dumps(source_item_ids),
                    None,
                    "planned",
                    "",
                    now,
                    now,
                    json.dumps(metadata or {}),
                ],
            )
            conn.commit()

    def update_compaction_journal(
        self,
        compaction_id: str,
        *,
        status: str,
        archival_item_id: Optional[str] = None,
        error: str = "",
    ) -> None:
        """Update an existing compaction journal entry."""
        self._ensure_initialized()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE compaction_journal
                SET status = ?,
                    archival_item_id = COALESCE(?, archival_item_id),
                    error = ?,
                    updated_at = ?
                WHERE compaction_id = ?
                """,
                [status, archival_item_id, error, datetime.utcnow().isoformat(), compaction_id],
            )
            conn.commit()

    def list_pending_compactions(self) -> list[dict[str, Any]]:
        """List compaction journal entries that need recovery."""
        self._ensure_initialized()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM compaction_journal
                WHERE status IN ('planned', 'archival_written')
                ORDER BY created_at ASC
                """
            )
            return [
                {
                    "compaction_id": row["compaction_id"],
                    "namespace": row["namespace"],
                    "source_item_ids": json.loads(row["source_item_ids"] or "[]"),
                    "archival_item_id": row["archival_item_id"],
                    "status": row["status"],
                    "error": row["error"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                }
                for row in cursor.fetchall()
            ]

    def record_undo_entry(
        self,
        *,
        commit_id: str,
        item_id: str,
        operation: str,
        pre_state: Optional[MemoryItem],
    ) -> None:
        """Persist the first pre-state for an item touched by a commit."""
        self._ensure_initialized()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO commit_undo_log (
                    commit_id, item_id, tier, pre_state, operation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    commit_id,
                    item_id,
                    self.tier.value,
                    pre_state.model_dump_json() if pre_state is not None else "",
                    operation,
                    datetime.utcnow().isoformat(),
                ],
            )
            conn.commit()

    def list_undo_entries(self, commit_id: str) -> list[dict[str, Any]]:
        """Return persisted undo entries for a commit in insertion order."""
        self._ensure_initialized()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM commit_undo_log
                WHERE commit_id = ?
                ORDER BY created_at DESC
                """,
                [commit_id],
            )
            return [
                {
                    "commit_id": row["commit_id"],
                    "item_id": row["item_id"],
                    "tier": row["tier"],
                    "operation": row["operation"],
                    "pre_state": MemoryItem.model_validate_json(row["pre_state"]) if row["pre_state"] else None,
                }
                for row in cursor.fetchall()
            ]

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

    def list_lru_eviction_candidates(self, *, max_items: int, batch_size: int = 100) -> list[MemoryItem]:
        """Return excess items in deprecated-first, least-recently-retrieved order."""
        self._ensure_initialized()
        if max_items < 0:
            raise ValueError("max_items must be non-negative")
        batch_size = max(1, int(batch_size))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM memory_items WHERE tier = ?", (self.tier.value,))
            total = int(cursor.fetchone()[0])
            excess = total - max_items
            if excess <= 0:
                return []

            limit = min(excess, batch_size)
            cursor.execute(
                """
                SELECT * FROM memory_items
                WHERE tier = ?
                ORDER BY
                    CASE WHEN status = 'deprecated' THEN 0 ELSE 1 END ASC,
                    COALESCE(last_retrieved_at, updated_at, created_at) ASC,
                    confidence ASC,
                    created_at ASC
                LIMIT ?
                """,
                (self.tier.value, limit),
            )
            candidates = [self._row_to_item(row) for row in cursor.fetchall()]
            return candidates

    def delete_items(self, item_ids: list[str]) -> int:
        """Delete a batch of items and associated sidecar rows."""
        self._ensure_initialized()
        if not item_ids:
            return 0
        placeholders = ",".join("?" * len(item_ids))
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"DELETE FROM memory_entities WHERE item_id IN ({placeholders})",
                item_ids,
            )
            cursor.execute(
                f"DELETE FROM memory_claims WHERE item_id IN ({placeholders})",
                item_ids,
            )
            cursor.execute(
                f"DELETE FROM memory_items WHERE id IN ({placeholders})",
                item_ids,
            )
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    def evict_lru(self, *, max_items: int, batch_size: int = 100) -> list[MemoryItem]:
        """Delete excess items in deprecated-first, least-recently-retrieved order."""
        candidates = self.list_lru_eviction_candidates(max_items=max_items, batch_size=batch_size)
        if candidates:
            self.delete_items([item.id for item in candidates])
        return candidates

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

    @staticmethod
    def _result_signature(item: MemoryItem) -> str:
        text = " ".join(f"{item.title} {item.content}".lower().split())
        return text[:600]

    @staticmethod
    def _query_tokens(query: str) -> set[str]:
        return set(re.findall(r"[A-Za-z0-9_]{2,}", str(query or "").lower()))

    @staticmethod
    def _metadata_values(metadata: dict[str, Any], *keys: str) -> set[str]:
        values: set[str] = set()
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, list):
                values.update(str(entry) for entry in value if entry not in (None, ""))
            elif value not in (None, ""):
                values.add(str(value))
        return values

    def _scope_boost(
        self,
        item: MemoryItem,
        *,
        current_quest_id: str = "",
        operator_id: str = "",
        workflow_id: str = "",
    ) -> float:
        metadata = item.metadata or {}
        score = 0.0
        if current_quest_id:
            quest_values = self._metadata_values(metadata, "quest_id", "quest_ids")
            if item.namespace == f"quest:{current_quest_id}" or current_quest_id in quest_values:
                score += 4.0
        if operator_id:
            operator_values = self._metadata_values(metadata, "operator_id", "operator_ids")
            if item.namespace == f"operator:{operator_id}" or operator_id in operator_values:
                score += 2.5
        if workflow_id:
            workflow_values = self._metadata_values(metadata, "workflow_id", "workflow", "template_id")
            if item.namespace == f"workflow:{workflow_id}" or workflow_id in workflow_values:
                score += 1.5
        if item.namespace == "global":
            score += 0.25
        return score

    @staticmethod
    def _evidence_boost(item: MemoryItem, query_tokens: set[str]) -> float:
        tags = {str(tag).lower() for tag in item.tags}
        score = min(len(tags & query_tokens), 5) * 0.15
        if tags & {"receipt", "receipts", "work_ledger", "session_brief", "task_experience"}:
            score += 0.5
        metadata = item.metadata or {}
        if metadata.get("receipt_ids") or metadata.get("last_receipt_id"):
            score += 0.35
        return score

    def search_all(
        self,
        query: str,
        tiers: Optional[list[MemoryTier]] = None,
        namespace: Optional[str] = None,
        limit: int = 20,
        total_limit: Optional[int] = None,
        current_quest_id: str = "",
        operator_id: str = "",
        workflow_id: str = "",
        include_blobs: bool = False,
    ) -> list[MemoryItem]:
        """
        Search across multiple tiers.

        Args:
            query: Search query
            tiers: Tiers to search (default: all non-core)
            namespace: Filter by namespace
            limit: Maximum results per tier
            total_limit: Optional maximum results after cross-tier dedupe/ranking
            include_blobs: Whether to include full-source audit blobs

        Returns:
            Combined list of matching items
        """
        if tiers is None:
            tiers = [MemoryTier.working, MemoryTier.episodic, MemoryTier.archival]

        tier_weight = {
            MemoryTier.working: 3.0,
            MemoryTier.episodic: 2.0,
            MemoryTier.archival: 1.0,
        }
        query_tokens = self._query_tokens(query)
        ranked: dict[str, tuple[float, MemoryItem]] = {}
        candidate_limit = max(int(limit), int(total_limit or 0), 10)
        for tier in tiers:
            if tier == MemoryTier.core:
                continue
            store = self.get_store(tier)
            candidates = [
                *store.search_by_entities(
                    query,
                    namespace=namespace,
                    limit=candidate_limit,
                    include_blobs=include_blobs,
                ),
                *store.search(
                    query,
                    namespace=namespace,
                    limit=candidate_limit,
                    include_blobs=include_blobs,
                ),
            ]
            for position, item in enumerate(candidates):
                signature = self._result_signature(item) or item.id
                score = (
                    tier_weight.get(item.tier, 0.0)
                    + item.confidence
                    + self._scope_boost(
                        item,
                        current_quest_id=current_quest_id,
                        operator_id=operator_id,
                        workflow_id=workflow_id,
                    )
                    + self._evidence_boost(item, query_tokens)
                    - (position * 0.01)
                )
                existing = ranked.get(signature)
                if existing is None or score > existing[0]:
                    ranked[signature] = (score, item)

        ordered = [
            item
            for _, item in sorted(
                ranked.values(),
                key=lambda pair: (
                    pair[0],
                    pair[1].updated_at,
                ),
                reverse=True,
            )
        ]
        max_results = total_limit if total_limit is not None else limit * max(1, len(tiers))
        return ordered[: max(1, int(max_results))]

    def close_all(self) -> None:
        """Close all store connections."""
        for store in self._stores.values():
            store.close()
        self._stores.clear()
