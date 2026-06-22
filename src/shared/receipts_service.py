"""Receipt service and factory helpers for canonical audit receipts."""

from __future__ import annotations

import base64
import logging
import os
import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import replace
from contextlib import contextmanager

try:
    from .receipt_queries import ReceiptQueryMixin
    from .receipts_action_types import ActionType
    from .receipts_models import (
        CognitionTier,
        ImmutableReceiptError,
        Receipt,
        ReceiptIntegrityKeyError,
        ReceiptStatus,
    )
    from .receipts_migrations import (
        SCHEMA_VERSION,
        ensure_receipt_schema,
    )
    from .receipts_integrity import (
        INTEGRITY_CHAIN_HEAD,
        INTEGRITY_KEY_FILENAME,
        INTEGRITY_KEY_VERSION,
        build_local_key_record,
        canonical_receipt_payload,
        compute_integrity_hash,
        compute_integrity_signature,
        create_integrity_key_file,
        derive_env_key_id,
        load_integrity_key_file,
    )
    from .receipts_store import open_receipt_connection, receipt_from_row
except ImportError:  # pragma: no cover - legacy top-level import path
    from receipt_queries import ReceiptQueryMixin
    from receipts_action_types import ActionType
    from receipts_models import (
        CognitionTier,
        ImmutableReceiptError,
        Receipt,
        ReceiptIntegrityKeyError,
        ReceiptStatus,
    )
    from receipts_migrations import (
        SCHEMA_VERSION,
        ensure_receipt_schema,
    )
    from receipts_integrity import (
        INTEGRITY_CHAIN_HEAD,
        INTEGRITY_KEY_FILENAME,
        INTEGRITY_KEY_VERSION,
        build_local_key_record,
        canonical_receipt_payload,
        compute_integrity_hash,
        compute_integrity_signature,
        create_integrity_key_file,
        derive_env_key_id,
        load_integrity_key_file,
    )
    from receipts_store import open_receipt_connection, receipt_from_row


logger = logging.getLogger(__name__)


