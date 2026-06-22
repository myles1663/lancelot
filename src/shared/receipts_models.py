"""Receipt data models and public exceptions."""

import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

try:
    from .receipts_action_types import ActionType
except ImportError:  # pragma: no cover - legacy top-level import path
    from receipts_action_types import ActionType


class ImmutableReceiptError(RuntimeError):
    """Raised when code attempts to mutate or delete immutable receipts."""


class ReceiptIntegrityKeyError(RuntimeError):
    """Raised when the receipt signing key cannot be loaded or provisioned."""


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
