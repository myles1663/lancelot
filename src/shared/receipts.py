"""SQLite-backed receipt storage for autonomous action audit trails.

Pending actions are staged separately and finalized into an append-only
receipt log with hash-chain integrity fields.
"""

import base64
import logging
import os
import uuid
import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict, replace
from enum import Enum
from contextlib import contextmanager

try:
    from .receipt_queries import ReceiptQueryMixin
except ImportError:  # pragma: no cover - legacy top-level import path
    from receipt_queries import ReceiptQueryMixin


logger = logging.getLogger(__name__)


class ImmutableReceiptError(RuntimeError):
    """Raised when code attempts to mutate or delete immutable receipts."""


class ReceiptIntegrityKeyError(RuntimeError):
    """Raised when the receipt signing key cannot be loaded or provisioned."""


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
    TOKEN_MINTED = "token_minted"
    TOKEN_REVOKED = "token_revoked"
    TOKEN_EXPIRED = "token_expired"
    TASK_CREATED = "task_created"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    VERIFY_PASSED = "verify_passed"
    VERIFY_FAILED = "verify_failed"
    VOICE_STT = "voice_stt"
    VOICE_TTS = "voice_tts"
    TOOL_FLOW_EVENT = "tool_flow_event"
    ACTION_CARD_PRESENTED = "action_card_presented"
    ACTION_CARD_RESOLVED = "action_card_resolved"
    HIVE_TASK_EVENT = "hive_task_event"
    HIVE_AGENT_EVENT = "hive_agent_event"
    HIVE_INTERVENTION_EVENT = "hive_intervention_event"
    FEDERATION_HEARTBEAT_EVENT = "federation_heartbeat_event"
    FEDERATION_IDENTITY_EVENT = "federation_identity_event"
    FEDERATION_TOPOLOGY_EVENT = "federation_topology_event"
    FEDERATION_HANDOFF_EVENT = "federation_handoff_event"
    FEDERATION_SOUL_EVENT = "federation_soul_event"
    FEDERATION_BUDGET_EVENT = "federation_budget_event"
    MCP_TOOL_CALL = "mcp_tool_call"
    MCP_TOOL_BLOCKED = "mcp_tool_blocked"
    KILL_SWITCH_ISSUED = "kill_switch_issued"
    KILL_SWITCH_LIFTED = "kill_switch_lifted"
    T3_APPROVED = "t3_approved"
    T3_REJECTED = "t3_rejected"
    SOUL_UPDATED = "soul_updated"
    SOUL_VERSION_PINNED = "soul_version_pinned"
    AGENT_DEPLOYED = "agent_deployed"
    AGENT_STOPPED = "agent_stopped"
    CREDENTIAL_REGISTERED = "credential_registered"
    CREDENTIAL_REVOKED = "credential_revoked"
    MCP_SERVER_REGISTERED = "mcp_server_registered"
    MCP_SERVER_REVOKED = "mcp_server_revoked"
    MCP_T3_APPROVED = "mcp_t3_approved"
    MCP_T3_REJECTED = "mcp_t3_rejected"
    CONNECTOR_ENABLED = "connector_enabled"
    CONNECTOR_DISABLED = "connector_disabled"
    ALLOWLIST_MODIFIED = "allowlist_modified"
    SCHEDULER_TASK_CREATED = "scheduler_task_created"
    SCHEDULER_TASK_DELETED = "scheduler_task_deleted"
    SCHEDULER_TASK_TRIGGERED = "scheduler_task_triggered"
    TOOL_ENABLED = "tool_enabled"
    TOOL_DISABLED = "tool_disabled"
    APL_RULE_APPROVED = "apl_rule_approved"
    APL_RULE_REJECTED = "apl_rule_rejected"
    GOVERNANCE_WRITE_ERROR = "governance_write_error"
    COMPLIANCE_EXPORT_GENERATED = "compliance_export_generated"
    WEBHOOK_DELIVERY_FAILED = "webhook_delivery_failed"
    METRICS_API_QUERY = "metrics_api_query"
    A2A_TASK_RECEIVED = "a2a_task_received"
    A2A_INBOUND_BLOCKED = "a2a_inbound_blocked"
    A2A_TASK_EXECUTING = "a2a_task_executing"
    A2A_TASK_COMPLETED = "a2a_task_completed"
    A2A_DELEGATION_SENT = "a2a_delegation_sent"
    A2A_OUTBOUND_BLOCKED = "a2a_outbound_blocked"
    A2A_DELEGATION_COMPLETED = "a2a_delegation_completed"
    A2A_DELEGATION_FAILED = "a2a_delegation_failed"
    T3_A2A_INBOUND_APPROVAL_REQUEST = "t3_a2a_inbound_approval_request"
    T3_A2A_INBOUND_APPROVED = "t3_a2a_inbound_approved"
    T3_A2A_INBOUND_REJECTED = "t3_a2a_inbound_rejected"
    T3_A2A_OUTBOUND_APPROVAL_REQUEST = "t3_a2a_outbound_approval_request"
    T3_A2A_OUTBOUND_APPROVED = "t3_a2a_outbound_approved"
    T3_A2A_OUTBOUND_REJECTED = "t3_a2a_outbound_rejected"
    A2A_AGENT_REGISTERED = "a2a_agent_registered"
    A2A_AGENT_CARD_UPDATED = "a2a_agent_card_updated"
    A2A_AGENT_CARD_FETCHED = "a2a_agent_card_fetched"
    QUEST_FORKED = "quest_forked"
    QUEST_REPLAYED = "quest_replayed"
    TIME_TRAVEL_INSPECT = "time_travel_inspect"
    T3_FORK_APPROVAL_REQUEST = "t3_fork_approval_request"
    T3_FORK_APPROVED = "t3_fork_approved"
    T3_FORK_REJECTED = "t3_fork_rejected"
    FORK_SOUL_REJECTED = "fork_soul_rejected"
    SOUL_TEMPLATE_APPLIED = "soul_template_applied"
    INCIDENT_OPENED = "incident_opened"
    INCIDENT_PAGED = "incident_paged"
    INCIDENT_ACKNOWLEDGED = "incident_acknowledged"
    INCIDENT_STATUS_UPDATED = "incident_status_updated"
    INCIDENT_TIMELINE_ENTRY = "incident_timeline_entry"
    INCIDENT_REMEDIATION_LINKED = "incident_remediation_linked"
    INCIDENT_ESCALATED = "incident_escalated"
    INCIDENT_CLOSED = "incident_closed"
    INCIDENT_FALSE_POSITIVE = "incident_false_positive"
    PLAYBOOK_UPDATED = "playbook_updated"


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
    operator_id: Optional[str] = None
    session_id: Optional[str] = None
    integrity_prev_hash: Optional[str] = None
    integrity_hash: Optional[str] = None
    integrity_key_id: Optional[str] = None
    integrity_signature: Optional[str] = None

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
            metadata=self.metadata,
            operator_id=self.operator_id,
            session_id=self.session_id,
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
            metadata=self.metadata,
            operator_id=self.operator_id,
            session_id=self.session_id,
        )


