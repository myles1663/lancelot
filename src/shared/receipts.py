"""
Lancelot vNext — Receipt Storage & Service
===========================================
Production-ready receipt system for tracing all autonomous actions.
Every tool call, LLM invocation, and file operation generates a receipt.

Receipts are:
- Mandatory for every autonomous action
- Hidden from users by default
- Always available for audit
- Persisted in SQLite for durability
"""

import os
import uuid
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
from contextlib import contextmanager


class ActionType(str, Enum):
    """Types of actions that generate receipts."""
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    FILE_OP = "file_op"
    ENV_QUERY = "env_query"
    PLAN_STEP = "plan_step"
    VERIFICATION = "verification"
    USER_INTERACTION = "user_interaction"
    SYSTEM = "system"
    # Fix Pack V1 — Execution Authority + Task Graph
    TOKEN_MINTED = "token_minted"
    TOKEN_REVOKED = "token_revoked"
    TOKEN_EXPIRED = "token_expired"
    TASK_CREATED = "task_created"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    VERIFY_PASSED = "verify_passed"
    VERIFY_FAILED = "verify_failed"
    # Fix Pack V1 — Voice Notes
    VOICE_STT = "voice_stt"
    VOICE_TTS = "voice_tts"
    # Business Automation Layer (BAL)
    BAL_CLIENT_EVENT = "bal_client_event"
    BAL_INTAKE_EVENT = "bal_intake_event"
    BAL_REPURPOSE_EVENT = "bal_repurpose_event"
    BAL_DELIVERY_EVENT = "bal_delivery_event"
    BAL_BILLING_EVENT = "bal_billing_event"


class ReceiptStatus(str, Enum):
    """Status of a receipt."""
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


class CognitionTier(int, Enum):
    """Cognition tiers for model routing."""
    DETERMINISTIC = 0      # No LLM, pure logic
    CLASSIFICATION = 1     # Simple routing/classification
    PLANNING = 2           # Complex planning
    SYNTHESIS = 3          # High-risk synthesis


