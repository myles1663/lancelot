# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federated Kill Switch Engine — cross-instance kill switch propagation.

Implements the authority hierarchy for federation-wide kill switches:
- L1: Federation Root (can kill any instance)
- L2: Local Instance (can kill self only)
- L3: Automated (governance-triggered kills)

Five command types:
- LOCAL_KILL: Kill all agents on local instance
- FEDERATION_KILL: Kill all agents on all instances
- TARGETED_KILL: Kill specific agent on specific instance
- CASCADING_KILL: Kill agent and all downstream dependents
- FEATURE_KILL: Disable a feature flag federation-wide
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class KillAuthority(str, Enum):
    """Authority level for kill switch commands."""
    L1_FEDERATION_ROOT = "L1_federation_root"
    L2_LOCAL_INSTANCE = "L2_local_instance"
    L3_AUTOMATED = "L3_automated"


class KillCommandType(str, Enum):
    """Types of kill switch commands."""
    LOCAL_KILL = "local_kill"
    FEDERATION_KILL = "federation_kill"
    TARGETED_KILL = "targeted_kill"
    CASCADING_KILL = "cascading_kill"
    FEATURE_KILL = "feature_kill"


class KillCommandState(str, Enum):
    """State of a kill command."""
    PENDING = "pending"
    PROPAGATING = "propagating"
    COMPLETED = "completed"
    PARTIAL = "partial"       # Some instances acknowledged, some didn't
    FAILED = "failed"
    LIFTED = "lifted"         # Kill was lifted after review


class PropagationAck(str, Enum):
    """Acknowledgment state from a target instance."""
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class KillTarget:
    """A target instance for a kill command."""
    instance_id: str
    ack_state: PropagationAck = PropagationAck.PENDING
    ack_at: Optional[str] = None
    reject_reason: str = ""
    agents_killed: int = 0


@dataclass
class KillCommand:
    """A kill switch command with propagation tracking."""
    command_id: str
    command_type: KillCommandType
    authority: KillAuthority
    issuer_id: str           # Instance or operator that issued the command
    reason: str
    state: KillCommandState = KillCommandState.PENDING
    issued_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    lifted_at: Optional[str] = None
    lifted_by: Optional[str] = None
    lift_review_notes: str = ""

    # Targeting
    target_instance_id: Optional[str] = None   # For TARGETED_KILL
    target_agent_id: Optional[str] = None       # For TARGETED/CASCADING
    target_feature: Optional[str] = None        # For FEATURE_KILL

    # Propagation tracking
    targets: List[KillTarget] = field(default_factory=list)
    timeout_seconds: float = 30.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type.value,
            "authority": self.authority.value,
            "issuer_id": self.issuer_id,
            "reason": self.reason,
            "state": self.state.value,
            "issued_at": self.issued_at,
            "completed_at": self.completed_at,
            "lifted_at": self.lifted_at,
            "lifted_by": self.lifted_by,
            "target_instance_id": self.target_instance_id,
            "target_agent_id": self.target_agent_id,
            "target_feature": self.target_feature,
            "targets": [
                {
                    "instance_id": t.instance_id,
                    "ack_state": t.ack_state.value,
                    "ack_at": t.ack_at,
                    "agents_killed": t.agents_killed,
                }
                for t in self.targets
            ],
        }


