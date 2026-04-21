# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Resume Engine — Fork and Replay Creation Pipelines.

Implements the 8-stage fork creation pipeline and the simpler replay pipeline.
Both produce new quest_ids and run under the CURRENT Soul (never the original).

Fork Creation Pipeline:
1. Receipt Selection — validate source quest exists + retrieve chain
2. State Modification — apply requested field changes
3. Soul Validation — evaluate against fork_permissions
4. Risk Reclassification — re-tier under current Soul
5. T3 Approval Gate — request and await T3 approval (if required)
6. Fork Quest Creation — mint new quest_id + link to source
7. Governed Execution — delegate to the injected governed quest executor
8. Fork Receipt — emit QUEST_FORKED receipt

Replay Pipeline:
1. Receipt Selection — validate source quest
2. Soul Validation — evaluate replay permission
3. T3 Approval Gate — if required by fork_permissions
4. Replay Quest Creation — mint new quest_id + link to source
5. Governed Execution — delegate to the injected governed quest executor
6. Replay Receipt — emit QUEST_REPLAYED receipt

Public API:
    create_fork(source_quest_id, modifications, operator_id, session_id) → ForkResult
    create_replay(source_quest_id, operator_id, session_id) → ReplayResult
    create_inspection(receipt_id, operator_id, session_id) → InspectionResult
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.shared.receipts import ActionType, Receipt, ReceiptStatus, CognitionTier

logger = logging.getLogger(__name__)


# ── Result Types ─────────────────────────────────────────────────

@dataclass
class ForkResult:
    """Result of a fork operation."""
    success: bool
    fork_quest_id: Optional[str] = None
    source_quest_id: Optional[str] = None
    receipt_id: Optional[str] = None
    error: Optional[str] = None
    approval_status: Optional[str] = None  # "approved", "rejected", "pending"
    modifications_applied: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "fork_quest_id": self.fork_quest_id,
            "source_quest_id": self.source_quest_id,
            "receipt_id": self.receipt_id,
            "error": self.error,
            "approval_status": self.approval_status,
            "modifications_applied": self.modifications_applied,
        }


@dataclass
class ReplayResult:
    """Result of a replay operation."""
    success: bool
    replay_quest_id: Optional[str] = None
    source_quest_id: Optional[str] = None
    receipt_id: Optional[str] = None
    error: Optional[str] = None
    approval_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "replay_quest_id": self.replay_quest_id,
            "source_quest_id": self.source_quest_id,
            "receipt_id": self.receipt_id,
            "error": self.error,
            "approval_status": self.approval_status,
        }


@dataclass
class InspectionResult:
    """Result of an inspect operation."""
    success: bool
    receipt_id: Optional[str] = None
    snapshot: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "receipt_id": self.receipt_id,
            "snapshot": self.snapshot,
            "error": self.error,
        }


# ── Resume Engine ────────────────────────────────────────────────