@dataclass
class Receipt:
    """
    Immutable record of an autonomous action.
    
    Every autonomous operation creates a receipt that captures:
    - What was done (action_type, action_name)
    - Inputs and outputs
    - Performance metrics (duration, tokens)
    - Hierarchy (parent_id, quest_id for grouping)
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action_type: str = ActionType.SYSTEM.value
    action_name: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    status: str = ReceiptStatus.PENDING.value
    duration_ms: Optional[int] = None
    token_count: Optional[int] = None
    tier: int = CognitionTier.DETERMINISTIC.value
    parent_id: Optional[str] = None
    quest_id: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Receipt":
        """Create Receipt from dictionary."""
        return cls(**data)

    def complete(self, outputs: Dict[str, Any], duration_ms: int, 
                 token_count: Optional[int] = None) -> "Receipt":
        """Mark receipt as successfully completed."""
        return Receipt(
            id=self.id,
            timestamp=self.timestamp,
            action_type=self.action_type,
            action_name=self.action_name,
            inputs=self.inputs,
            outputs=outputs,
            status=ReceiptStatus.SUCCESS.value,
            duration_ms=duration_ms,
            token_count=token_count,
            tier=self.tier,
            parent_id=self.parent_id,
            quest_id=self.quest_id,
            error_message=None,
            metadata=self.metadata
        )

    def fail(self, error_message: str, duration_ms: int) -> "Receipt":
        """Mark receipt as failed."""
        return Receipt(
            id=self.id,
            timestamp=self.timestamp,
            action_type=self.action_type,
            action_name=self.action_name,
            inputs=self.inputs,
            outputs={},
            status=ReceiptStatus.FAILURE.value,
            duration_ms=duration_ms,
            token_count=None,
            tier=self.tier,
            parent_id=self.parent_id,
            quest_id=self.quest_id,
            error_message=error_message,
            metadata=self.metadata
        )


class ReceiptService:
    """
    Production-ready SQLite-backed receipt storage service.
    
    Thread-safe, with connection pooling and automatic schema migration.
    Designed for high-volume autonomous operation logging.
    """
    
    SCHEMA_VERSION = 1
    
    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS receipts (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        action_type TEXT NOT NULL,
        action_name TEXT NOT NULL,
        inputs TEXT NOT NULL,
        outputs TEXT NOT NULL,
        status TEXT NOT NULL,
        duration_ms INTEGER,
        token_count INTEGER,
        tier INTEGER NOT NULL DEFAULT 0,
        parent_id TEXT,
        quest_id TEXT,
        error_message TEXT,
        metadata TEXT NOT NULL DEFAULT '{}'
    );
    
    CREATE INDEX IF NOT EXISTS idx_receipts_timestamp ON receipts(timestamp);
    CREATE INDEX IF NOT EXISTS idx_receipts_action_type ON receipts(action_type);
    CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
    CREATE INDEX IF NOT EXISTS idx_receipts_quest_id ON receipts(quest_id);
    CREATE INDEX IF NOT EXISTS idx_receipts_parent_id ON receipts(parent_id);
    """

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        """
        Initialize the receipt service.
        
        Args:
            data_dir: Directory for storing receipts.db
        """
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "receipts.db")
        self._local = threading.local()
        self._lock = threading.Lock()
        
        # Ensure data directory exists
        os.makedirs(data_dir, exist_ok=True)
        
        # Initialize database schema
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent performance
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
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
        """Initialize database schema."""
        with self._transaction() as conn:
            conn.executescript(self.CREATE_TABLE_SQL)

    def create(self, receipt: Receipt) -> Receipt:
        """
        Persist a new receipt.
        
        Args:
            receipt: The receipt to store
            
        Returns:
            The stored receipt
        """
        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO receipts (
                    id, timestamp, action_type, action_name,
                    inputs, outputs, status, duration_ms,
                    token_count, tier, parent_id, quest_id,
                    error_message, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                receipt.id,
                receipt.timestamp,
                receipt.action_type,
                receipt.action_name,
                json.dumps(receipt.inputs),
                json.dumps(receipt.outputs),
                receipt.status,
                receipt.duration_ms,
                receipt.token_count,
                receipt.tier,
                receipt.parent_id,
                receipt.quest_id,
                receipt.error_message,
                json.dumps(receipt.metadata)
            ))
        return receipt

    def update(self, receipt: Receipt) -> Receipt:
        """
        Update an existing receipt (e.g., when completing).
        
        Args:
            receipt: The receipt with updated fields
            
        Returns:
            The updated receipt
        """
        with self._transaction() as conn:
            conn.execute("""
                UPDATE receipts SET
                    outputs = ?,
                    status = ?,
                    duration_ms = ?,
                    token_count = ?,
                    error_message = ?,
                    metadata = ?
                WHERE id = ?
            """, (
                json.dumps(receipt.outputs),
                receipt.status,
                receipt.duration_ms,
                receipt.token_count,
                receipt.error_message,
                json.dumps(receipt.metadata),
                receipt.id
            ))
        return receipt

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

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        action_type: Optional[str] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None
    ) -> List[Receipt]:
        """
        List receipts with optional filtering.
        
        Args:
            limit: Maximum number of receipts to return
            offset: Number of receipts to skip
            action_type: Filter by action type
            status: Filter by status
            quest_id: Filter by quest ID
            since: Filter receipts after this ISO timestamp
            until: Filter receipts before this ISO timestamp
            
        Returns:
            List of matching receipts
        """
        query = "SELECT * FROM receipts WHERE 1=1"
        params: List[Any] = []
        
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        if quest_id:
            query += " AND quest_id = ?"
            params.append(quest_id)
        
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        
        if until:
            query += " AND timestamp <= ?"
            params.append(until)
        
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        conn = self._get_connection()
        cursor = conn.execute(query, params)
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def search(
        self,
        query: str,
        limit: int = 50,
        action_types: Optional[List[str]] = None,
        time_range_hours: Optional[int] = None
    ) -> List[Receipt]:
        """
        Search receipts by text query.
        
        Searches action_name, inputs, outputs, and error_message.
        
        Args:
            query: Text to search for
            limit: Maximum results
            action_types: Optional list of action types to filter
            time_range_hours: Optional time range in hours
            
        Returns:
            List of matching receipts
        """
        sql = """
            SELECT * FROM receipts 
            WHERE (
                action_name LIKE ? OR
                inputs LIKE ? OR
                outputs LIKE ? OR
                error_message LIKE ?
            )
        """
        pattern = f"%{query}%"
        params: List[Any] = [pattern, pattern, pattern, pattern]
        
        if action_types:
            placeholders = ",".join(["?" for _ in action_types])
            sql += f" AND action_type IN ({placeholders})"
            params.extend(action_types)
        
        if time_range_hours:
            cutoff = datetime.now(timezone.utc)
            from datetime import timedelta
            cutoff = cutoff - timedelta(hours=time_range_hours)
            sql += " AND timestamp >= ?"
            params.append(cutoff.isoformat())
        
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

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
        Delete receipts older than specified days.
        
        Args:
            days: Number of days to retain
            
        Returns:
            Number of deleted receipts
        """
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM receipts WHERE timestamp < ?",
                (cutoff.isoformat(),)
            )
            return cursor.rowcount

    def _row_to_receipt(self, row: sqlite3.Row) -> Receipt:
        """Convert a database row to a Receipt object."""
        return Receipt(
            id=row["id"],
            timestamp=row["timestamp"],
            action_type=row["action_type"],
            action_name=row["action_name"],
            inputs=json.loads(row["inputs"]),
            outputs=json.loads(row["outputs"]),
            status=row["status"],
            duration_ms=row["duration_ms"],
            token_count=row["token_count"],
            tier=row["tier"],
            parent_id=row["parent_id"],
            quest_id=row["quest_id"],
            error_message=row["error_message"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {}
        )

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
    metadata: Optional[Dict[str, Any]] = None
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
        metadata=metadata or {}
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
