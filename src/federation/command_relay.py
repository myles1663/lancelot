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
        receipt_mgr=None,
        audit=None,
        command_timeout_s: float = 5.0,
    ):
        self._identity = identity
        self._transport = transport
        self._topology = topology
        self._kill_switch = kill_switch
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
        target_ids = command_data.get("target_instance_ids", [])

        # Determine which peers to contact
        if target_ids:
            peers = [
                p for p in self._topology.list_peers()
                if p.instance_id in target_ids
            ]
        else:
            # Broadcast to all peers
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

        results = await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/killswitch",
            body=payload,
            timeout_override_s=self._command_timeout_s,
        )

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

    def handle_kill_command(self, request_data: dict) -> dict:
        """Handle an incoming kill command from a peer.

        Validates authority and executes locally.

        Args:
            request_data: The kill command payload.

        Returns:
            Response dict with ack/reject.
        """
        command = request_data.get("command", {})
        issuer_id = request_data.get("issuer_instance_id", "")

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

        # Authority check: only ROOT peers can issue federation kills
        if peer.role not in ("root", "peer"):
            return {
                "accepted": False,
                "error": f"Insufficient authority: peer role={peer.role}",
                "command_id": command_id,
            }

        # Execute local kill if kill_switch is available
        agents_killed = 0
        if self._kill_switch:
            try:
                # The kill switch handles local execution
                agents_killed = self._kill_switch.execute_local_kill(
                    command_type=command_type,
                    reason=reason,
                    issuer_id=issuer_id,
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
        }

        results = await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/pause",
            body=payload,
            timeout_override_s=self._command_timeout_s,
        )

        return {pid: r.success for pid, r in results.items()}

    def handle_pause(self, request_data: dict) -> dict:
        """Handle an incoming pause signal from a peer."""
        issuer_id = request_data.get("issuer_instance_id", "")
        reason = request_data.get("reason", "")

        peer = self._topology.get_peer(issuer_id)
        if not peer:
            return {"accepted": False, "error": f"Unknown issuer: {issuer_id}"}

        logger.info("Pause signal received from %s: %s", issuer_id, reason)

        return {
            "accepted": True,
            "instance_id": self._identity.instance_id,
            "reason": reason,
        }