class ReceiptService(ReceiptQueryMixin):
    """
    SQLite-backed immutable receipt storage service.

    Finalized receipts are append-only audit records.
    Pending receipts are kept in an internal staging table until they resolve
    to success or failure, at which point a finalized immutable receipt row is
    inserted into the durable receipt log.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    INTEGRITY_KEY_VERSION = INTEGRITY_KEY_VERSION
    INTEGRITY_KEY_FILENAME = INTEGRITY_KEY_FILENAME
    INTEGRITY_CHAIN_HEAD = INTEGRITY_CHAIN_HEAD

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        """
        Initialize the receipt service.

        Args:
            data_dir: Directory for storing receipts.db
        """
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "receipts.db")
        self._integrity_key_path = os.path.join(
            data_dir, self.INTEGRITY_KEY_FILENAME
        )
        self._local = threading.local()
        self._lock = threading.Lock()

        # Ensure data directory exists
        os.makedirs(data_dir, exist_ok=True)
        (
            self._integrity_secret,
            self._integrity_key_id,
        ) = self._load_or_create_integrity_key()

        # Initialize database schema
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = open_receipt_connection(self.db_path)
        return self._local.connection

    @contextmanager
    def _transaction(self):
        """Context manager for database transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_database(self):
        """Initialize database schema with migration support."""
        with self._transaction() as conn:
            ensure_receipt_schema(conn, logger)

    def _derive_env_key_id(self, integrity_secret: bytes) -> str:
        return derive_env_key_id(integrity_secret)

    def _build_local_key_record(self, integrity_secret: bytes) -> Dict[str, Any]:
        return build_local_key_record(integrity_secret, self.INTEGRITY_KEY_VERSION)

    def _load_integrity_key_file(self) -> Dict[str, str]:
        return load_integrity_key_file(
            self._integrity_key_path,
            self.INTEGRITY_KEY_VERSION,
        )

    def _create_integrity_key_file(self) -> Dict[str, str]:
        return create_integrity_key_file(
            self._integrity_key_path,
            self.INTEGRITY_KEY_VERSION,
            self._build_local_key_record,
            self._load_integrity_key_file,
        )

    def _load_or_create_integrity_key(self) -> tuple[bytes, str]:
        env_secret = os.environ.get("LANCELOT_RECEIPT_HMAC_KEY", "").strip()
        if env_secret:
            integrity_secret = env_secret.encode("utf-8")
            if len(integrity_secret) < 32:
                raise ReceiptIntegrityKeyError(
                    "LANCELOT_RECEIPT_HMAC_KEY must be at least 32 bytes long"
                )
            return integrity_secret, self._derive_env_key_id(integrity_secret)

        try:
            payload = self._load_integrity_key_file()
        except FileNotFoundError:
            payload = self._create_integrity_key_file()

        try:
            integrity_secret = base64.b64decode(
                payload["secret_b64"].encode("ascii"), validate=True
            )
        except Exception as exc:
            raise ReceiptIntegrityKeyError(
                "Receipt integrity key file contains an invalid base64 secret"
            ) from exc

        if len(integrity_secret) < 32:
            raise ReceiptIntegrityKeyError(
                "Receipt integrity signing secret must be at least 32 bytes"
            )

        return integrity_secret, payload["key_id"]

    @staticmethod
    def _canonical_receipt_payload(receipt: Receipt, prev_hash: str) -> Dict[str, Any]:
        """Stable serialized representation used for receipt chain hashing."""
        return canonical_receipt_payload(receipt, prev_hash)

    def _compute_integrity_hash(self, receipt: Receipt, prev_hash: str) -> str:
        return compute_integrity_hash(receipt, prev_hash)

    def _compute_integrity_signature(self, integrity_hash: str) -> Optional[str]:
        return compute_integrity_signature(integrity_hash, self._integrity_secret)

    def _get_latest_integrity_hash(self, conn: sqlite3.Connection) -> str:
        cursor = conn.execute(
            "SELECT integrity_hash FROM receipts ORDER BY rowid DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row and row["integrity_hash"]:
            return row["integrity_hash"]
        return self.INTEGRITY_CHAIN_HEAD

    def _prepare_for_storage(
        self,
        conn: sqlite3.Connection,
        table: str,
        receipt: Receipt,
    ) -> Receipt:
        if table != "receipts" or receipt.status == ReceiptStatus.PENDING.value:
            return receipt

        prev_hash = self._get_latest_integrity_hash(conn)
        integrity_hash = self._compute_integrity_hash(receipt, prev_hash)
        return replace(
            receipt,
            integrity_prev_hash=prev_hash,
            integrity_hash=integrity_hash,
            integrity_key_id=self._integrity_key_id,
            integrity_signature=self._compute_integrity_signature(integrity_hash),
        )

    def _insert_receipt(self, conn: sqlite3.Connection, table: str, receipt: Receipt) -> Receipt:
        stored = self._prepare_for_storage(conn, table, receipt)
        conn.execute(f"""
            INSERT INTO {table} (
                id, timestamp, action_type, action_name,
                inputs, outputs, status, duration_ms,
                token_count, tier, parent_id, quest_id,
                error_message, metadata,
                operator_id, session_id, integrity_prev_hash, integrity_hash,
                integrity_key_id, integrity_signature
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stored.id,
            stored.timestamp,
            stored.action_type,
            stored.action_name,
            json.dumps(stored.inputs),
            json.dumps(stored.outputs),
            stored.status,
            stored.duration_ms,
            stored.token_count,
            stored.tier,
            stored.parent_id,
            stored.quest_id,
            stored.error_message,
            json.dumps(stored.metadata),
            stored.operator_id,
            stored.session_id,
            stored.integrity_prev_hash,
            stored.integrity_hash,
            stored.integrity_key_id,
            stored.integrity_signature,
        ))
        return stored

    def _get_staged(self, receipt_id: str) -> Optional[Receipt]:
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM receipt_staging WHERE id = ?",
            (receipt_id,),
        )
        row = cursor.fetchone()
        if row:
            return self._row_to_receipt(row)
        return None

    def _emit_observability_bridge(self, receipt: Receipt) -> None:
        try:
            from src.observability.receipt_bridge import on_receipt_written
            on_receipt_written(receipt.to_dict())
        except Exception as exc:
            logger.warning(
                "Observability receipt bridge emission failed for receipt '%s': %s",
                receipt.id,
                exc,
            )

    def create(self, receipt: Receipt) -> Receipt:
        """
        Persist a new receipt.

        Enforces operator identity requirements: if the receipt's action_type
        is in IDENTITY_REQUIRED_TYPES, operator_id must be present and valid.
        Raises IdentityRequiredError or InvalidIdentityError on violation.

        Args:
            receipt: The receipt to store

        Returns:
            The stored receipt
        """
        self._enforce_identity(receipt)

        if receipt.status == ReceiptStatus.PENDING.value:
            with self._transaction() as conn:
                stored = self._insert_receipt(conn, "receipt_staging", receipt)
        else:
            with self._lock:
                with self._transaction() as conn:
                    stored = self._insert_receipt(conn, "receipts", receipt)

        if stored.status != ReceiptStatus.PENDING.value:
            self._emit_observability_bridge(stored)

        return stored

    def _enforce_identity(self, receipt: Receipt) -> None:
        """Check operator identity requirements before writing.

        If the receipt type requires identity and none is supplied,
        a GOVERNANCE_WRITE_ERROR receipt is persisted (as a fallback
        audit trail) and IdentityRequiredError is raised.
        """
        from src.core.operator_identity import (
            IDENTITY_REQUIRED_TYPES,
            IdentityRequiredError,
            InvalidIdentityError,
        )

        if receipt.action_type not in IDENTITY_REQUIRED_TYPES:
            return

        if not receipt.operator_id:
            # Persist a governance write error receipt for audit trail
            self._record_governance_write_error(
                receipt.action_type, "missing_identity"
            )
            raise IdentityRequiredError(receipt.action_type)

        if receipt.operator_id == "SYSTEM":
            # SYSTEM identity is never valid on human-required receipt types
            self._record_governance_write_error(
                receipt.action_type, "system_identity_on_human_action"
            )
            raise IdentityRequiredError(receipt.action_type)

    def _record_governance_write_error(
        self, attempted_type: str, error_class: str
    ) -> None:
        """Persist a GOVERNANCE_WRITE_ERROR receipt as fallback audit trail.

        This receipt does NOT require OperatorIdentity (it may be written
        when identity is unavailable). It is never blocked by identity
        enforcement; that would create a circular dependency.
        """
        try:
            error_receipt = Receipt(
                action_type=ActionType.GOVERNANCE_WRITE_ERROR.value,
                action_name="identity_enforcement",
                inputs={
                    "attempted_receipt_type": attempted_type,
                    "error_class": error_class,
                },
                outputs={},
                status=ReceiptStatus.FAILURE.value,
                metadata={"enforcement": "operator_identity"},
                error_message=f"IdentityRequiredError: {attempted_type}",
            )
            with self._lock:
                with self._transaction() as conn:
                    self._insert_receipt(conn, "receipts", error_receipt)
        except Exception as exc:
            # Last resort: if even the error receipt fails, log it.
            # The AuditLogger (hash-chained text log) is the final fallback.
            logger.error(
                "GOVERNANCE_WRITE_ERROR receipt itself failed for type=%s "
                "error_class=%s: %s",
                attempted_type,
                error_class,
                exc,
                exc_info=True,
            )

    def update(self, receipt: Receipt) -> Receipt:
        """
        Finalize a staged receipt into the immutable log.

        Args:
            receipt: The finalized receipt (success/failure)

        Returns:
            The immutable finalized receipt
        """
        if receipt.status == ReceiptStatus.PENDING.value:
            raise ImmutableReceiptError("Pending receipts must be staged, not updated")

        staged = self._get_staged(receipt.id)
        if staged is not None:
            with self._lock:
                with self._transaction() as conn:
                    stored = self._insert_receipt(conn, "receipts", receipt)
                    conn.execute(
                        "DELETE FROM receipt_staging WHERE id = ?",
                        (receipt.id,),
                    )
            self._emit_observability_bridge(stored)
            return stored

        existing = self.get(receipt.id)
        if existing is not None:
            if existing.status == ReceiptStatus.PENDING.value:
                finalized_metadata = dict(receipt.metadata or {})
                finalized_metadata.setdefault("supersedes_receipt_id", existing.id)
                finalized_metadata.setdefault("immutability_transition", "legacy_pending_finalize")
                finalized_receipt = Receipt(
                    action_type=receipt.action_type,
                    action_name=receipt.action_name,
                    inputs=receipt.inputs,
                    outputs=receipt.outputs,
                    status=receipt.status,
                    duration_ms=receipt.duration_ms,
                    token_count=receipt.token_count,
                    tier=receipt.tier,
                    parent_id=existing.id,
                    quest_id=receipt.quest_id,
                    error_message=receipt.error_message,
                    metadata=finalized_metadata,
                    operator_id=receipt.operator_id,
                    session_id=receipt.session_id,
                )
                with self._lock:
                    with self._transaction() as conn:
                        stored = self._insert_receipt(conn, "receipts", finalized_receipt)
                self._emit_observability_bridge(stored)
                return stored
            raise ImmutableReceiptError(
                f"Receipt {receipt.id} is already finalized and cannot be mutated"
            )

        raise ImmutableReceiptError(
            f"Receipt {receipt.id} has no staged or legacy pending predecessor and cannot be finalized directly"
        )

    def get(self, receipt_id: str) -> Optional[Receipt]:
        """
        Retrieve a receipt by ID.

        Args:
            receipt_id: The unique receipt identifier

        Returns:
            The receipt if found, None otherwise
        """
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM receipts WHERE id = ?",
            (receipt_id,)
        )
        row = cursor.fetchone()
        if row:
            return self._row_to_receipt(row)
        return None

    def summarize_parent_chain(
        self,
        *,
        since: str,
        until: str,
        quest_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return parent-chain counts and missing-parent gaps for an audit period."""
        base_where = "WHERE timestamp >= ? AND timestamp <= ?"
        params: List[Any] = [since, until]
        if quest_id:
            base_where += " AND quest_id = ?"
            params.append(quest_id)

        conn = self._get_connection()
        total_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM receipts {base_where}",
            params,
        ).fetchone()
        parent_row = conn.execute(
            f"""SELECT COUNT(*) as cnt FROM receipts
                {base_where} AND parent_id IS NOT NULL AND parent_id != ''""",
            params,
        ).fetchone()
        gap_rows = conn.execute(
            f"""
            SELECT r.id, r.parent_id, r.timestamp
            FROM receipts r
            {base_where}
              AND r.parent_id IS NOT NULL
              AND r.parent_id != ''
              AND NOT EXISTS (
                  SELECT 1 FROM receipts p WHERE p.id = r.parent_id
              )
            ORDER BY r.timestamp ASC
            """,
            params,
        ).fetchall()
        return {
            "total_receipts": int(total_row["cnt"] if total_row else 0),
            "receipts_with_parents": int(parent_row["cnt"] if parent_row else 0),
            "missing_parent_gaps": [
                {
                    "receipt_id": row["id"],
                    "orphaned_parent_id": row["parent_id"],
                    "receipt_timestamp": row["timestamp"],
                }
                for row in gap_rows
            ],
        }

    def search(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        action_types: Optional[List[str]] = None,
        time_range_hours: Optional[int] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        risk_tier: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Receipt]:
        """
        Search receipts by text query.

        Searches action_name, inputs, outputs, and error_message.

        Args:
            query: Text to search for
            limit: Maximum results
            offset: Number of matching results to skip
            action_types: Optional list of action types to filter
            time_range_hours: Optional time range in hours
            status: Optional receipt status filter
            quest_id: Optional quest filter
            risk_tier: Optional cognition tier filter
            since: Optional inclusive start timestamp
            until: Optional inclusive end timestamp

        Returns:
            List of matching receipts
        """
        sql, params = self._search_query_sql(
            query=query,
            action_types=action_types,
            time_range_hours=time_range_hours,
            status=status,
            quest_id=quest_id,
            risk_tier=risk_tier,
            since=since,
            until=until,
            select="*",
        )
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def count_search(
        self,
        query: str,
        action_types: Optional[List[str]] = None,
        time_range_hours: Optional[int] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        risk_tier: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> int:
        """Count receipts matching the same filters used by search()."""
        sql, params = self._search_query_sql(
            query=query,
            action_types=action_types,
            time_range_hours=time_range_hours,
            status=status,
            quest_id=quest_id,
            risk_tier=risk_tier,
            since=since,
            until=until,
            select="COUNT(*) as total",
        )
        row = self._get_connection().execute(sql, params).fetchone()
        return int(row["total"] if row else 0)

    def _search_query_sql(
        self,
        *,
        query: str,
        select: str,
        action_types: Optional[List[str]] = None,
        time_range_hours: Optional[int] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        risk_tier: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> tuple[str, List[Any]]:
        """Build a parameterized text-search query for finalized receipts."""
        sql = """
            SELECT {select} FROM receipts
            WHERE (
                action_name LIKE ? OR
                inputs LIKE ? OR
                outputs LIKE ? OR
                error_message LIKE ?
            )
        """.format(select=select)
        pattern = f"%{query}%"
        params: List[Any] = [pattern, pattern, pattern, pattern]

        if action_types:
            placeholders = ",".join(["?" for _ in action_types])
            sql += f" AND action_type IN ({placeholders})"
            params.extend(action_types)

        for clause, value in (
            ("status = ?", status),
            ("quest_id = ?", quest_id),
            ("timestamp >= ?", since),
            ("timestamp <= ?", until),
        ):
            if value:
                sql += f" AND {clause}"
                params.append(value)

        if risk_tier is not None:
            sql += " AND tier = ?"
            params.append(risk_tier)

        if time_range_hours:
            cutoff = datetime.now(timezone.utc)
            from datetime import timedelta
            cutoff = cutoff - timedelta(hours=time_range_hours)
            sql += " AND timestamp >= ?"
            params.append(cutoff.isoformat())

        return sql, params

    def get_quest_receipts(self, quest_id: str) -> List[Receipt]:
        """
        Get all receipts for a specific quest (grouped operation).

        Args:
            quest_id: The quest identifier

        Returns:
            All receipts in the quest, ordered by timestamp
        """
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM receipts WHERE quest_id = ? ORDER BY timestamp ASC",
            (quest_id,)
        )
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def clear(self) -> None:
        """Receipt logs are append-only; runtime clearing is not supported."""
        raise ImmutableReceiptError(
            "Finalized receipts are append-only and cannot be cleared at runtime. "
            "Use external volume reset procedures only for a complete fresh install."
        )

    def get_children(self, parent_id: str) -> List[Receipt]:
        """
        Get all child receipts of a parent operation.

        Args:
            parent_id: The parent receipt ID

        Returns:
            All child receipts, ordered by timestamp
        """
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM receipts WHERE parent_id = ? ORDER BY timestamp ASC",
            (parent_id,)
        )
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def validate_parent_chain(
        self,
        quest_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Find receipts whose parent_id points to a non-existent receipt.

        Useful for audit: every receipt with a parent_id should reference
        an actual receipt.  Orphans indicate tampering or data corruption.

        Args:
            quest_id: Optional quest scope.  If given, only checks receipts
                      within that quest.

        Returns:
            List of dicts with ``receipt_id`` and ``orphaned_parent_id``
            for every broken link found.  Empty list means chain is intact.
        """
        sql = """
            SELECT r.id, r.parent_id
            FROM receipts r
            WHERE r.parent_id IS NOT NULL
              AND r.parent_id != ''
              AND NOT EXISTS (
                  SELECT 1 FROM receipts p WHERE p.id = r.parent_id
              )
        """
        params: List[Any] = []
        if quest_id:
            sql += " AND r.quest_id = ?"
            params.append(quest_id)

        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        return [
            {"receipt_id": row["id"], "orphaned_parent_id": row["parent_id"]}
            for row in cursor.fetchall()
        ]

    def validate_integrity_chain(
        self,
        quest_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Validate the finalized receipt hash/signature chain and report mismatches."""
        sql = "SELECT rowid AS receipt_rowid, * FROM receipts ORDER BY rowid ASC"
        params: List[Any] = []

        conn = self._get_connection()
        rows = conn.execute(sql, params).fetchall()
        issues: List[Dict[str, str]] = []
        expected_prev_hash = self.INTEGRITY_CHAIN_HEAD

        for row in rows:
            receipt = self._row_to_receipt(row)
            include_receipt = quest_id is None or receipt.quest_id == quest_id
            actual_prev = receipt.integrity_prev_hash or ""
            actual_hash = receipt.integrity_hash or ""

            if not actual_prev or not actual_hash:
                if include_receipt:
                    issues.append(
                        {
                            "receipt_id": receipt.id,
                            "issue": "missing_integrity_fields",
                        }
                    )
                expected_prev_hash = actual_hash or expected_prev_hash
                continue

            if include_receipt and actual_prev != expected_prev_hash:
                issues.append(
                    {
                        "receipt_id": receipt.id,
                        "issue": "prev_hash_mismatch",
                        "expected_prev_hash": expected_prev_hash,
                        "actual_prev_hash": actual_prev,
                    }
                )

            expected_hash = self._compute_integrity_hash(receipt, actual_prev)
            if include_receipt and actual_hash != expected_hash:
                issues.append(
                    {
                        "receipt_id": receipt.id,
                        "issue": "integrity_hash_mismatch",
                        "expected_hash": expected_hash,
                        "actual_hash": actual_hash,
                    }
                )

            if include_receipt and not receipt.integrity_signature:
                issues.append(
                    {
                        "receipt_id": receipt.id,
                        "issue": "missing_integrity_signature",
                    }
                )
            elif include_receipt and not receipt.integrity_key_id:
                issues.append(
                    {
                        "receipt_id": receipt.id,
                        "issue": "missing_integrity_key_id",
                    }
                )
            elif include_receipt:
                if receipt.integrity_key_id != self._integrity_key_id:
                    issues.append(
                        {
                            "receipt_id": receipt.id,
                            "issue": "integrity_key_id_mismatch",
                            "expected_key_id": self._integrity_key_id,
                            "actual_key_id": receipt.integrity_key_id or "",
                        }
                    )
                expected_signature = self._compute_integrity_signature(actual_hash)
                if not hmac.compare_digest(
                    receipt.integrity_signature,
                    expected_signature,
                ):
                    issues.append(
                        {
                            "receipt_id": receipt.id,
                            "issue": "integrity_signature_mismatch",
                            "expected_key_id": self._integrity_key_id,
                            "actual_key_id": receipt.integrity_key_id or "",
                        }
                    )

            expected_prev_hash = actual_hash

        return issues

    def get_stats(
        self,
        since: Optional[str] = None,
        quest_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get aggregate statistics for receipts.

        Args:
            since: Optional ISO timestamp to filter from
            quest_id: Optional quest ID to scope stats

        Returns:
            Dictionary with counts, token usage, etc.
        """
        base_query = "SELECT * FROM receipts WHERE 1=1"
        params: List[Any] = []

        if since:
            base_query += " AND timestamp >= ?"
            params.append(since)

        if quest_id:
            base_query += " AND quest_id = ?"
            params.append(quest_id)

        conn = self._get_connection()

        # Total count
        count_cursor = conn.execute(
            f"SELECT COUNT(*) as total FROM ({base_query})",
            params
        )
        total = count_cursor.fetchone()["total"]

        # Status breakdown
        status_cursor = conn.execute(
            f"""SELECT status, COUNT(*) as count
                FROM ({base_query}) GROUP BY status""",
            params
        )
        by_status = {row["status"]: row["count"] for row in status_cursor.fetchall()}

        # Action type breakdown
        type_cursor = conn.execute(
            f"""SELECT action_type, COUNT(*) as count
                FROM ({base_query}) GROUP BY action_type""",
            params
        )
        by_type = {row["action_type"]: row["count"] for row in type_cursor.fetchall()}

        # Token usage
        token_cursor = conn.execute(
            f"""SELECT
                SUM(token_count) as total_tokens,
                AVG(token_count) as avg_tokens,
                MAX(token_count) as max_tokens
                FROM ({base_query}) WHERE token_count IS NOT NULL""",
            params
        )
        token_row = token_cursor.fetchone()

        # Duration stats
        duration_cursor = conn.execute(
            f"""SELECT
                SUM(duration_ms) as total_ms,
                AVG(duration_ms) as avg_ms,
                MAX(duration_ms) as max_ms
                FROM ({base_query}) WHERE duration_ms IS NOT NULL""",
            params
        )
        duration_row = duration_cursor.fetchone()

        return {
            "total_receipts": total,
            "by_status": by_status,
            "by_action_type": by_type,
            "tokens": {
                "total": token_row["total_tokens"] or 0,
                "average": round(token_row["avg_tokens"] or 0, 2),
                "max": token_row["max_tokens"] or 0
            },
            "duration_ms": {
                "total": duration_row["total_ms"] or 0,
                "average": round(duration_row["avg_ms"] or 0, 2),
                "max": duration_row["max_ms"] or 0
            }
        }

    def delete_old(self, days: int = 30) -> int:
        """
        Immutable receipts cannot be deleted in-place.

        Retention enforcement must use archival/export workflows instead of
        destructive deletion from the receipt log.
        """
        raise ImmutableReceiptError(
            "Receipts are immutable and cannot be deleted from the audit log"
        )

    def _row_to_receipt(self, row: sqlite3.Row) -> Receipt:
        """Convert a database row to a Receipt object."""
        return receipt_from_row(row)

    def close(self):
        """Close database connections."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None


# Convenience function for creating receipts
def create_receipt(
    action_type: ActionType,
    action_name: str,
    inputs: Dict[str, Any],
    tier: CognitionTier = CognitionTier.DETERMINISTIC,
    parent_id: Optional[str] = None,
    quest_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    operator_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Receipt:
    """
    Factory function for creating new receipts.

    Args:
        action_type: The type of action
        action_name: Specific name of the operation
        inputs: Input parameters
        tier: Cognition tier for model routing
        parent_id: Optional parent receipt ID
        quest_id: Optional quest ID for grouping
        metadata: Optional additional metadata
        operator_id: Stable operator UUID (required for governance actions)
        session_id: Ephemeral session UUID

    Returns:
        A new Receipt in PENDING status
    """
    return Receipt(
        action_type=action_type.value,
        action_name=action_name,
        inputs=inputs,
        tier=tier.value,
        parent_id=parent_id,
        quest_id=quest_id,
        metadata=metadata or {},
        operator_id=operator_id,
        session_id=session_id,
    )


def create_finalized_receipt(
    action_type: ActionType,
    action_name: str,
    inputs: Dict[str, Any],
    outputs: Optional[Dict[str, Any]] = None,
    *,
    status: ReceiptStatus = ReceiptStatus.SUCCESS,
    tier: CognitionTier = CognitionTier.DETERMINISTIC,
    parent_id: Optional[str] = None,
    quest_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    operator_id: Optional[str] = None,
    session_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    token_count: Optional[int] = None,
    error_message: Optional[str] = None,
) -> Receipt:
    """
    Factory for immutable event receipts that are finalized at creation time.

    Use this for governance and lifecycle events that do not have a pending
    execution window and should land in the immutable audit log immediately.
    """
    return Receipt(
        action_type=action_type.value,
        action_name=action_name,
        inputs=inputs,
        outputs=outputs or {},
        status=status.value,
        duration_ms=duration_ms,
        token_count=token_count,
        tier=tier.value,
        parent_id=parent_id,
        quest_id=quest_id,
        error_message=error_message,
        metadata=metadata or {},
        operator_id=operator_id,
        session_id=session_id,
    )


# Singleton service instance (initialized on first use)
_service_instance: Optional[ReceiptService] = None
_service_lock = threading.Lock()


def get_receipt_service(data_dir: str = "/home/lancelot/data") -> ReceiptService:
    """
    Get the singleton ReceiptService instance.

    Args:
        data_dir: Data directory (only used on first call)

    Returns:
        The global ReceiptService instance
    """
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = ReceiptService(data_dir)
    return _service_instance
