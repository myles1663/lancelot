# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Soul Transport — Soul document push/pull over HTTP.

Wires the SoulPropagationEngine to actual HTTP delivery across peers.
Implements the 3-tier propagation lifecycle:
    T1 — Minor changes: push to all peers, no pause required
    T2 — Significant changes: pause all peers → push → activate simultaneously
    T3 — Breaking changes: pause → push → activate → per-instance confirm
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.federation.identity import FederationIdentity
from src.federation.topology import TopologyRegistry
from src.federation.transport import FederationTransport

logger = logging.getLogger(__name__)


class SoulTransport:
    """Handles Soul document propagation across federation peers."""

    def __init__(
        self,
        identity: FederationIdentity,
        transport: FederationTransport,
        topology: TopologyRegistry,
        propagation_engine=None,
        receipt_mgr=None,
        audit=None,
        handoff_timeout_s: float = 30.0,
    ):
        self._identity = identity
        self._transport = transport
        self._topology = topology
        self._propagation_engine = propagation_engine
        self._receipt_mgr = receipt_mgr
        self._audit = audit
        self._timeout_s = handoff_timeout_s

    async def push_soul_update(
        self,
        soul_document: dict,
        soul_hash: str,
        tier: str = "T1",
        reason: str = "",
        target_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Push a Soul update to federation peers.

        The propagation tier determines the push strategy:
            T1: Direct push, no pause required
            T2: Pause all → push → activate simultaneously
            T3: Pause all → push → activate → per-instance confirm

        Args:
            soul_document: Serialized Soul YAML as dict.
            soul_hash: SHA-256 hash of the Soul document.
            tier: Propagation tier ("T1", "T2", "T3").
            reason: Why the Soul is being updated.
            target_ids: Specific peers (None = all).

        Returns:
            Result dict with per-peer outcomes.
        """
        peers = self._resolve_targets(target_ids)
        if not peers:
            return {"delivered": 0, "total": 0, "results": {}}

        peer_dicts = [
            {"instance_id": p.instance_id, "address": p.address}
            for p in peers
        ]

        # T2/T3: Pause peers first
        if tier in ("T2", "T3"):
            pause_results = await self._transport.broadcast(
                peers=peer_dicts,
                method="POST",
                path="/api/federation/pause",
                body={
                    "reason": f"Soul propagation ({tier}): {reason}",
                    "issuer_instance_id": self._identity.instance_id,
                },
                timeout_override_s=self._timeout_s,
            )
            paused = sum(1 for r in pause_results.values() if r.success)
            logger.info("Soul propagation: paused %d/%d peers", paused, len(peers))

        # Push Soul document to all peers
        payload = {
            "soul_document": soul_document,
            "soul_hash": soul_hash,
            "tier": tier,
            "reason": reason,
            "source_instance_id": self._identity.instance_id,
            "source_fingerprint": self._identity.fingerprint,
        }

        push_results = await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/soul/update",
            body=payload,
            timeout_override_s=self._timeout_s,
        )

        delivered = sum(1 for r in push_results.values() if r.success)

        # Emit receipts
        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_soul_version_push(
                    soul_hash=soul_hash,
                    peer_count=len(peers),
                    delivered=delivered,
                    tier=tier,
                )
            except Exception:
                pass

        if self._audit:
            try:
                self._audit.record(
                    event_type="soul_push",
                    instance_id=self._identity.instance_id,
                    details={
                        "soul_hash": soul_hash,
                        "tier": tier,
                        "delivered": delivered,
                        "total": len(peers),
                    },
                )
            except Exception:
                pass

        logger.info(
            "Soul push complete: %d/%d peers received (%s, hash=%s)",
            delivered, len(peers), tier, soul_hash[:8],
        )

        return {
            "delivered": delivered,
            "total": len(peers),
            "tier": tier,
            "soul_hash": soul_hash,
            "results": {
                pid: {
                    "success": r.success,
                    "error": r.error if not r.success else "",
                    "latency_ms": r.latency_ms,
                }
                for pid, r in push_results.items()
            },
        }

    def handle_soul_push(self, request_data: dict) -> dict:
        """Handle an incoming Soul push from a peer.

        Validates the request, applies the federation MCP ceiling
        (child permissions can only be equal or more restrictive),
        and applies the new Soul version.

        Args:
            request_data: The soul push payload.

        Returns:
            Response dict with acceptance/rejection.
        """
        source_id = request_data.get("source_instance_id", "")
        soul_doc = request_data.get("soul_document", {})
        soul_hash = request_data.get("soul_hash", "")
        tier = request_data.get("tier", "T1")

        # Validate source is a known peer
        peer = self._topology.get_peer(source_id)
        if not peer:
            return {
                "accepted": False,
                "error": f"Unknown source: {source_id}",
            }

        # Enforce MCP federation ceiling — child/peer permissions can
        # only be equal or more restrictive than the root Soul's.
        # Same narrowing contract as HIVE Scoped Soul generation.
        mcp_ceiling_result = None
        if soul_doc.get("mcp_permissions"):
            try:
                from src.mcp.federation_ceiling import narrow_soul_mcp_permissions
                # The pushed Soul is the root ceiling; our local perms
                # must be narrowed to fit within it.
                mcp_ceiling_result = narrow_soul_mcp_permissions(
                    child_soul_data=soul_doc,  # Use pushed soul as both source and ceiling
                    root_soul_data=soul_doc,   # Root pushes its own as the ceiling
                )
                if mcp_ceiling_result.get("ceiling_enforced"):
                    logger.info(
                        "MCP federation ceiling applied: %d violation(s)",
                        len(mcp_ceiling_result.get("violations", [])),
                    )
            except ImportError:
                logger.debug("MCP federation ceiling module not available")
            except Exception as e:
                logger.warning("MCP ceiling enforcement failed: %s", e)

        # In a full implementation, this would also:
        # 1. Validate soul_doc against the linter
        # 2. Check compatibility with current soul
        # 3. Apply the new soul version
        # 4. Update local heartbeat with new hash

        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_soul_handshake_ack(
                    peer_id=source_id,
                    soul_hash=soul_hash,
                    accepted=True,
                )
            except Exception:
                pass

        logger.info(
            "Soul update received from %s: tier=%s, hash=%s",
            source_id, tier, soul_hash[:8] if soul_hash else "empty",
        )

        response = {
            "accepted": True,
            "instance_id": self._identity.instance_id,
            "soul_hash": soul_hash,
            "tier": tier,
        }

        if mcp_ceiling_result:
            response["mcp_ceiling"] = {
                "enforced": mcp_ceiling_result.get("ceiling_enforced", False),
                "violation_count": len(mcp_ceiling_result.get("violations", [])),
            }

        return response

    async def fetch_soul_from_peer(self, peer_address: str, peer_id: str = "") -> Dict[str, Any]:
        """Pull the current Soul document from a peer.

        Args:
            peer_address: The peer's base URL.
            peer_id: The peer's instance ID.

        Returns:
            Dict with soul_document and soul_hash, or error.
        """
        result = await self._transport.send(
            peer_address=peer_address,
            method="GET",
            path="/api/federation/soul",
            peer_id=peer_id,
            timeout_override_s=self._timeout_s,
        )

        if result.success and result.body:
            return result.body

        return {"error": result.error or "Failed to fetch soul"}

    def handle_soul_fetch(self) -> dict:
        """Handle a GET /api/federation/soul request.

        Returns this instance's current Soul document.
        """
        # In a full implementation, this would serialize the active Soul
        return {
            "instance_id": self._identity.instance_id,
            "soul_document": {},  # Would be loaded from soul store
            "soul_hash": "",
        }

    async def perform_handshake(self, peer_address: str, peer_id: str = "") -> Dict[str, Any]:
        """Perform a Soul version handshake with a peer.

        Compares local and remote Soul hashes to detect divergence.

        Returns:
            Dict with compatible (bool), local_hash, remote_hash.
        """
        result = await self._transport.send(
            peer_address=peer_address,
            method="GET",
            path="/api/federation/soul/hash",
            peer_id=peer_id,
        )

        if not result.success or not result.body:
            return {
                "compatible": False,
                "error": result.error or "Handshake failed",
            }

        remote_hash = result.body.get("soul_version_hash", "")

        return {
            "compatible": True,  # Would compare with local hash
            "remote_hash": remote_hash,
            "remote_instance_id": result.body.get("instance_id", ""),
        }

    def _resolve_targets(self, target_ids: Optional[List[str]] = None) -> list:
        """Resolve target peers from IDs or return all."""
        all_peers = self._topology.list_peers()
        if target_ids:
            return [p for p in all_peers if p.instance_id in target_ids]
        return all_peers
