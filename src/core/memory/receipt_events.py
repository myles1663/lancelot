"""Receipt emission helpers for structured memory operations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.shared.receipts import CognitionTier, Receipt, ReceiptService, ReceiptStatus

logger = logging.getLogger(__name__)


class MemoryReceiptEmitter:
    """Create finalized receipts for structured memory operations."""

    def __init__(
        self,
        data_dir: str | Path,
        receipt_service: Optional[ReceiptService] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.receipt_service = receipt_service or ReceiptService(str(self.data_dir))

    def emit(
        self,
        *,
        action_type: str,
        action_name: str,
        inputs: Optional[dict[str, Any]] = None,
        outputs: Optional[dict[str, Any]] = None,
        status: ReceiptStatus = ReceiptStatus.SUCCESS,
        duration_ms: Optional[int] = None,
        token_count: Optional[int] = None,
        quest_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[Receipt]:
        """Persist a finalized memory receipt and return it if successful."""
        try:
            receipt = Receipt(
                action_type=action_type,
                action_name=action_name,
                inputs=inputs or {},
                outputs=outputs or {},
                status=status.value,
                duration_ms=duration_ms,
                token_count=token_count,
                tier=CognitionTier.DETERMINISTIC.value,
                quest_id=quest_id,
                operator_id=operator_id,
                session_id=session_id,
                error_message=error_message,
                metadata={
                    "subsystem": "memory",
                    "emitted_at": datetime.now(timezone.utc).isoformat(),
                    **(metadata or {}),
                },
            )
            return self.receipt_service.create(receipt)
        except Exception as exc:  # pragma: no cover - receipt failure must not mask runtime action
            logger.warning("Failed to emit memory receipt %s: %s", action_type, exc)
            return None