class ReceiptService(ReceiptQueryMixin):
    """
    SQLite-backed immutable receipt storage service.
    
    Finalized receipts are append-only audit records.
    Pending receipts are kept in an internal staging table until they resolve
    to success or failure, at which point a finalized immutable receipt row is
    inserted into the durable receipt log.
    """
    
    SCHEMA_VERSION = 3
    INTEGRITY_KEY_VERSION = 1
    INTEGRITY_KEY_FILENAME = "receipt_integrity_key.json"
    INTEGRITY_CHAIN_HEAD = "0" * 64

    # Base schema: table + indexes on columns that exist in all versions.
    # The operator_id index is created AFTER migration (see _init_database)
    # to avoid "no such column" errors when upgrading v1 → v2 databases.
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
        metadata TEXT NOT NULL DEFAULT '{}',
        operator_id TEXT,
        session_id TEXT,
        integrity_prev_hash TEXT,
        integrity_hash TEXT,
        integrity_key_id TEXT,
        integrity_signature TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_receipts_timestamp ON receipts(timestamp);
    CREATE INDEX IF NOT EXISTS idx_receipts_action_type ON receipts(action_type);
    CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
    CREATE INDEX IF NOT EXISTS idx_receipts_quest_id ON receipts(quest_id);
    CREATE INDEX IF NOT EXISTS idx_receipts_parent_id ON receipts(parent_id);
    """

    CREATE_STAGING_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS receipt_staging (
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
        metadata TEXT NOT NULL DEFAULT '{}',
        operator_id TEXT,
        session_id TEXT,
        integrity_prev_hash TEXT,
        integrity_hash TEXT,
        integrity_key_id TEXT,
        integrity_signature TEXT
    );
    """

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
        """Initialize database schema with migration support.

        Order matters:
        1. CREATE TABLE + base indexes (columns present in all versions)
        2. Migrate v1 → v2: ALTER TABLE to add operator_id/session_id
        3. CREATE INDEX on operator_id (safe now that column exists)
        """
        with self._transaction() as conn:
            conn.executescript(self.CREATE_TABLE_SQL)
            conn.executescript(self.CREATE_STAGING_TABLE_SQL)
            # Migrate v1 → v2: add operator_id and session_id columns
            try:
                cursor = conn.execute("PRAGMA table_info(receipts)")
                columns = {row[1] for row in cursor.fetchall()}
                if "operator_id" not in columns:
                    conn.execute("ALTER TABLE receipts ADD COLUMN operator_id TEXT")
                if "session_id" not in columns:
                    conn.execute("ALTER TABLE receipts ADD COLUMN session_id TEXT")
                if "integrity_prev_hash" not in columns:
                    conn.execute("ALTER TABLE receipts ADD COLUMN integrity_prev_hash TEXT")
                if "integrity_hash" not in columns:
                    conn.execute("ALTER TABLE receipts ADD COLUMN integrity_hash TEXT")
                if "integrity_key_id" not in columns:
                    conn.execute("ALTER TABLE receipts ADD COLUMN integrity_key_id TEXT")
                if "integrity_signature" not in columns:
                    conn.execute("ALTER TABLE receipts ADD COLUMN integrity_signature TEXT")
            except Exception as exc:
                logger.debug(
                    "Receipt schema migration check skipped while initializing database: %s",
                    exc,
                )
            # Create operator_id index AFTER migration so column is guaranteed
            # to exist (whether from fresh CREATE TABLE or ALTER TABLE).
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_receipts_operator_id "
                "ON receipts(operator_id)"
            )
            cursor = conn.execute("PRAGMA table_info(receipt_staging)")
            staging_columns = {row[1] for row in cursor.fetchall()}
            if "operator_id" not in staging_columns:
                conn.execute("ALTER TABLE receipt_staging ADD COLUMN operator_id TEXT")
            if "session_id" not in staging_columns:
                conn.execute("ALTER TABLE receipt_staging ADD COLUMN session_id TEXT")
            if "integrity_prev_hash" not in staging_columns:
                conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_prev_hash TEXT")
            if "integrity_hash" not in staging_columns:
                conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_hash TEXT")
            if "integrity_key_id" not in staging_columns:
                conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_key_id TEXT")
            if "integrity_signature" not in staging_columns:
                conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_signature TEXT")

    def _derive_env_key_id(self, integrity_secret: bytes) -> str:
        configured = os.environ.get("LANCELOT_RECEIPT_HMAC_KEY_ID", "").strip()
        if configured:
            return configured
        digest = hashlib.sha256(integrity_secret).hexdigest()[:12]
        return f"env:receipt-hmac:{digest}"

    def _build_local_key_record(self, integrity_secret: bytes) -> Dict[str, Any]:
        digest = hashlib.sha256(integrity_secret).hexdigest()[:12]
        return {
            "version": self.INTEGRITY_KEY_VERSION,
            "algorithm": "hmac-sha256",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "key_id": f"local:receipt-hmac:{digest}",
            "secret_b64": base64.b64encode(integrity_secret).decode("ascii"),
        }

    def _load_integrity_key_file(self) -> Dict[str, str]:
        try:
            with open(self._integrity_key_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise ReceiptIntegrityKeyError(
                f"Receipt integrity key file is unreadable: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ReceiptIntegrityKeyError(
                "Receipt integrity key file must contain a JSON object"
            )

        version = payload.get("version")
        key_id = payload.get("key_id")
        secret_b64 = payload.get("secret_b64")
        algorithm = payload.get("algorithm")
        if version != self.INTEGRITY_KEY_VERSION:
            raise ReceiptIntegrityKeyError(
                f"Unsupported receipt integrity key version: {version}"
            )
        if algorithm != "hmac-sha256":
            raise ReceiptIntegrityKeyError(
                f"Unsupported receipt integrity key algorithm: {algorithm}"
            )
        if not isinstance(key_id, str) or not key_id.strip():
            raise ReceiptIntegrityKeyError(
                "Receipt integrity key file is missing a valid key_id"
            )
        if not isinstance(secret_b64, str) or not secret_b64.strip():
            raise ReceiptIntegrityKeyError(
                "Receipt integrity key file is missing the signing secret"
            )

        return {
            "key_id": key_id,
            "secret_b64": secret_b64,
        }

    def _create_integrity_key_file(self) -> Dict[str, str]:
        integrity_secret = secrets.token_bytes(32)
        payload = self._build_local_key_record(integrity_secret)
        serialized = f"{json.dumps(payload, indent=2)}\n".encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL

        try:
            fd = os.open(self._integrity_key_path, flags, 0o600)
        except FileExistsError:
            return self._load_integrity_key_file()
        except OSError as exc:
            raise ReceiptIntegrityKeyError(
                f"Unable to create receipt integrity key file: {exc}"
            ) from exc

        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(serialized)
        except Exception as exc:
            try:
                os.unlink(self._integrity_key_path)
            except OSError:
                logger.warning(
                    "Failed to remove incomplete receipt integrity key file '%s'",
                    self._integrity_key_path,
                )
            raise ReceiptIntegrityKeyError(
                f"Unable to persist receipt integrity key file: {exc}"
            ) from exc

        try:
            os.chmod(self._integrity_key_path, 0o600)
        except OSError:
            logger.debug(
                "Receipt integrity key permissions could not be tightened for '%s'",
                self._integrity_key_path,
            )

        return {
            "key_id": payload["key_id"],
            "secret_b64": payload["secret_b64"],
        }

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
        return {
            "id": receipt.id,
            "timestamp": receipt.timestamp,
            "action_type": receipt.action_type,
            "action_name": receipt.action_name,
            "inputs": receipt.inputs,
            "outputs": receipt.outputs,
            "status": receipt.status,
            "duration_ms": receipt.duration_ms,
            "token_count": receipt.token_count,
            "tier": receipt.tier,
            "parent_id": receipt.parent_id,
            "quest_id": receipt.quest_id,
            "error_message": receipt.error_message,
            "metadata": receipt.metadata,
            "operator_id": receipt.operator_id,
            "session_id": receipt.session_id,
            "prev_hash": prev_hash,
        }

    def _compute_integrity_hash(self, receipt: Receipt, prev_hash: str) -> str:
        payload = self._canonical_receipt_payload(receipt, prev_hash)
        serialized = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _compute_integrity_signature(self, integrity_hash: str) -> Optional[str]:
        return hmac.new(
            self._integrity_secret,
            integrity_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

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
        # Handle both v1 (no operator columns) and v2 schemas
        row_keys = row.keys() if hasattr(row, "keys") else []
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
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            operator_id=row["operator_id"] if "operator_id" in row_keys else None,
            session_id=row["session_id"] if "session_id" in row_keys else None,
            integrity_prev_hash=(
                row["integrity_prev_hash"] if "integrity_prev_hash" in row_keys else None
            ),
            integrity_hash=row["integrity_hash"] if "integrity_hash" in row_keys else None,
            integrity_key_id=row["integrity_key_id"] if "integrity_key_id" in row_keys else None,
            integrity_signature=(
                row["integrity_signature"] if "integrity_signature" in row_keys else None
            ),
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
