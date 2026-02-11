"""
Trust Ledger — Progressive tier relaxation through demonstrated reliability.

Tracks per-capability success/failure history and proposes tier graduations
when consecutive success thresholds are met. Revokes trust on failure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.core.governance.models import RiskTier
from src.core.governance.trust_models import (
    GraduationEvent,
    GraduationProposal,
    TrustGraduationConfig,
    TrustRecord,
)

logger = logging.getLogger(__name__)


class TrustLedger:
    """Tracks trust records and manages tier graduation lifecycle."""

    def __init__(self, config: TrustGraduationConfig) -> None:
        self._config = config
        self._records: Dict[str, TrustRecord] = {}
        self._proposals: List[GraduationProposal] = []

    def _key(self, capability: str, scope: str) -> str:
        return f"{capability}:{scope}"

    def get_or_create_record(
        self,
        capability: str,
        scope: str,
        default_tier: RiskTier,
        soul_minimum_tier: RiskTier = RiskTier.T0_INERT,
    ) -> TrustRecord:
        """Get existing record or create a new one with defaults."""
        key = self._key(capability, scope)
        if key not in self._records:
            self._records[key] = TrustRecord(
                capability=capability,
                scope=scope,
                current_tier=default_tier,
                default_tier=default_tier,
                soul_minimum_tier=soul_minimum_tier,
            )
        return self._records[key]

    def record_success(self, capability: str, scope: str) -> TrustRecord:
        """Record a successful execution. May trigger graduation check."""
        key = self._key(capability, scope)
        record = self._records.get(key)
        if record is None:
            raise KeyError(f"No trust record for {key}")

        record.consecutive_successes += 1
        record.total_successes += 1
        record.last_success = datetime.now(timezone.utc).isoformat()

        # Decrement cooldown
        if record.cooldown_remaining > 0:
            record.cooldown_remaining -= 1

        # Check if graduation threshold met
        self.check_graduation(record)

        return record

    def record_failure(
        self, capability: str, scope: str, is_rollback: bool = False
    ) -> TrustRecord:
        """Record a failed execution. Resets streak, may revoke trust."""
        key = self._key(capability, scope)
        record = self._records.get(key)
        if record is None:
            raise KeyError(f"No trust record for {key}")

        record.consecutive_successes = 0
        record.total_failures += 1
        record.last_failure = datetime.now(timezone.utc).isoformat()

        if is_rollback:
            record.total_rollbacks += 1

        # Revoke trust if graduated
        if record.is_graduated:
            self.revoke_trust(capability, scope, is_rollback=is_rollback)

        return record

    def check_graduation(self, record: TrustRecord) -> Optional[GraduationProposal]:
        """Check if a record qualifies for tier graduation."""
        if not record.can_graduate:
            return None

        threshold = self._get_threshold(record.current_tier)
        if threshold is None:
            return None

        if record.consecutive_successes >= threshold:
            # Propose graduation: lower tier by 1
            proposed_tier = RiskTier(record.current_tier - 1)
            proposal = GraduationProposal(
                capability=record.capability,
                scope=record.scope,
                current_tier=record.current_tier,
                proposed_tier=proposed_tier,
                consecutive_successes=record.consecutive_successes,
                total_successes=record.total_successes,
                total_failures=record.total_failures,
            )
            record.pending_proposal = proposal
            self._proposals.append(proposal)
            logger.info(
                "Graduation proposal: %s %s→%s (%d consecutive successes)",
                record.capability, record.current_tier.name,
                proposed_tier.name, record.consecutive_successes,
            )
            return proposal

        return None

    def apply_graduation(
        self, proposal_id: str, approved: bool, reason: str = ""
    ) -> TrustRecord:
        """Apply or deny a graduation proposal."""
        # Find the proposal
        proposal = None
        for p in self._proposals:
            if p.id == proposal_id:
                proposal = p
                break
        if proposal is None:
            raise KeyError(f"No proposal with id {proposal_id}")

        key = self._key(proposal.capability, proposal.scope)
        record = self._records.get(key)
        if record is None:
            raise KeyError(f"No trust record for {key}")

        if approved:
            old_tier = record.current_tier
            new_tier = RiskTier(max(record.current_tier - 1, record.soul_minimum_tier))
            record.current_tier = new_tier
            record.graduation_history.append(GraduationEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                from_tier=old_tier,
                to_tier=new_tier,
                trigger="owner_approval",
                consecutive_successes_at_time=record.consecutive_successes,
                owner_approved=True,
            ))
            proposal.status = "approved"
            record.pending_proposal = None
            logger.info("Graduation approved: %s %s→%s", record.capability, old_tier.name, new_tier.name)
        else:
            record.cooldown_remaining = self._config.revocation.cooldown_after_denial
            record.graduation_history.append(GraduationEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                from_tier=record.current_tier,
                to_tier=record.current_tier,
                trigger="owner_denial",
                consecutive_successes_at_time=record.consecutive_successes,
                owner_approved=False,
            ))
            proposal.status = "denied"
            record.pending_proposal = None
            logger.info("Graduation denied: %s (cooldown=%d)", record.capability, record.cooldown_remaining)

        return record

    def revoke_trust(
        self, capability: str, scope: str, reason: str = "", is_rollback: bool = False
    ) -> TrustRecord:
        """Revoke graduated trust, snapping back to default or above."""
        key = self._key(capability, scope)
        record = self._records.get(key)
        if record is None:
            raise KeyError(f"No trust record for {key}")

        old_tier = record.current_tier

        if is_rollback and self._config.revocation.on_rollback == "reset_above_default":
            new_tier = RiskTier(min(record.default_tier + 1, RiskTier.T3_IRREVERSIBLE))
        else:
            new_tier = record.default_tier

        record.current_tier = new_tier
        record.cooldown_remaining = self._config.revocation.cooldown_after_revocation

        # Clear any pending proposal
        if record.pending_proposal:
            record.pending_proposal.status = "revoked"
            record.pending_proposal = None

        record.graduation_history.append(GraduationEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            from_tier=old_tier,
            to_tier=new_tier,
            trigger="failure_revocation",
            consecutive_successes_at_time=record.consecutive_successes,
            owner_approved=None,
        ))

        logger.info(
            "Trust revoked: %s %s→%s (rollback=%s)",
            record.capability, old_tier.name, new_tier.name, is_rollback,
        )
        return record

    def simulate_timeline(
        self,
        capability: str,
        scope: str,
        default_tier: RiskTier,
        soul_minimum: RiskTier,
        num_successes: int,
    ) -> List[dict]:
        """Simulate N successes on a temporary copy. Does NOT modify real state."""
        import copy

        # Create a temporary ledger
        temp_ledger = TrustLedger(self._config)
        temp_ledger.get_or_create_record(capability, scope, default_tier, soul_minimum)

        events = []
        for i in range(num_successes):
            record = temp_ledger.record_success(capability, scope)
            if record.pending_proposal:
                proposal = record.pending_proposal
                events.append({
                    "success_number": i + 1,
                    "event": "graduation_proposed",
                    "from_tier": proposal.current_tier.name,
                    "to_tier": proposal.proposed_tier.name,
                })
                # Auto-approve in simulation
                temp_ledger.apply_graduation(proposal.id, approved=True)
                events.append({
                    "success_number": i + 1,
                    "event": "graduation_applied",
                    "new_tier": record.current_tier.name,
                })

        return events

    def get_effective_tier(self, capability: str, scope: str) -> Optional[RiskTier]:
        """Return current_tier if record exists, None otherwise."""
        key = self._key(capability, scope)
        record = self._records.get(key)
        return record.current_tier if record else None

    def get_record(self, capability: str, scope: str) -> Optional[TrustRecord]:
        """Get a trust record if it exists."""
        return self._records.get(self._key(capability, scope))

    def list_records(self) -> List[TrustRecord]:
        """Return all trust records."""
        return list(self._records.values())

    def pending_proposals(self) -> List[GraduationProposal]:
        """Return all pending proposals."""
        return [p for p in self._proposals if p.status == "pending"]

    def initialize_from_connector(
        self,
        connector_id: str,
        operations: list,
        soul_overrides: Optional[dict] = None,
    ) -> None:
        """Create trust records for all operations from a connector."""
        soul_overrides = soul_overrides or {}
        for op in operations:
            cap = f"connector.{connector_id}.{op.id}"
            soul_min = soul_overrides.get(cap, RiskTier.T0_INERT)
            self.get_or_create_record(
                capability=cap,
                scope="default",
                default_tier=op.default_tier,
                soul_minimum_tier=soul_min,
            )

    def _get_threshold(self, current_tier: RiskTier) -> Optional[int]:
        """Get the consecutive success threshold for graduating from current_tier."""
        thresholds = self._config.thresholds
        if current_tier == RiskTier.T3_IRREVERSIBLE:
            return thresholds.T3_to_T2
        elif current_tier == RiskTier.T2_CONTROLLED:
            return thresholds.T2_to_T1
        elif current_tier == RiskTier.T1_REVERSIBLE:
            return thresholds.T1_to_T0
        return None  # T0 cannot graduate further