class ResumeEngine:
    """Orchestrates fork, replay, and inspect operations.

    Requires:
        - receipt_service: ReceiptService for reading/writing receipts
        - soul: Active Soul instance (current, not historical)
        - snapshot_reader: StateSnapshotReader for inspect operations
    """

    def __init__(
        self,
        receipt_service: Any,
        soul: Any,
        snapshot_reader: Any = None,
        quest_executor: Optional[Callable[..., Dict[str, Any]]] = None,
        trust_ledger: Any = None,
        data_dir: Optional[str] = None,
    ):
        self._receipt_service = receipt_service
        self._soul = soul
        self._snapshot_reader = snapshot_reader
        self._quest_executor = quest_executor
        self._trust_ledger = trust_ledger
        self._data_dir = data_dir or os.environ.get("LANCELOT_DATA_DIR", "lancelot_data")

    def _get_soul(self) -> Any:
        """Resolve the live Soul object."""
        soul = self._soul
        if soul is None:
            return None
        if hasattr(soul, "fork_permissions") or hasattr(soul, "version"):
            return soul
        return soul() if callable(soul) else soul

    def update_soul(self, soul: Any) -> None:
        """Refresh the Soul/provider used for replay and fork policy."""
        self._soul = soul

    def update_quest_executor(self, quest_executor: Optional[Callable[..., Dict[str, Any]]]) -> None:
        """Refresh the quest execution callback used by replay and fork."""
        self._quest_executor = quest_executor

    def update_trust_ledger(self, trust_ledger: Any) -> None:
        """Refresh the live Trust Ledger/provider used for approval decisions."""
        self._trust_ledger = trust_ledger

    def _resolve_trust_ledger(self) -> Any:
        if self._trust_ledger is not None:
            return self._trust_ledger

        from src.core.governance.trust_ledger import TrustLedger

        return TrustLedger(data_dir=self._data_dir)

    def _execute_governed_quest(
        self,
        *,
        mode: str,
        source_quest_id: str,
        new_quest_id: str,
        modifications: Optional[Dict[str, Any]],
        operator_id: Optional[str],
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        """Delegate replay/fork execution to the injected tasking runtime."""
        if self._quest_executor is None:
            raise RuntimeError(
                "Time-Travel governed quest executor not configured "
                f"for {mode} on quest {source_quest_id}"
            )

        result = self._quest_executor(
            mode=mode,
            source_quest_id=source_quest_id,
            new_quest_id=new_quest_id,
            modifications=modifications or {},
            operator_id=operator_id or "",
            session_id=session_id or "",
        )
        if result is None:
            raise RuntimeError("Time-Travel executor returned no result")
        if not isinstance(result, dict):
            raise RuntimeError(
                f"Time-Travel executor returned unsupported result type: {type(result)!r}"
            )
        if not result:
            raise RuntimeError("Time-Travel executor returned an empty result")
        return result

    def create_inspection(
        self,
        receipt_id: str,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> InspectionResult:
        """Create a read-only inspection of a receipt's governance state.

        Always allowed — no Soul permission check needed for read-only.
        Emits a TIME_TRAVEL_INSPECT receipt.
        """
        from src.timetravel.fork_permissions import evaluate_inspect_request

        # Validate inspect permission (always true, but follows pattern)
        soul = self._get_soul()
        decision = evaluate_inspect_request(soul)
        if not decision.allowed:
            return InspectionResult(
                success=False,
                error=decision.reason,
            )

        # Build snapshot
        snapshot_dict = None
        if self._snapshot_reader is not None:
            try:
                snapshot = self._snapshot_reader.read_snapshot(receipt_id)
                snapshot_dict = snapshot.to_dict()
            except ValueError as e:
                return InspectionResult(success=False, error=str(e))
            except Exception as e:
                logger.warning("Snapshot read failed: %s", e)
                snapshot_dict = {"error": str(e)}

        # Emit TIME_TRAVEL_INSPECT receipt
        inspect_receipt = Receipt(
            action_type=ActionType.TIME_TRAVEL_INSPECT.value,
            action_name="time_travel_inspect",
            inputs={"inspected_receipt_id": receipt_id},
            outputs={"snapshot_generated": snapshot_dict is not None},
            status=ReceiptStatus.SUCCESS.value,
            tier=CognitionTier.DETERMINISTIC.value,
            operator_id=operator_id,
            session_id=session_id,
            metadata={"subsystem": "time_travel"},
        )

        try:
            self._receipt_service.create(inspect_receipt)
        except Exception as e:
            logger.warning("Failed to persist inspect receipt: %s", e)

        return InspectionResult(
            success=True,
            receipt_id=inspect_receipt.id,
            snapshot=snapshot_dict,
        )

    def create_replay(
        self,
        source_quest_id: str,
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ReplayResult:
        """Create a replay of an existing quest under the current Soul.

        Pipeline:
        1. Validate source quest exists
        2. Check Soul fork_permissions for replay
        3. T3 approval gate (if required)
        4. Mint new quest_id
        5. Emit QUEST_REPLAYED receipt
        """
        from src.timetravel.fork_permissions import evaluate_replay_request

        # Stage 1: Validate source quest
        source_receipts = self._receipt_service.get_quest_receipts(source_quest_id)
        if not source_receipts:
            return ReplayResult(
                success=False,
                source_quest_id=source_quest_id,
                error=f"Source quest not found: {source_quest_id}",
            )

        # Stage 2: Soul validation
        soul = self._get_soul()
        decision = evaluate_replay_request(soul)
        if not decision.allowed:
            self._emit_rejection_receipt(
                decision, source_quest_id, operator_id, session_id,
            )
            return ReplayResult(
                success=False,
                source_quest_id=source_quest_id,
                error=decision.reason,
                approval_status="rejected",
            )

        # Stage 3: T3 Approval Gate
        approval = self._request_approval(
            mode="replay",
            source_quest_id=source_quest_id,
            required_tier=decision.required_approval_tier,
            operator_id=operator_id,
            session_id=session_id,
        )
        if not approval["approved"]:
            return ReplayResult(
                success=False,
                source_quest_id=source_quest_id,
                error=approval.get("reason", "Approval denied"),
                approval_status="rejected",
            )

        # Stage 4: Mint new quest_id
        replay_quest_id = str(uuid.uuid4())

        # Stage 5: Governed execution
        try:
            execution = self._execute_governed_quest(
                mode="replay",
                source_quest_id=source_quest_id,
                new_quest_id=replay_quest_id,
                modifications={},
                operator_id=operator_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.error("Replay execution failed: %s", e)
            return ReplayResult(
                success=False,
                replay_quest_id=replay_quest_id,
                source_quest_id=source_quest_id,
                error=f"Replay execution failed: {e}",
            )

        # Stage 6: Emit QUEST_REPLAYED receipt
        replay_receipt = Receipt(
            action_type=ActionType.QUEST_REPLAYED.value,
            action_name="quest_replay",
            inputs={
                "source_quest_id": source_quest_id,
                "source_receipt_count": len(source_receipts),
                "soul_version": getattr(soul, "version", "unknown"),
            },
            outputs={
                "replay_quest_id": replay_quest_id,
                "mode": "replay",
                "execution": execution,
            },
            status=ReceiptStatus.SUCCESS.value,
            tier=CognitionTier.PLANNING.value,
            quest_id=replay_quest_id,
            operator_id=operator_id,
            session_id=session_id,
            metadata={
                "subsystem": "time_travel",
                "source_quest_id": source_quest_id,
                "approval_tier": decision.required_approval_tier,
            },
        )

        try:
            self._receipt_service.create(replay_receipt)
        except Exception as e:
            logger.error("Failed to persist replay receipt: %s", e)
            return ReplayResult(
                success=False,
                source_quest_id=source_quest_id,
                error=f"Receipt persistence failed: {e}",
            )

        return ReplayResult(
            success=True,
            replay_quest_id=replay_quest_id,
            source_quest_id=source_quest_id,
            receipt_id=replay_receipt.id,
            approval_status="approved",
        )

    def create_fork(
        self,
        source_quest_id: str,
        modifications: Dict[str, Any],
        operator_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ForkResult:
        """Create a fork of an existing quest with modifications.

        8-Stage Pipeline:
        1. Receipt Selection — validate source quest
        2. State Modification — validate modification structure
        3. Soul Validation — check fork_permissions
        4. Risk Reclassification — re-tier using source quest history
        5. T3 Approval Gate — request approval
        6. Fork Quest Creation — mint new quest_id
        7. Governed Execution — delegate to the injected governed quest executor
        8. Fork Receipt — emit QUEST_FORKED receipt
        """
        from src.timetravel.fork_permissions import evaluate_fork_request

        # Stage 1: Receipt Selection
        source_receipts = self._receipt_service.get_quest_receipts(source_quest_id)
        if not source_receipts:
            return ForkResult(
                success=False,
                source_quest_id=source_quest_id,
                error=f"Source quest not found: {source_quest_id}",
            )

        # Stage 2: State Modification validation
        if not isinstance(modifications, dict):
            return ForkResult(
                success=False,
                source_quest_id=source_quest_id,
                error="Modifications must be a dict of field_path → new_value",
            )

        # Stage 3: Soul Validation
        soul = self._get_soul()
        decision = evaluate_fork_request(soul, modifications)
        if not decision.allowed:
            self._emit_rejection_receipt(
                decision, source_quest_id, operator_id, session_id,
            )
            return ForkResult(
                success=False,
                source_quest_id=source_quest_id,
                error=decision.reason,
                approval_status="rejected",
            )

        # Stage 4: Risk Reclassification (uses max tier from source receipts)
        max_tier = max((r.tier for r in source_receipts), default=0)

        # Stage 5: T3 Approval Gate
        approval = self._request_approval(
            mode="fork",
            source_quest_id=source_quest_id,
            required_tier=decision.required_approval_tier,
            operator_id=operator_id,
            session_id=session_id,
            modifications=modifications,
        )
        if not approval["approved"]:
            return ForkResult(
                success=False,
                source_quest_id=source_quest_id,
                error=approval.get("reason", "Fork approval denied"),
                approval_status="rejected",
            )

        # Stage 6: Fork Quest Creation
        fork_quest_id = str(uuid.uuid4())

        try:
            execution = self._execute_governed_quest(
                mode="fork",
                source_quest_id=source_quest_id,
                new_quest_id=fork_quest_id,
                modifications=modifications,
                operator_id=operator_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.error("Fork execution failed: %s", e)
            return ForkResult(
                success=False,
                fork_quest_id=fork_quest_id,
                source_quest_id=source_quest_id,
                error=f"Fork execution failed: {e}",
            )

        # Stage 7 completed via the injected governed quest executor above.

        # Stage 8: Fork Receipt
        fork_receipt = Receipt(
            action_type=ActionType.QUEST_FORKED.value,
            action_name="quest_fork",
            inputs={
                "source_quest_id": source_quest_id,
                "source_receipt_count": len(source_receipts),
                "modifications": modifications,
                "soul_version": getattr(soul, "version", "unknown"),
                "max_source_tier": max_tier,
            },
            outputs={
                "fork_quest_id": fork_quest_id,
                "mode": "fork",
                "modifications_applied": list(modifications.keys()),
                "execution": execution,
            },
            status=ReceiptStatus.SUCCESS.value,
            tier=CognitionTier.SYNTHESIS.value,  # Forks are always T3
            quest_id=fork_quest_id,
            operator_id=operator_id,
            session_id=session_id,
            metadata={
                "subsystem": "time_travel",
                "source_quest_id": source_quest_id,
                "approval_tier": decision.required_approval_tier,
                "risk_reclassification_tier": max_tier,
            },
        )

        try:
            self._receipt_service.create(fork_receipt)
        except Exception as e:
            logger.error("Failed to persist fork receipt: %s", e)
            return ForkResult(
                success=False,
                source_quest_id=source_quest_id,
                error=f"Receipt persistence failed: {e}",
            )

        return ForkResult(
            success=True,
            fork_quest_id=fork_quest_id,
            source_quest_id=source_quest_id,
            receipt_id=fork_receipt.id,
            approval_status="approved",
            modifications_applied=modifications,
        )

    def _request_approval(
        self,
        mode: str,
        source_quest_id: str,
        required_tier: int,
        operator_id: Optional[str],
        session_id: Optional[str],
        modifications: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Request T3 approval for a fork/replay operation.

        Emits T3_FORK_APPROVAL_REQUEST receipt and checks for approval.
        In Phase B, approval is synchronous (auto-approved if tier ≤ current).
        War Room async approval will be added in a later phase.
        """
        # Emit approval request receipt (SYSTEM receipt — not human-initiated)
        request_receipt = Receipt(
            action_type=ActionType.T3_FORK_APPROVAL_REQUEST.value,
            action_name=f"t3_fork_approval_request_{mode}",
            inputs={
                "source_quest_id": source_quest_id,
                "mode": mode,
                "required_tier": required_tier,
                "modifications": modifications or {},
            },
            outputs={},
            status=ReceiptStatus.PENDING.value,
            tier=CognitionTier.SYNTHESIS.value,
            metadata={
                "subsystem": "time_travel",
                "approval_type": f"fork_{mode}",
            },
        )

        try:
            self._receipt_service.create(request_receipt)
        except Exception as e:
            logger.warning("Failed to persist approval request: %s", e)

        # Phase B: synchronous approval based on trust tier check
        current_tier = self._get_current_trust_tier()

        if current_tier >= required_tier:
            # Auto-approved — current trust tier meets requirement
            self._emit_approval_decision(
                approved=True,
                request_receipt_id=request_receipt.id,
                source_quest_id=source_quest_id,
                mode=mode,
                operator_id=operator_id,
                session_id=session_id,
                reason=f"Trust tier {current_tier} >= required {required_tier}",
            )
            return {"approved": True, "tier": current_tier}
        else:
            # Rejected — insufficient trust tier
            self._emit_approval_decision(
                approved=False,
                request_receipt_id=request_receipt.id,
                source_quest_id=source_quest_id,
                mode=mode,
                operator_id=operator_id,
                session_id=session_id,
                reason=f"Trust tier {current_tier} < required {required_tier}",
            )
            return {
                "approved": False,
                "tier": current_tier,
                "reason": (
                    f"Insufficient trust tier: current={current_tier}, "
                    f"required={required_tier}"
                ),
            }

    def _get_current_trust_tier(self) -> int:
        """Get the current effective trust tier from the trust ledger."""
        try:
            ledger = self._resolve_trust_ledger()
            return ledger.get_approval_tier()
        except Exception:
            # If trust ledger is unavailable, default to T0 (most restrictive)
            return 0

    def _emit_approval_decision(
        self,
        approved: bool,
        request_receipt_id: str,
        source_quest_id: str,
        mode: str,
        operator_id: Optional[str],
        session_id: Optional[str],
        reason: str,
    ) -> None:
        """Emit a T3_FORK_APPROVED or T3_FORK_REJECTED receipt."""
        action_type = (
            ActionType.T3_FORK_APPROVED.value
            if approved
            else ActionType.T3_FORK_REJECTED.value
        )

        receipt = Receipt(
            action_type=action_type,
            action_name=f"t3_fork_{'approved' if approved else 'rejected'}_{mode}",
            inputs={
                "approval_request_id": request_receipt_id,
                "source_quest_id": source_quest_id,
                "mode": mode,
            },
            outputs={
                "approved": approved,
                "reason": reason,
            },
            status=ReceiptStatus.SUCCESS.value,
            tier=CognitionTier.SYNTHESIS.value,
            parent_id=request_receipt_id,
            operator_id=operator_id,
            session_id=session_id,
            metadata={
                "subsystem": "time_travel",
                "approval_type": f"fork_{mode}",
            },
        )

        try:
            self._receipt_service.create(receipt)
        except Exception as e:
            logger.warning("Failed to persist approval decision receipt: %s", e)

    def _emit_rejection_receipt(
        self,
        decision: Any,
        source_quest_id: str,
        operator_id: Optional[str],
        session_id: Optional[str],
    ) -> None:
        """Emit a FORK_SOUL_REJECTED receipt when the Soul denies the operation."""
        from src.timetravel.fork_permissions import create_rejection_receipt_data

        data = create_rejection_receipt_data(
            decision=decision,
            quest_id=source_quest_id,
            operator_id=operator_id,
        )

        receipt = Receipt(
            action_type=data["action_type"],
            action_name=data["action_name"],
            inputs=data["inputs"],
            outputs=data["outputs"],
            status=data["status"],
            tier=data["tier"],
            quest_id=data["quest_id"],
            operator_id=data.get("operator_id"),
            session_id=session_id,
            metadata=data["metadata"],
        )

        try:
            self._receipt_service.create(receipt)
        except Exception as e:
            logger.warning("Failed to persist soul rejection receipt: %s", e)
