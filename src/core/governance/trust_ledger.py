"""
Trust Ledger - Progressive tier relaxation through demonstrated reliability.

Tracks per-capability success/failure history and proposes tier graduations
when consecutive success thresholds are met. Revokes trust on failure.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.core.governance.models import RiskTier
from src.core.governance.trust_models import (
    GraduationEvent,
    GraduationProposal,
    TrustGraduationConfig,
    TrustRecord,
)

logger = logging.getLogger(__name__)

_PERSISTENCE_FILENAME = "trust_ledger.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrustLedger:
    """Tracks trust records and manages tier graduation lifecycle."""

    def __init__(
        self,
        config: Optional[TrustGraduationConfig] = None,
        *,
        data_dir: Optional[str] = None,
        persistence_path: Optional[str] = None,
        auto_persist: Optional[bool] = None,
    ) -> None:
        self._config = config or TrustGraduationConfig()
        self._records: Dict[str, TrustRecord] = {}
        self._proposals: List[GraduationProposal] = []
        self._lock = threading.RLock()
        self._persistence_path = self._resolve_persistence_path(
            data_dir=data_dir,
            persistence_path=persistence_path,
        )
        self._auto_persist = (
            self._persistence_path is not None if auto_persist is None else auto_persist
        )
        if self._auto_persist and self._persistence_path is not None:
            self._load_state()

    @staticmethod
    def _resolve_persistence_path(
        *,
        data_dir: Optional[str],
        persistence_path: Optional[str],
    ) -> Optional[Path]:
        if persistence_path:
            return Path(persistence_path)
        if data_dir:
            return Path(data_dir) / "governance" / _PERSISTENCE_FILENAME
        return None

    def _backup_path(self) -> Optional[Path]:
        if self._persistence_path is None:
            return None
        return self._persistence_path.with_suffix(self._persistence_path.suffix + ".bak")

    def _key(self, capability: str, scope: str) -> str:
        return f"{capability}:{scope}"

    def _serialize_event(self, event: GraduationEvent) -> dict:
        return {
            "timestamp": event.timestamp,
            "from_tier": int(event.from_tier),
            "to_tier": int(event.to_tier),
            "trigger": event.trigger,
            "consecutive_successes_at_time": event.consecutive_successes_at_time,
            "owner_approved": event.owner_approved,
        }

    def _deserialize_event(self, payload: dict) -> GraduationEvent:
        return GraduationEvent(
            timestamp=payload.get("timestamp", ""),
            from_tier=RiskTier(payload.get("from_tier", int(RiskTier.T3_IRREVERSIBLE))),
            to_tier=RiskTier(payload.get("to_tier", int(RiskTier.T3_IRREVERSIBLE))),
            trigger=payload.get("trigger", ""),
            consecutive_successes_at_time=payload.get("consecutive_successes_at_time", 0),
            owner_approved=payload.get("owner_approved"),
        )

    def _serialize_proposal(self, proposal: GraduationProposal) -> dict:
        return {
            "id": proposal.id,
            "capability": proposal.capability,
            "scope": proposal.scope,
            "current_tier": int(proposal.current_tier),
            "proposed_tier": int(proposal.proposed_tier),
            "consecutive_successes": proposal.consecutive_successes,
            "total_successes": proposal.total_successes,
            "total_failures": proposal.total_failures,
            "created_at": proposal.created_at,
            "status": proposal.status,
        }

    def _deserialize_proposal(self, payload: dict) -> GraduationProposal:
        return GraduationProposal(
            id=payload.get("id", ""),
            capability=payload.get("capability", ""),
            scope=payload.get("scope", ""),
            current_tier=RiskTier(payload.get("current_tier", int(RiskTier.T3_IRREVERSIBLE))),
            proposed_tier=RiskTier(payload.get("proposed_tier", int(RiskTier.T2_CONTROLLED))),
            consecutive_successes=payload.get("consecutive_successes", 0),
            total_successes=payload.get("total_successes", 0),
            total_failures=payload.get("total_failures", 0),
            created_at=payload.get("created_at", _now_iso()),
            status=payload.get("status", "pending"),
        )

    def _serialize_record(self, record: TrustRecord) -> dict:
        return {
            "capability": record.capability,
            "scope": record.scope,
            "current_tier": int(record.current_tier),
            "default_tier": int(record.default_tier),
            "soul_minimum_tier": int(record.soul_minimum_tier),
            "consecutive_successes": record.consecutive_successes,
            "total_successes": record.total_successes,
            "total_failures": record.total_failures,
            "total_rollbacks": record.total_rollbacks,
            "last_success": record.last_success,
            "last_failure": record.last_failure,
            "graduation_history": [
                self._serialize_event(event)
                for event in record.graduation_history
            ],
            "pending_proposal_id": (
                record.pending_proposal.id if record.pending_proposal is not None else None
            ),
            "cooldown_remaining": record.cooldown_remaining,
        }

    def _deserialize_record(
        self,
        payload: dict,
        proposals_by_id: dict[str, GraduationProposal],
    ) -> TrustRecord:
        proposal_id = payload.get("pending_proposal_id")
        return TrustRecord(
            capability=payload.get("capability", ""),
            scope=payload.get("scope", ""),
            current_tier=RiskTier(payload.get("current_tier", int(RiskTier.T3_IRREVERSIBLE))),
            default_tier=RiskTier(payload.get("default_tier", int(RiskTier.T3_IRREVERSIBLE))),
            soul_minimum_tier=RiskTier(
                payload.get("soul_minimum_tier", int(RiskTier.T0_INERT))
            ),
            consecutive_successes=payload.get("consecutive_successes", 0),
            total_successes=payload.get("total_successes", 0),
            total_failures=payload.get("total_failures", 0),
            total_rollbacks=payload.get("total_rollbacks", 0),
            last_success=payload.get("last_success", ""),
            last_failure=payload.get("last_failure", ""),
            graduation_history=[
                self._deserialize_event(item)
                for item in payload.get("graduation_history", [])
            ],
            pending_proposal=proposals_by_id.get(proposal_id),
            cooldown_remaining=payload.get("cooldown_remaining", 0),
        )

    def _load_from_path(self, path: Path) -> tuple[Dict[str, TrustRecord], List[GraduationProposal]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        proposals = [
            self._deserialize_proposal(item)
            for item in payload.get("proposals", [])
        ]
        proposals_by_id = {proposal.id: proposal for proposal in proposals}
        records = {}
        for item in payload.get("records", []):
            record = self._deserialize_record(item, proposals_by_id)
            records[self._key(record.capability, record.scope)] = record
        return records, proposals

    def _load_state(self) -> None:
        path = self._persistence_path
        if path is None or not path.exists():
            return

        try:
            records, proposals = self._load_from_path(path)
        except Exception as primary_exc:
            backup_path = self._backup_path()
            if backup_path is None or not backup_path.exists():
                raise RuntimeError(
                    f"Failed to load trust ledger state from {path}: {primary_exc}"
                ) from primary_exc
            logger.warning(
                "Trust ledger primary state unreadable, attempting backup recovery: %s",
                primary_exc,
            )
            try:
                records, proposals = self._load_from_path(backup_path)
            except Exception as backup_exc:
                raise RuntimeError(
                    f"Failed to load trust ledger state from {path} and backup {backup_path}"
                ) from backup_exc

        self._records = records
        self._proposals = proposals

    def _build_payload(self) -> dict:
        return {
            "version": "1.0",
            "updated_at": _now_iso(),
            "records": [
                self._serialize_record(record)
                for record in sorted(
                    self._records.values(),
                    key=lambda item: (item.capability, item.scope),
                )
            ],
            "proposals": [
                self._serialize_proposal(proposal)
                for proposal in self._proposals
            ],
        }

    def _persist_if_enabled(self) -> None:
        if not self._auto_persist or self._persistence_path is None:
            return
        self._save_state()

    def _save_state(self) -> None:
        path = self._persistence_path
        if path is None:
            return

        payload = self._build_payload()
        serialized = json.dumps(payload, indent=2, sort_keys=True)

        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        backup_path = self._backup_path()
        if backup_path is not None and path.exists():
            shutil.copyfile(path, backup_path)

        temp_path.write_text(serialized, encoding="utf-8")
        os.replace(temp_path, path)

    def _record_approval_tier(self, record: TrustRecord) -> int:
        return max(0, 3 - int(record.current_tier))

    def get_approval_tier(self) -> int:
        """Aggregate approval tier derived from the best earned trust record."""
        with self._lock:
            if not self._records:
                return 0
            return max(
                self._record_approval_tier(record)
                for record in self._records.values()
            )

    def export_records(self) -> List[dict]:
        """Return JSON-safe trust records for UI and snapshot consumers."""
        with self._lock:
            return [self._serialize_record(record) for record in self._records.values()]

    def get_or_create_record(
        self,
        capability: str,
        scope: str,
        default_tier: RiskTier,
        soul_minimum_tier: RiskTier = RiskTier.T0_INERT,
    ) -> TrustRecord:
        """Get existing record or create a new one with defaults."""
        with self._lock:
            key = self._key(capability, scope)
            if key not in self._records:
                self._records[key] = TrustRecord(
                    capability=capability,
                    scope=scope,
                    current_tier=default_tier,
                    default_tier=default_tier,
                    soul_minimum_tier=soul_minimum_tier,
                )
                self._persist_if_enabled()
            return self._records[key]

    def record_success(self, capability: str, scope: str) -> TrustRecord:
        """Record a successful execution. May trigger graduation check."""
        with self._lock:
            key = self._key(capability, scope)
            record = self._records.get(key)
            if record is None:
                raise KeyError(f"No trust record for {key}")

            record.consecutive_successes += 1
            record.total_successes += 1
            record.last_success = _now_iso()

            if record.cooldown_remaining > 0:
                record.cooldown_remaining -= 1

            self.check_graduation(record)
            self._persist_if_enabled()
            return record

    def record_failure(
        self, capability: str, scope: str, is_rollback: bool = False
    ) -> TrustRecord:
        """Record a failed execution. Resets streak, may revoke trust."""
        with self._lock:
            key = self._key(capability, scope)
            record = self._records.get(key)
            if record is None:
                raise KeyError(f"No trust record for {key}")

            record.consecutive_successes = 0
            record.total_failures += 1
            record.last_failure = _now_iso()

            if is_rollback:
                record.total_rollbacks += 1

            if record.is_graduated:
                self.revoke_trust(capability, scope, is_rollback=is_rollback)

            self._persist_if_enabled()
            return record

    def check_graduation(self, record: TrustRecord) -> Optional[GraduationProposal]:
        """Check if a record qualifies for tier graduation."""
        with self._lock:
            if not record.can_graduate:
                return None

            threshold = self._get_threshold(record.current_tier)
            if threshold is None:
                return None

            if record.consecutive_successes < threshold:
                return None

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
            self._persist_if_enabled()
            logger.info(
                "Graduation proposal: %s %s->%s (%d consecutive successes)",
                record.capability,
                record.current_tier.name,
                proposed_tier.name,
                record.consecutive_successes,
            )
            return proposal

    def apply_graduation(
        self, proposal_id: str, approved: bool, reason: str = ""
    ) -> TrustRecord:
        """Apply or deny a graduation proposal."""
        with self._lock:
            proposal = next((p for p in self._proposals if p.id == proposal_id), None)
            if proposal is None:
                raise KeyError(f"No proposal with id {proposal_id}")
            if proposal.status != "pending":
                raise ValueError(
                    f"Proposal {proposal_id} is no longer pending (status={proposal.status})"
                )

            key = self._key(proposal.capability, proposal.scope)
            record = self._records.get(key)
            if record is None:
                raise KeyError(f"No trust record for {key}")
            if record.pending_proposal is None or record.pending_proposal.id != proposal_id:
                raise ValueError(f"Proposal {proposal_id} is not the active pending proposal for {key}")

            if approved:
                old_tier = record.current_tier
                new_tier = RiskTier(
                    max(record.current_tier - 1, record.soul_minimum_tier)
                )
                record.current_tier = new_tier
                record.cooldown_remaining = 0
                record.graduation_history.append(
                    GraduationEvent(
                        timestamp=_now_iso(),
                        from_tier=old_tier,
                        to_tier=new_tier,
                        trigger="owner_approval",
                        consecutive_successes_at_time=record.consecutive_successes,
                        owner_approved=True,
                    )
                )
                proposal.status = "approved"
                record.pending_proposal = None
                logger.info(
                    "Graduation approved: %s %s->%s",
                    record.capability,
                    old_tier.name,
                    new_tier.name,
                )
            else:
                record.cooldown_remaining = self._config.revocation.cooldown_after_denial
                record.graduation_history.append(
                    GraduationEvent(
                        timestamp=_now_iso(),
                        from_tier=record.current_tier,
                        to_tier=record.current_tier,
                        trigger="owner_denial",
                        consecutive_successes_at_time=record.consecutive_successes,
                        owner_approved=False,
                    )
                )
                proposal.status = "denied"
                record.pending_proposal = None
                logger.info(
                    "Graduation denied: %s (cooldown=%d reason=%s)",
                    record.capability,
                    record.cooldown_remaining,
                    reason or "n/a",
                )

            self._persist_if_enabled()
            return record

    def revoke_trust(
        self, capability: str, scope: str, reason: str = "", is_rollback: bool = False
    ) -> TrustRecord:
        """Revoke graduated trust, snapping back to default or above."""
        with self._lock:
            key = self._key(capability, scope)
            record = self._records.get(key)
            if record is None:
                raise KeyError(f"No trust record for {key}")

            old_tier = record.current_tier
            if is_rollback and self._config.revocation.on_rollback == "reset_above_default":
                new_tier = RiskTier(
                    min(record.default_tier + 1, RiskTier.T3_IRREVERSIBLE)
                )
            else:
                new_tier = record.default_tier

            record.current_tier = new_tier
            record.cooldown_remaining = self._config.revocation.cooldown_after_revocation

            if record.pending_proposal:
                record.pending_proposal.status = "revoked"
                record.pending_proposal = None

            record.graduation_history.append(
                GraduationEvent(
                    timestamp=_now_iso(),
                    from_tier=old_tier,
                    to_tier=new_tier,
                    trigger="failure_revocation",
                    consecutive_successes_at_time=record.consecutive_successes,
                    owner_approved=None,
                )
            )

            self._persist_if_enabled()
            logger.info(
                "Trust revoked: %s %s->%s (rollback=%s reason=%s)",
                record.capability,
                old_tier.name,
                new_tier.name,
                is_rollback,
                reason or "n/a",
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
        temp_ledger = TrustLedger(self._config, auto_persist=False)
        temp_ledger.get_or_create_record(capability, scope, default_tier, soul_minimum)

        events = []
        for index in range(num_successes):
            record = temp_ledger.record_success(capability, scope)
            if record.pending_proposal:
                proposal = record.pending_proposal
                events.append(
                    {
                        "success_number": index + 1,
                        "event": "graduation_proposed",
                        "from_tier": proposal.current_tier.name,
                        "to_tier": proposal.proposed_tier.name,
                    }
                )
                temp_ledger.apply_graduation(proposal.id, approved=True)
                events.append(
                    {
                        "success_number": index + 1,
                        "event": "graduation_applied",
                        "new_tier": record.current_tier.name,
                    }
                )

        return events

    def get_effective_tier(self, capability: str, scope: str) -> Optional[RiskTier]:
        """Return current_tier if record exists, None otherwise."""
        with self._lock:
            key = self._key(capability, scope)
            record = self._records.get(key)
            return record.current_tier if record else None

    def get_record(self, capability: str, scope: str) -> Optional[TrustRecord]:
        """Get a trust record if it exists."""
        with self._lock:
            return self._records.get(self._key(capability, scope))

    def list_records(self) -> List[TrustRecord]:
        """Return all trust records."""
        with self._lock:
            return list(self._records.values())

    def pending_proposals(self) -> List[GraduationProposal]:
        """Return all pending proposals."""
        with self._lock:
            return [proposal for proposal in self._proposals if proposal.status == "pending"]

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
        if current_tier == RiskTier.T2_CONTROLLED:
            return thresholds.T2_to_T1
        if current_tier == RiskTier.T1_REVERSIBLE:
            return thresholds.T1_to_T0
        return None
