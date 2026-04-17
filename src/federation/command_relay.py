# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Command Relay — Kill switch and pause command propagation to peers.

Wires the FederatedKillSwitch engine to actual HTTP transport. Commands
are signed and broadcast to target peers, with ack/reject tracking.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.federation.identity import FederationIdentity
from src.federation.topology import TopologyRegistry, PeerRecord
from src.federation.transport import FederationTransport

logger = logging.getLogger(__name__)


class CommandRelay:
    """Relays governance commands (kill, pause) to federation peers via HTTP."""

    def __init__(
        self,
        identity: FederationIdentity,
        transport: FederationTransport,
        topology: TopologyRegistry,
        kill_switch=None,
        local_pause_handler=None,
        local_resume_handler=None,
        receipt_mgr=None,
        audit=None,
        command_timeout_s: float = 5.0,
    ):
        self._identity = identity
        self._transport = transport
        self._topology = topology
        self._kill_switch = kill_switch
        self._local_pause_handler = local_pause_handler
        self._local_resume_handler = local_resume_handler
        self._receipt_mgr = receipt_mgr
        self._audit = audit
        self._command_timeout_s = command_timeout_s

    async def propagate_kill(self, command_data: dict) -> Dict[str, bool]:
        """Broadcast a kill command to target peers.

        Args:
            command_data: Serialized kill command dict containing
                command_id, command_type, authority, reason, targets, etc.

        Returns:
            Dict mapping instance_id to success boolean.
        """
        results = await self._broadcast_kill_results(command_data)

        # Process results
        outcome: Dict[str, bool] = {}
        for pid, result in results.items():
            outcome[pid] = result.success
            if result.success:
                logger.info("Kill command acknowledged by peer %s", pid)
                if self._audit:
                    try:
                        self._audit.record(
                            event_type="kill_acknowledged",
                            instance_id=pid,
                            details={
                                "command_id": command_data.get("command_id"),
                                "latency_ms": result.latency_ms,
                            },
                        )
                    except Exception:
                        pass
            else:
                logger.warning(
                    "Kill command failed for peer %s: %s", pid, result.error
                )

        return outcome

    async def issue_and_propagate_kill(self, command_data: dict) -> Dict[str, Any]:
        """Issue a kill command through the real kill-switch engine and propagate it.

        This is the operator-facing path: validate, persist the command, execute
        the local target leg, then fan out to remote peers and record their
        acknowledgements or rejections into the same command ledger.
        """
        if self._kill_switch is None:
            raise RuntimeError("Federated kill switch is not configured")

        command_id = str(command_data.get("command_id", "")).strip()
        if not command_id:
            raise ValueError("command_id is required")

        authority = command_data.get("authority", "")
        command_type = command_data.get("command_type", "")
        issuer_id = command_data.get("issuer_instance_id", "") or self._identity.instance_id
        reason = str(command_data.get("reason", "")).strip()
        target_ids = list(command_data.get("target_instance_ids", []) or [])
        target_instance_id = command_data.get("target_instance_id")
        if target_ids and command_type == "targeted_kill" and not target_instance_id:
            target_instance_id = target_ids[0]

        from src.federation.kill_switch import KillAuthority, KillCommandType

        cmd = self._kill_switch.issue_command(
            command_id=command_id,
            command_type=KillCommandType(command_type),
            authority=KillAuthority(authority),
            issuer_id=issuer_id,
            reason=reason,
            target_instance_id=target_instance_id,
            target_agent_id=command_data.get("target_agent_id"),
            target_feature=command_data.get("target_feature"),
            timeout_seconds=float(command_data.get("timeout_seconds", self._command_timeout_s)),
        )
        local_agents_killed = self._kill_switch.propagate_local(command_id)

        raw_results = await self._broadcast_kill_results(
            {
                **command_data,
                "issuer_instance_id": issuer_id,
                "target_instance_ids": [
                    target.instance_id
                    for target in cmd.targets
                    if target.instance_id != self._identity.instance_id
                ],
            }
        )

        remote_results: Dict[str, bool] = {}
        known_result_ids = set(raw_results.keys())
        for pid, result in raw_results.items():
            remote_results[pid] = result.success
            if result.success:
                self._kill_switch.record_ack(command_id, pid, 0)
            else:
                self._kill_switch.record_rejection(
                    command_id,
                    pid,
                    result.error or "Kill propagation failed",
                )

        for target in cmd.targets:
            if target.instance_id == self._identity.instance_id:
                continue
            if target.instance_id not in known_result_ids:
                self._kill_switch.record_rejection(
                    command_id,
                    target.instance_id,
                    "Target peer unavailable for kill propagation",
                )

        timeout_updates = self._kill_switch.sweep_timeouts()
        final_cmd = self._kill_switch.get_command(command_id)

        return {
            "command_id": command_id,
            "local_agents_killed": local_agents_killed,
            "results": remote_results,
            "total": len(remote_results),
            "timed_out": timeout_updates.get(command_id, []),
            "command": final_cmd.to_dict() if final_cmd else None,
        }

    def handle_kill_command(
        self,
        request_data: dict,
        authenticated_instance_id: Optional[str] = None,
    ) -> dict:
        """Handle an incoming kill command from a peer.

        Validates authority and executes locally.

        Args:
            request_data: The kill command payload.

        Returns:
            Response dict with ack/reject.
        """
        command = request_data.get("command", {})
        issuer_id = request_data.get("issuer_instance_id", "")
        if authenticated_instance_id:
            if issuer_id and issuer_id != authenticated_instance_id:
                return {
                    "accepted": False,
                    "error": (
                        "Issuer instance does not match authenticated peer: "
                        f"{issuer_id} != {authenticated_instance_id}"
                    ),
                    "command_id": command.get("command_id", ""),
                }
            issuer_id = authenticated_instance_id

        command_id = command.get("command_id", "")
        command_type = command.get("command_type", "")
        authority = command.get("authority", "")
        reason = command.get("reason", "")

        # Validate the issuer is a known peer
        peer = self._topology.get_peer(issuer_id)
        if not peer:
            return {
                "accepted": False,
                "error": f"Unknown issuer: {issuer_id}",
                "command_id": command_id,
            }

        # Federation kill ingress is root-authority only.
        if peer.role != "root":
            return {
                "accepted": False,
                "error": f"Insufficient authority: peer role={peer.role}",
                "command_id": command_id,
            }

        if self._kill_switch is None:
            return {
                "accepted": False,
                "error": "Local kill engine not configured",
                "command_id": command_id,
            }

        # Execute local kill through the wired engine.
        try:
            agents_killed = self._kill_switch.execute_received_command(
                command_id=command_id,
                command_type=command_type,
                authority=authority,
                issuer_id=issuer_id,
                reason=reason,
                target_instance_id=command.get("target_instance_id"),
                target_agent_id=command.get("target_agent_id"),
                target_feature=command.get("target_feature"),
                timeout_seconds=float(command.get("timeout_seconds", self._command_timeout_s)),
            )
        except Exception as e:
            logger.error("Local kill execution failed: %s", e)
            return {
                "accepted": False,
                "error": f"Local execution failed: {e}",
                "command_id": command_id,
            }

        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_kill_acknowledged(
                    command_id=command_id,
                    issuer_id=issuer_id,
                    agents_killed=agents_killed,
                )
            except Exception:
                pass

        logger.info(
            "Kill command executed: command_id=%s, type=%s, agents_killed=%d",
            command_id, command_type, agents_killed,
        )

        return {
            "accepted": True,
            "command_id": command_id,
            "agents_killed": agents_killed,
            "instance_id": self._identity.instance_id,
        }

    async def propagate_pause(
        self,
        reason: str,
        target_ids: Optional[List[str]] = None,
        *,
        full_stop: bool = False,
    ) -> Dict[str, bool]:
        """Broadcast a pause signal to peers.

        Args:
            reason: Why the pause is being issued.
            target_ids: Specific peers to pause (None = all).

        Returns:
            Dict mapping instance_id to success boolean.
        """
        if target_ids:
            peers = [
                p for p in self._topology.list_peers()
                if p.instance_id in target_ids
            ]
        else:
            peers = self._topology.list_peers()

        if not peers:
            return {}

        peer_dicts = [
            {"instance_id": p.instance_id, "address": p.address}
            for p in peers
        ]

        payload = {
            "reason": reason,
            "issuer_instance_id": self._identity.instance_id,
            "full_stop": full_stop,
        }

        results = await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/pause",
            body=payload,
            timeout_override_s=self._command_timeout_s,
        )

        return {pid: r.success for pid, r in results.items()}

    async def propagate_resume(
        self,
        reason: str = "",
        target_ids: Optional[List[str]] = None,
    ) -> Dict[str, bool]:
        """Broadcast a resume signal to peers."""
        if target_ids:
            peers = [
                p for p in self._topology.list_peers()
                if p.instance_id in target_ids
            ]
        else:
            peers = self._topology.list_peers()

        if not peers:
            return {}

        peer_dicts = [
            {"instance_id": p.instance_id, "address": p.address}
            for p in peers
        ]

        payload = {
            "reason": reason,
            "issuer_instance_id": self._identity.instance_id,
        }

        results = await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/resume",
            body=payload,
            timeout_override_s=self._command_timeout_s,
        )

        return {pid: r.success for pid, r in results.items()}

    def handle_pause(
        self,
        request_data: dict,
        authenticated_instance_id: Optional[str] = None,
    ) -> dict:
        """Handle an incoming pause signal from a peer."""
        issuer_id = request_data.get("issuer_instance_id", "")
        reason = request_data.get("reason", "")
        full_stop = bool(request_data.get("full_stop", False))
        if authenticated_instance_id:
            if issuer_id and issuer_id != authenticated_instance_id:
                return {
                    "accepted": False,
                    "error": (
                        "Issuer instance does not match authenticated peer: "
                        f"{issuer_id} != {authenticated_instance_id}"
                    ),
                }
            issuer_id = authenticated_instance_id

        peer = self._topology.get_peer(issuer_id)
        if not peer:
            return {"accepted": False, "error": f"Unknown issuer: {issuer_id}"}
        if getattr(peer, "role", "") != "root":
            return {
                "accepted": False,
                "error": f"Insufficient authority: peer role={getattr(peer, 'role', '') or 'unknown'}",
            }

        if self._local_pause_handler is None:
            return {
                "accepted": False,
                "error": "Local pause engine not configured",
            }

        try:
            try:
                pause_result = self._local_pause_handler(reason, full_stop=full_stop)
            except TypeError:
                pause_result = self._local_pause_handler(reason)
        except Exception as exc:
            logger.error("Local pause execution failed: %s", exc)
            return {
                "accepted": False,
                "error": f"Local pause failed: {exc}",
            }

        if isinstance(pause_result, dict):
            paused_agents = int(pause_result.get("paused_agents", 0) or 0)
            already_paused_agents = int(pause_result.get("already_paused_agents", 0) or 0)
            execution_state = str(
                pause_result.get("execution_state")
                or ("paused" if (paused_agents or already_paused_agents) else "idle")
            )
        else:
            paused_agents = int(pause_result or 0)
            already_paused_agents = 0
            execution_state = "paused" if paused_agents else "idle"

        logger.info(
            "Pause signal executed from %s: %s (paused=%d, already_paused=%d)",
            issuer_id,
            reason,
            paused_agents,
            already_paused_agents,
        )

        return {
            "accepted": True,
            "instance_id": self._identity.instance_id,
            "reason": reason,
            "full_stop": full_stop,
            "paused_agents": paused_agents,
            "already_paused_agents": already_paused_agents,
            "execution_state": execution_state,
        }

    def handle_resume(
        self,
        request_data: dict,
        authenticated_instance_id: Optional[str] = None,
    ) -> dict:
        """Handle an incoming resume signal from a peer."""
        issuer_id = request_data.get("issuer_instance_id", "")
        reason = request_data.get("reason", "")
        if authenticated_instance_id:
            if issuer_id and issuer_id != authenticated_instance_id:
                return {
                    "accepted": False,
                    "error": (
                        "Issuer instance does not match authenticated peer: "
                        f"{issuer_id} != {authenticated_instance_id}"
                    ),
                }
            issuer_id = authenticated_instance_id

        peer = self._topology.get_peer(issuer_id)
        if not peer:
            return {"accepted": False, "error": f"Unknown issuer: {issuer_id}"}
        if getattr(peer, "role", "") != "root":
            return {
                "accepted": False,
                "error": f"Insufficient authority: peer role={getattr(peer, 'role', '') or 'unknown'}",
            }

        if self._local_resume_handler is None:
            return {
                "accepted": False,
                "error": "Local resume engine not configured",
            }

        try:
            resume_result = self._local_resume_handler(reason)
        except Exception as exc:
            logger.error("Local resume execution failed: %s", exc)
            return {
                "accepted": False,
                "error": f"Local resume failed: {exc}",
            }

        if isinstance(resume_result, dict):
            resumed_agents = int(resume_result.get("resumed_agents", 0) or 0)
            execution_state = str(
                resume_result.get("execution_state")
                or ("running" if resumed_agents else "idle")
            )
        else:
            resumed_agents = int(resume_result or 0)
            execution_state = "running" if resumed_agents else "idle"

        logger.info(
            "Resume signal executed from %s: %s (resumed=%d)",
            issuer_id,
            reason,
            resumed_agents,
        )

        return {
            "accepted": True,
            "instance_id": self._identity.instance_id,
            "reason": reason,
            "resumed_agents": resumed_agents,
            "execution_state": execution_state,
        }

    async def _broadcast_kill_results(self, command_data: dict):
        target_ids = command_data.get("target_instance_ids", [])

        if target_ids:
            peers = [
                p for p in self._topology.list_peers()
                if p.instance_id in target_ids
            ]
        else:
            peers = self._topology.list_peers()

        if not peers:
            logger.info("No peers to propagate kill command to")
            return {}

        peer_dicts = [
            {"instance_id": p.instance_id, "address": p.address}
            for p in peers
        ]

        payload = {
            "command": command_data,
            "issuer_instance_id": self._identity.instance_id,
            "issuer_fingerprint": self._identity.fingerprint,
        }

        return await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/killswitch",
            body=payload,
            timeout_override_s=self._command_timeout_s,
        )
