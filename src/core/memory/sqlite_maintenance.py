"""SQLite memory maintenance and journal helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import json
import logging

from .entities import normalize_entity
from .schemas import MemoryItem, MemoryStatus

logger = logging.getLogger(__name__)


class MemoryMaintenanceMixin:
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