class FederatedKillSwitch:
    """Federated kill switch engine.

    Manages kill command lifecycle: issue → propagate → track acks → complete/partial.
    Provides authority validation and pre-lift review gating.
    """

    # Authority matrix: which authority can issue which command types
    _AUTHORITY_MATRIX = {
        KillAuthority.L1_FEDERATION_ROOT: {
            KillCommandType.LOCAL_KILL,
            KillCommandType.FEDERATION_KILL,
            KillCommandType.TARGETED_KILL,
            KillCommandType.CASCADING_KILL,
            KillCommandType.FEATURE_KILL,
        },
        KillAuthority.L2_LOCAL_INSTANCE: {
            KillCommandType.LOCAL_KILL,
            KillCommandType.TARGETED_KILL,  # Self-instance only
        },
        KillAuthority.L3_AUTOMATED: {
            KillCommandType.LOCAL_KILL,
            KillCommandType.TARGETED_KILL,
            KillCommandType.CASCADING_KILL,
        },
    }

    def __init__(
        self,
        self_instance_id: str,
        peer_ids: Optional[List[str]] = None,
        local_kill_handler: Optional[Callable[[str], int]] = None,
    ):
        """
        Args:
            self_instance_id: This instance's ID.
            peer_ids: Known peer instance IDs for propagation.
            local_kill_handler: Callable that kills local agents. Takes reason,
                returns number of agents killed.
        """
        self._self_id = self_instance_id
        self._peer_ids = list(peer_ids or [])
        self._local_kill_handler = local_kill_handler
        self._commands: Dict[str, KillCommand] = {}
        self._lock = threading.Lock()

    def update_peers(self, peer_ids: List[str]) -> None:
        """Update the known peer list."""
        with self._lock:
            self._peer_ids = list(peer_ids)

    def validate_authority(
        self,
        authority: KillAuthority,
        command_type: KillCommandType,
        target_instance_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Validate that the authority level can issue this command type.

        Returns (allowed, reason).
        """
        allowed_types = self._AUTHORITY_MATRIX.get(authority, set())
        if command_type not in allowed_types:
            return False, (
                f"Authority {authority.value} cannot issue {command_type.value}"
            )

        # L2 can only target self for TARGETED_KILL
        if (authority == KillAuthority.L2_LOCAL_INSTANCE
                and command_type == KillCommandType.TARGETED_KILL
                and target_instance_id
                and target_instance_id != self._self_id):
            return False, "L2 authority can only target local instance"

        return True, "Authorized"

    def issue_command(
        self,
        command_id: str,
        command_type: KillCommandType,
        authority: KillAuthority,
        issuer_id: str,
        reason: str,
        target_instance_id: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        target_feature: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> KillCommand:
        """Issue a kill switch command.

        Validates authority, creates the command, and determines targets.
        Does NOT automatically propagate — caller must call propagate_local()
        and handle remote propagation via federation transport.

        Raises ValueError if authority is insufficient.
        """
        allowed, reason_str = self.validate_authority(
            authority, command_type, target_instance_id
        )
        if not allowed:
            raise ValueError(reason_str)

        if not reason.strip():
            raise ValueError("Kill command requires a reason")

        with self._lock:
            cmd = KillCommand(
                command_id=command_id,
                command_type=command_type,
                authority=authority,
                issuer_id=issuer_id,
                reason=reason,
                target_instance_id=target_instance_id,
                target_agent_id=target_agent_id,
                target_feature=target_feature,
                timeout_seconds=timeout_seconds,
            )

            # Determine targets based on command type
            if command_type == KillCommandType.LOCAL_KILL:
                cmd.targets = [KillTarget(instance_id=self._self_id)]
            elif command_type == KillCommandType.FEDERATION_KILL:
                cmd.targets = [KillTarget(instance_id=self._self_id)]
                for pid in self._peer_ids:
                    cmd.targets.append(KillTarget(instance_id=pid))
            elif command_type == KillCommandType.TARGETED_KILL:
                tid = target_instance_id or self._self_id
                cmd.targets = [KillTarget(instance_id=tid)]
            elif command_type == KillCommandType.CASCADING_KILL:
                # Cascading starts at target, includes all downstream peers
                tid = target_instance_id or self._self_id
                cmd.targets = [KillTarget(instance_id=tid)]
                for pid in self._peer_ids:
                    if pid != tid:
                        cmd.targets.append(KillTarget(instance_id=pid))
            elif command_type == KillCommandType.FEATURE_KILL:
                cmd.targets = [KillTarget(instance_id=self._self_id)]
                for pid in self._peer_ids:
                    cmd.targets.append(KillTarget(instance_id=pid))

            cmd.state = KillCommandState.PROPAGATING
            self._commands[command_id] = cmd
            return cmd

    def propagate_local(self, command_id: str) -> int:
        """Execute kill on local instance. Returns agents killed."""
        with self._lock:
            cmd = self._commands.get(command_id)
            if not cmd:
                return 0

            local_target = next(
                (t for t in cmd.targets if t.instance_id == self._self_id),
                None,
            )
            if not local_target:
                return 0

        # Execute outside lock
        agents_killed = 0
        if self._local_kill_handler and cmd.command_type != KillCommandType.FEATURE_KILL:
            try:
                agents_killed = self._local_kill_handler(cmd.reason)
            except Exception as e:
                logger.error("Local kill handler failed: %s", e)

        with self._lock:
            if local_target:
                local_target.ack_state = PropagationAck.ACKNOWLEDGED
                local_target.ack_at = datetime.now(timezone.utc).isoformat()
                local_target.agents_killed = agents_killed
                self._check_completion(cmd)

        return agents_killed

    def record_ack(
        self,
        command_id: str,
        instance_id: str,
        agents_killed: int = 0,
    ) -> bool:
        """Record acknowledgment from a remote instance."""
        with self._lock:
            cmd = self._commands.get(command_id)
            if not cmd:
                return False

            target = next(
                (t for t in cmd.targets if t.instance_id == instance_id),
                None,
            )
            if not target or target.ack_state != PropagationAck.PENDING:
                return False

            target.ack_state = PropagationAck.ACKNOWLEDGED
            target.ack_at = datetime.now(timezone.utc).isoformat()
            target.agents_killed = agents_killed
            self._check_completion(cmd)
            return True

    def record_rejection(
        self,
        command_id: str,
        instance_id: str,
        reason: str = "",
    ) -> bool:
        """Record rejection from a remote instance."""
        with self._lock:
            cmd = self._commands.get(command_id)
            if not cmd:
                return False

            target = next(
                (t for t in cmd.targets if t.instance_id == instance_id),
                None,
            )
            if not target or target.ack_state != PropagationAck.PENDING:
                return False

            target.ack_state = PropagationAck.REJECTED
            target.ack_at = datetime.now(timezone.utc).isoformat()
            target.reject_reason = reason
            self._check_completion(cmd)
            return True

    def check_timeouts(self, command_id: str) -> List[str]:
        """Check for timed-out targets. Returns list of timed-out instance IDs."""
        with self._lock:
            cmd = self._commands.get(command_id)
            if not cmd or cmd.state not in (
                KillCommandState.PROPAGATING, KillCommandState.PENDING
            ):
                return []

            now = datetime.now(timezone.utc)
            issued = datetime.fromisoformat(cmd.issued_at)
            elapsed = (now - issued).total_seconds()

            timed_out = []
            if elapsed >= cmd.timeout_seconds:
                for target in cmd.targets:
                    if target.ack_state == PropagationAck.PENDING:
                        target.ack_state = PropagationAck.TIMEOUT
                        target.ack_at = now.isoformat()
                        timed_out.append(target.instance_id)

                if timed_out:
                    self._check_completion(cmd)

            return timed_out

    def lift_kill(
        self,
        command_id: str,
        lifted_by: str,
        review_notes: str,
    ) -> bool:
        """Lift a kill command after review.

        Requires review notes (pre-lift review gate).
        Returns False if command not found or not in a liftable state.
        """
        if not review_notes.strip():
            raise ValueError("Lift requires review notes")

        with self._lock:
            cmd = self._commands.get(command_id)
            if not cmd:
                return False
            if cmd.state not in (
                KillCommandState.COMPLETED,
                KillCommandState.PARTIAL,
            ):
                return False

            cmd.state = KillCommandState.LIFTED
            cmd.lifted_at = datetime.now(timezone.utc).isoformat()
            cmd.lifted_by = lifted_by
            cmd.lift_review_notes = review_notes
            return True

    def get_command(self, command_id: str) -> Optional[KillCommand]:
        """Get a kill command by ID."""
        with self._lock:
            return self._commands.get(command_id)

    def get_active_commands(self) -> List[KillCommand]:
        """Get all non-lifted commands."""
        with self._lock:
            return [
                cmd for cmd in self._commands.values()
                if cmd.state != KillCommandState.LIFTED
            ]

    def get_all_commands(self) -> List[KillCommand]:
        """Get all commands."""
        with self._lock:
            return list(self._commands.values())

    def _check_completion(self, cmd: KillCommand) -> None:
        """Check if all targets have responded. Caller must hold lock."""
        pending = [t for t in cmd.targets if t.ack_state == PropagationAck.PENDING]
        if pending:
            return

        acked = [t for t in cmd.targets if t.ack_state == PropagationAck.ACKNOWLEDGED]
        if len(acked) == len(cmd.targets):
            cmd.state = KillCommandState.COMPLETED
        else:
            cmd.state = KillCommandState.PARTIAL

        cmd.completed_at = datetime.now(timezone.utc).isoformat()
