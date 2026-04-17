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
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "ack_state": self.ack_state.value,
            "ack_at": self.ack_at,
            "reject_reason": self.reject_reason,
            "agents_killed": self.agents_killed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KillTarget":
        ack_state = data.get("ack_state", PropagationAck.PENDING.value)
        try:
            ack_enum = PropagationAck(ack_state)
        except Exception:
            ack_enum = PropagationAck.PENDING
        return cls(
            instance_id=data.get("instance_id", ""),
            ack_state=ack_enum,
            ack_at=data.get("ack_at"),
            reject_reason=data.get("reject_reason", ""),
            agents_killed=int(data.get("agents_killed", 0) or 0),
        )


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
                t.to_dict()
                for t in self.targets
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KillCommand":
        try:
            command_type = KillCommandType(data.get("command_type", KillCommandType.LOCAL_KILL.value))
        except Exception:
            command_type = KillCommandType.LOCAL_KILL
        try:
            authority = KillAuthority(data.get("authority", KillAuthority.L2_LOCAL_INSTANCE.value))
        except Exception:
            authority = KillAuthority.L2_LOCAL_INSTANCE
        try:
            state = KillCommandState(data.get("state", KillCommandState.PENDING.value))
        except Exception:
            state = KillCommandState.PENDING
        return cls(
            command_id=data.get("command_id", ""),
            command_type=command_type,
            authority=authority,
            issuer_id=data.get("issuer_id", ""),
            reason=data.get("reason", ""),
            state=state,
            issued_at=data.get("issued_at", datetime.now(timezone.utc).isoformat()),
            completed_at=data.get("completed_at"),
            lifted_at=data.get("lifted_at"),
            lifted_by=data.get("lifted_by"),
            lift_review_notes=data.get("lift_review_notes", ""),
            target_instance_id=data.get("target_instance_id"),
            target_agent_id=data.get("target_agent_id"),
            target_feature=data.get("target_feature"),
            targets=[KillTarget.from_dict(t) for t in data.get("targets", [])],
            timeout_seconds=float(data.get("timeout_seconds", 30.0) or 30.0),
        )


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
        persistence_path: str = "",
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
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._load_from_disk()

    def update_peers(self, peer_ids: List[str]) -> None:
        """Update the known peer list."""
        with self._lock:
            self._peer_ids = list(peer_ids)
            self._persist_to_disk_locked()

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
            self._persist_to_disk_locked()
            return cmd

    def execute_received_command(
        self,
        *,
        command_id: str,
        command_type: str,
        authority: str,
        issuer_id: str,
        reason: str,
        target_instance_id: Optional[str] = None,
        target_agent_id: Optional[str] = None,
        target_feature: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> int:
        """Persist and execute a received federated kill command locally.

        Returns the number of local agents affected. Raises ValueError if the
        command payload is invalid.
        """
        if not command_id:
            raise ValueError("Kill command missing command_id")

        try:
            command_type_enum = KillCommandType(command_type)
        except Exception as exc:
            raise ValueError(f"Unknown kill command type: {command_type}") from exc

        try:
            authority_enum = KillAuthority(authority)
        except Exception as exc:
            raise ValueError(f"Unknown kill authority: {authority}") from exc

        with self._lock:
            existing = self._commands.get(command_id)
            if existing is not None:
                if (
                    existing.command_type != command_type_enum
                    or existing.authority != authority_enum
                    or existing.issuer_id != issuer_id
                ):
                    raise ValueError(
                        "Kill command_id collision with different command metadata"
                    )
                result = self._propagate_local_locked(existing)
                self._persist_to_disk_locked()
                return result

            cmd = KillCommand(
                command_id=command_id,
                command_type=command_type_enum,
                authority=authority_enum,
                issuer_id=issuer_id,
                reason=reason,
                target_instance_id=target_instance_id,
                target_agent_id=target_agent_id,
                target_feature=target_feature,
                timeout_seconds=timeout_seconds,
            )
            cmd.targets = [KillTarget(instance_id=self._self_id)]
            cmd.state = KillCommandState.PROPAGATING
            self._commands[command_id] = cmd
            result = self._propagate_local_locked(cmd)
            self._persist_to_disk_locked()
            return result

    def propagate_local(self, command_id: str) -> int:
        """Execute kill on local instance. Returns agents killed."""
        with self._lock:
            cmd = self._commands.get(command_id)
            if not cmd:
                return 0
            result = self._propagate_local_locked(cmd)
            self._persist_to_disk_locked()
            return result

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
            self._persist_to_disk_locked()
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
            self._persist_to_disk_locked()
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
                    self._persist_to_disk_locked()

            return timed_out

    def sweep_timeouts(self) -> Dict[str, List[str]]:
        """Advance timeout state for all active commands.

        Returns a mapping of command_id to timed-out instance IDs for commands
        that changed during this sweep.
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            updates: Dict[str, List[str]] = {}
            changed = False

            for command_id, cmd in self._commands.items():
                if cmd.state not in (
                    KillCommandState.PROPAGATING,
                    KillCommandState.PENDING,
                ):
                    continue

                issued = datetime.fromisoformat(cmd.issued_at)
                elapsed = (now - issued).total_seconds()
                if elapsed < cmd.timeout_seconds:
                    continue

                timed_out = []
                for target in cmd.targets:
                    if target.ack_state == PropagationAck.PENDING:
                        target.ack_state = PropagationAck.TIMEOUT
                        target.ack_at = now.isoformat()
                        timed_out.append(target.instance_id)

                if timed_out:
                    self._check_completion(cmd)
                    updates[command_id] = timed_out
                    changed = True

            if changed:
                self._persist_to_disk_locked()

            return updates

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
            self._persist_to_disk_locked()
            return True

    def get_command(self, command_id: str) -> Optional[KillCommand]:
        """Get a kill command by ID."""
        self.sweep_timeouts()
        with self._lock:
            return self._commands.get(command_id)

    def get_active_commands(self) -> List[KillCommand]:
        """Get all non-lifted commands."""
        self.sweep_timeouts()
        with self._lock:
            return [
                cmd for cmd in self._commands.values()
                if cmd.state != KillCommandState.LIFTED
            ]

    def get_all_commands(self) -> List[KillCommand]:
        """Get all commands."""
        self.sweep_timeouts()
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
        elif not acked:
            cmd.state = KillCommandState.FAILED
        else:
            cmd.state = KillCommandState.PARTIAL

        cmd.completed_at = datetime.now(timezone.utc).isoformat()

    def _propagate_local_locked(self, cmd: KillCommand) -> int:
        """Execute the local part of a command while preserving command state.

        Caller must already hold ``self._lock``.
        """
        local_target = next(
            (t for t in cmd.targets if t.instance_id == self._self_id),
            None,
        )
        if not local_target:
            return 0

        if cmd.command_type != KillCommandType.FEATURE_KILL and self._local_kill_handler is None:
            local_target.ack_state = PropagationAck.REJECTED
            local_target.ack_at = datetime.now(timezone.utc).isoformat()
            local_target.reject_reason = "Local kill handler not configured"
            self._check_completion(cmd)
            return 0

        agents_killed = 0
        if self._local_kill_handler and cmd.command_type != KillCommandType.FEATURE_KILL:
            try:
                agents_killed = self._local_kill_handler(cmd.reason)
            except Exception as e:
                logger.error("Local kill handler failed: %s", e)
                local_target.ack_state = PropagationAck.REJECTED
                local_target.ack_at = datetime.now(timezone.utc).isoformat()
                local_target.reject_reason = f"Local kill failed: {e}"
                local_target.agents_killed = 0
                self._check_completion(cmd)
                return 0

        local_target.ack_state = PropagationAck.ACKNOWLEDGED
        local_target.ack_at = datetime.now(timezone.utc).isoformat()
        local_target.agents_killed = agents_killed
        self._check_completion(cmd)
        return agents_killed

    def _persist_to_disk_locked(self) -> None:
        if not self._persistence_path:
            return
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "self_instance_id": self._self_id,
                "peer_ids": self._peer_ids,
                "commands": [cmd.to_dict() for cmd in self._commands.values()],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._persistence_path.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to persist federation kill commands: %s", exc)

    def _load_from_disk(self) -> None:
        if not self._persistence_path or not self._persistence_path.exists():
            return
        try:
            data = json.loads(self._persistence_path.read_text(encoding="utf-8"))
            self._peer_ids = list(data.get("peer_ids", self._peer_ids))
            for cmd_data in data.get("commands", []):
                cmd = KillCommand.from_dict(cmd_data)
                if cmd.command_id:
                    self._commands[cmd.command_id] = cmd
        except Exception as exc:
            logger.warning("Failed to load federation kill commands: %s", exc)
