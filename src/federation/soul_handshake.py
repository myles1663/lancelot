# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Soul Version Handshake — protocol for propagating Soul updates across federation peers.

When an operator updates a Soul, the change is pushed to all known peers.
Each peer responds with a handshake acknowledgment. The push is not considered
complete until all peers have responded (or timed out).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HandshakeState(str, Enum):
    """State of a Soul version push handshake with a single peer."""
    INITIATED = "initiated"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    GOVERNANCE_DENIAL = "governance_denial"


@dataclass
class SoulVersionHandshake:
    """Record of a Soul version propagation to one peer."""
    handshake_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    initiator_instance_id: str = ""
    target_instance_id: str = ""
    old_soul_hash: str = ""
    new_soul_hash: str = ""
    state: HandshakeState = HandshakeState.INITIATED
    initiated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    acknowledged_at: Optional[str] = None
    timeout_at: Optional[str] = None
    target_execution_state: Optional[Dict[str, Any]] = None
    reason_if_rejected: Optional[str] = None

    def is_terminal(self) -> bool:
        """Check if this handshake has reached a terminal state."""
        return self.state in (
            HandshakeState.ACKNOWLEDGED,
            HandshakeState.REJECTED,
            HandshakeState.TIMEOUT,
            HandshakeState.GOVERNANCE_DENIAL,
        )

    def is_success(self) -> bool:
        return self.state == HandshakeState.ACKNOWLEDGED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "handshake_id": self.handshake_id,
            "initiator_instance_id": self.initiator_instance_id,
            "target_instance_id": self.target_instance_id,
            "old_soul_hash": self.old_soul_hash,
            "new_soul_hash": self.new_soul_hash,
            "state": self.state.value,
            "initiated_at": self.initiated_at,
            "acknowledged_at": self.acknowledged_at,
            "timeout_at": self.timeout_at,
            "reason_if_rejected": self.reason_if_rejected,
        }


@dataclass
class SoulPushResult:
    """Aggregate result of a Soul version push to all peers."""
    new_soul_hash: str
    handshakes: List[SoulVersionHandshake] = field(default_factory=list)
    all_acknowledged: bool = False
    governance_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "new_soul_hash": self.new_soul_hash,
            "total_peers": len(self.handshakes),
            "acknowledged": sum(1 for h in self.handshakes if h.is_success()),
            "rejected": sum(1 for h in self.handshakes if h.state == HandshakeState.REJECTED),
            "timed_out": sum(1 for h in self.handshakes if h.state == HandshakeState.TIMEOUT),
            "denied": sum(1 for h in self.handshakes if h.state == HandshakeState.GOVERNANCE_DENIAL),
            "all_acknowledged": self.all_acknowledged,
            "governance_gaps": self.governance_gaps,
            "handshakes": [h.to_dict() for h in self.handshakes],
        }


def create_handshakes(
    initiator_instance_id: str,
    target_instance_ids: List[str],
    old_soul_hash: str,
    new_soul_hash: str,
    timeout_s: float = 30.0,
) -> List[SoulVersionHandshake]:
    """Create handshake records for a Soul version push to all known peers.

    Each handshake starts in INITIATED state. The caller is responsible for
    sending the push via the Governance API and processing responses.

    Args:
        initiator_instance_id: UUID of the pushing instance.
        target_instance_ids: List of peer instance IDs to push to.
        old_soul_hash: Hash of the current (old) Soul.
        new_soul_hash: Hash of the new Soul being pushed.
        timeout_s: Seconds before a handshake is considered timed out.

    Returns:
        List of SoulVersionHandshake in INITIATED state.
    """
    now = datetime.now(timezone.utc)
    timeout_at = (now + timedelta(seconds=timeout_s)).isoformat()

    handshakes = []
    for target_id in target_instance_ids:
        hs = SoulVersionHandshake(
            initiator_instance_id=initiator_instance_id,
            target_instance_id=target_id,
            old_soul_hash=old_soul_hash,
            new_soul_hash=new_soul_hash,
            timeout_at=timeout_at,
        )
        handshakes.append(hs)
        logger.info(
            "Soul handshake created: %s → %s (hash %s→%s)",
            initiator_instance_id, target_id, old_soul_hash[:8], new_soul_hash[:8],
        )

    return handshakes


def process_response(
    handshake: SoulVersionHandshake,
    state: HandshakeState,
    peer_execution_state: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> SoulVersionHandshake:
    """Process a peer's response to a Soul version push.

    Transitions the handshake to a terminal state.

    Args:
        handshake: The handshake to update.
        state: The response state from the peer.
        peer_execution_state: Snapshot of peer's task state at response time.
        reason: Human-readable reason (for REJECTED or GOVERNANCE_DENIAL).

    Returns:
        Updated handshake.
    """
    if handshake.is_terminal():
        logger.warning(
            "Handshake %s already terminal (%s), ignoring response %s",
            handshake.handshake_id, handshake.state.value, state.value,
        )
        return handshake

    handshake.state = state
    handshake.acknowledged_at = datetime.now(timezone.utc).isoformat()
    handshake.target_execution_state = peer_execution_state
    handshake.reason_if_rejected = reason

    logger.info(
        "Soul handshake %s → %s (peer=%s)",
        handshake.handshake_id, state.value, handshake.target_instance_id,
    )
    return handshake


def check_timeouts(
    handshakes: List[SoulVersionHandshake],
) -> List[SoulVersionHandshake]:
    """Check for timed-out handshakes and mark them.

    Returns list of handshakes that were just marked as timed out.
    """
    now = datetime.now(timezone.utc)
    timed_out = []

    for hs in handshakes:
        if hs.is_terminal():
            continue
        if hs.timeout_at:
            try:
                timeout_time = datetime.fromisoformat(hs.timeout_at)
                if timeout_time.tzinfo is None:
                    timeout_time = timeout_time.replace(tzinfo=timezone.utc)
                if now >= timeout_time:
                    hs.state = HandshakeState.TIMEOUT
                    hs.acknowledged_at = now.isoformat()
                    timed_out.append(hs)
                    logger.warning(
                        "Soul handshake %s timed out (peer=%s)",
                        hs.handshake_id, hs.target_instance_id,
                    )
            except (ValueError, TypeError):
                pass

    return timed_out


def evaluate_push_result(
    handshakes: List[SoulVersionHandshake],
    new_soul_hash: str,
) -> SoulPushResult:
    """Evaluate the aggregate result of a Soul version push.

    Args:
        handshakes: All handshakes for this push.
        new_soul_hash: The hash of the Soul being pushed.

    Returns:
        SoulPushResult with aggregate status and governance gaps.
    """
    gaps = []
    for hs in handshakes:
        if not hs.is_success():
            gaps.append(hs.target_instance_id)

    all_ack = len(gaps) == 0 and len(handshakes) > 0

    return SoulPushResult(
        new_soul_hash=new_soul_hash,
        handshakes=handshakes,
        all_acknowledged=all_ack,
        governance_gaps=gaps,
    )
