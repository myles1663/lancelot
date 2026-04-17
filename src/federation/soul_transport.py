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
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.core.soul.linter import lint_or_raise
from src.core.soul.store import Soul, SoulStoreError, _resolve_soul_dir, get_active_version, set_active_version
from src.federation.identity import FederationIdentity
from src.federation.soul_compat import CompatibilityLevel, classify_compatibility, hash_soul
from src.federation.soul_handshake import (
    HandshakeState,
    check_timeouts,
    create_handshakes,
    evaluate_push_result,
    process_response,
)
from src.federation.soul_propagation import (
    InstancePropState,
    PropagationTier,
    SoulPropagationEngine,
)
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
        current_soul_provider=None,
        runtime_reload_callback=None,
        soul_dir: Optional[str] = None,
        heartbeat_emitter=None,
        local_pause_handler=None,
        local_resume_handler=None,
    ):
        self._identity = identity
        self._transport = transport
        self._topology = topology
        self._propagation_engine = propagation_engine
        self._receipt_mgr = receipt_mgr
        self._audit = audit
        self._timeout_s = handoff_timeout_s
        self._current_soul_provider = current_soul_provider
        self._runtime_reload_callback = runtime_reload_callback
        self._soul_dir = soul_dir
        self._heartbeat_emitter = heartbeat_emitter
        self._local_pause_handler = local_pause_handler
        self._local_resume_handler = local_resume_handler

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
            return {
                "delivered": 0,
                "total": 0,
                "results": {},
                "handshake": {
                    "new_soul_hash": soul_hash,
                    "total_peers": 0,
                    "acknowledged": 0,
                    "rejected": 0,
                    "timed_out": 0,
                    "denied": 0,
                    "all_acknowledged": False,
                    "governance_gaps": [],
                    "handshakes": [],
                },
            }

        current_soul = self._get_current_soul()
        current_hash = hash_soul(current_soul) if current_soul is not None else ""
        handshakes = create_handshakes(
            initiator_instance_id=self._identity.instance_id,
            target_instance_ids=[p.instance_id for p in peers],
            old_soul_hash=current_hash,
            new_soul_hash=soul_hash,
            timeout_s=self._timeout_s,
        )
        handshake_map = {hs.target_instance_id: hs for hs in handshakes}
        event = None
        if self._propagation_engine:
            try:
                self._propagation_engine.update_peers([p.instance_id for p in peers])
                event = self._propagation_engine.initiate_propagation(
                    event_id=f"soul-{uuid.uuid4().hex[:12]}",
                    tier=self._parse_tier(tier),
                    issuer_id=self._identity.instance_id,
                    reason=reason,
                    source_version=getattr(current_soul, "version", ""),
                    source_version_hash=current_hash,
                    target_version=str(soul_document.get("version", "")),
                    target_version_hash=soul_hash,
                    timeout_seconds=self._timeout_s,
                )
            except Exception as exc:
                logger.warning("Failed to start Soul propagation event: %s", exc)

        peer_dicts = [
            {"instance_id": p.instance_id, "address": p.address}
            for p in peers
        ]
        propagation_tier = self._parse_tier(tier)

        # T2/T3: Pause peers first
        if propagation_tier in (
            PropagationTier.T2_SIGNIFICANT,
            PropagationTier.T3_CRITICAL,
        ):
            if self._local_pause_handler is not None:
                try:
                    self._local_pause_handler(
                        f"Soul propagation ({tier}): {reason}",
                        full_stop=propagation_tier == PropagationTier.T3_CRITICAL,
                    )
                except TypeError:
                    self._local_pause_handler(f"Soul propagation ({tier}): {reason}")
            pause_results = await self._transport.broadcast(
                peers=peer_dicts,
                method="POST",
                path="/api/federation/pause",
                body={
                    "reason": f"Soul propagation ({tier}): {reason}",
                    "issuer_instance_id": self._identity.instance_id,
                    "full_stop": propagation_tier == PropagationTier.T3_CRITICAL,
                },
                timeout_override_s=self._timeout_s,
            )
            paused = sum(1 for r in pause_results.values() if r.success)
            logger.info("Soul propagation: paused %d/%d peers", paused, len(peers))
            pause_failed = any(not r.success for r in pause_results.values())
            if event and self._propagation_engine:
                self._propagation_engine.record_pause_ack(event.event_id, self._identity.instance_id)
                for pid, result in pause_results.items():
                    if result.success:
                        self._propagation_engine.record_pause_ack(event.event_id, pid)
                    else:
                        self._propagation_engine.record_rejection(
                            event.event_id,
                            pid,
                            result.error or "Pause failed",
                        )
                event = self._propagation_engine.get_event(event.event_id)
                if event and event.state.value == "paused":
                    self._propagation_engine.advance_to_activation(event.event_id)
                    event = self._propagation_engine.get_event(event.event_id)
            if pause_failed:
                for peer_id, handshake in handshake_map.items():
                    pause_result = pause_results.get(peer_id)
                    if pause_result is None:
                        process_response(
                            handshake,
                            HandshakeState.REJECTED,
                            reason="Required pause phase did not complete",
                        )
                        continue
                    if pause_result.success:
                        process_response(
                            handshake,
                            HandshakeState.REJECTED,
                            reason="Propagation aborted because one or more peers rejected the required pause phase",
                        )
                        continue

                    error_text = pause_result.error or "Pause failed"
                    lowered = error_text.lower()
                    handshake_state = HandshakeState.REJECTED
                    if "timeout" in lowered:
                        handshake_state = HandshakeState.TIMEOUT
                    elif "governance" in lowered or "approval" in lowered:
                        handshake_state = HandshakeState.GOVERNANCE_DENIAL
                    process_response(
                        handshake,
                        handshake_state,
                        reason=error_text,
                    )

                check_timeouts(handshakes)
                handshake_result = evaluate_push_result(handshakes, soul_hash)
                logger.warning(
                    "Soul propagation aborted before push because the required pause phase failed (%s, hash=%s)",
                    tier,
                    soul_hash[:8],
                )
                return {
                    "delivered": 0,
                    "total": len(peers),
                    "tier": tier,
                    "soul_hash": soul_hash,
                    "propagation": self._event_to_dict(event),
                    "handshake": handshake_result.to_dict(),
                    "resume": {},
                    "confirmation_required": False,
                    "confirmation_pending_instance_ids": [],
                    "results": {},
                }

        # Push Soul document to all peers
        payload = {
            "soul_document": soul_document,
            "soul_hash": soul_hash,
            "tier": tier,
            "event_id": event.event_id if event else "",
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

        propagation_tier = self._parse_tier(tier)
        delivered = sum(1 for r in push_results.values() if r.success)
        if event and self._propagation_engine:
            self._propagation_engine.record_activation(event.event_id, self._identity.instance_id)
            if propagation_tier == PropagationTier.T3_CRITICAL:
                # The root/local instance has already applied the Soul and is
                # treated as confirmed locally. Remote peers must still send
                # the explicit second-leg confirmation before resume.
                self._propagation_engine.record_confirmation(
                    event.event_id,
                    self._identity.instance_id,
                )
            for pid, result in push_results.items():
                if result.success:
                    self._propagation_engine.record_activation(event.event_id, pid)
                else:
                    self._propagation_engine.record_rejection(
                        event.event_id,
                        pid,
                        result.error or "Soul update delivery failed",
                    )
            event = self._propagation_engine.get_event(event.event_id)

        for peer_id, handshake in handshake_map.items():
            result_obj = push_results.get(peer_id)
            if result_obj is None:
                continue
            body = getattr(result_obj, "body", None) or {}
            if result_obj.success and body.get("accepted", True):
                process_response(
                    handshake,
                    HandshakeState.ACKNOWLEDGED,
                    peer_execution_state=body.get("execution_state"),
                )
                continue

            error_text = result_obj.error or body.get("error", "")
            handshake_state = HandshakeState.REJECTED
            lowered = error_text.lower()
            if "timeout" in lowered:
                handshake_state = HandshakeState.TIMEOUT
            elif "governance" in lowered or "approval" in lowered:
                handshake_state = HandshakeState.GOVERNANCE_DENIAL

            process_response(
                handshake,
                handshake_state,
                peer_execution_state=body.get("execution_state"),
                reason=error_text or "Soul propagation rejected",
            )

        check_timeouts(handshakes)
        handshake_result = evaluate_push_result(handshakes, soul_hash)

        resume_results: Dict[str, Dict[str, Any]] = {}
        if propagation_tier == PropagationTier.T2_SIGNIFICANT:
            if self._local_resume_handler is not None:
                self._local_resume_handler(
                    f"Soul propagation ({tier}) complete: {reason}"
                )
            raw_resume_results = await self._transport.broadcast(
                peers=peer_dicts,
                method="POST",
                path="/api/federation/resume",
                body={
                    "reason": f"Soul propagation ({tier}) complete: {reason}",
                    "issuer_instance_id": self._identity.instance_id,
                },
                timeout_override_s=self._timeout_s,
            )
            resume_results = {
                pid: {
                    "success": result.success,
                    "error": result.error if not result.success else "",
                    "latency_ms": result.latency_ms,
                }
                for pid, result in raw_resume_results.items()
            }
            if event and self._propagation_engine:
                for pid, result in raw_resume_results.items():
                    if not result.success:
                        self._propagation_engine.record_rejection(
                            event.event_id,
                            pid,
                            result.error or "Resume failed after Soul propagation",
                        )
                event = self._propagation_engine.get_event(event.event_id)

        # Emit receipts
        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_soul_version_push(
                    soul_hash=soul_hash,
                    target_instance_ids=[p.instance_id for p in peers],
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
                        "all_acknowledged": handshake_result.all_acknowledged,
                        "governance_gaps": handshake_result.governance_gaps,
                    },
                )
            except Exception:
                pass

        logger.info(
            "Soul push complete: %d/%d peers received (%s, hash=%s)",
            delivered, len(peers), tier, soul_hash[:8],
        )

        confirmation_pending_instance_ids: List[str] = []
        if event and propagation_tier == PropagationTier.T3_CRITICAL:
            confirmation_pending_instance_ids = [
                instance.instance_id
                for instance in event.instances
                if instance.instance_id != self._identity.instance_id
                and instance.state != InstancePropState.CONFIRMED
            ]

        return {
            "delivered": delivered,
            "total": len(peers),
            "tier": tier,
            "soul_hash": soul_hash,
            "propagation": self._event_to_dict(event),
            "handshake": handshake_result.to_dict(),
            "resume": resume_results,
            "confirmation_required": propagation_tier == PropagationTier.T3_CRITICAL,
            "confirmation_pending_instance_ids": confirmation_pending_instance_ids,
            "results": {
                pid: {
                    "success": r.success,
                    "error": r.error if not r.success else "",
                    "latency_ms": r.latency_ms,
                }
                for pid, r in push_results.items()
            },
        }

    def handle_soul_push(
        self,
        request_data: dict,
        authenticated_instance_id: Optional[str] = None,
    ) -> dict:
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
        event_id = str(request_data.get("event_id", "")).strip()
        if authenticated_instance_id:
            if source_id and source_id != authenticated_instance_id:
                return {
                    "accepted": False,
                    "error": (
                        "Source instance does not match authenticated peer: "
                        f"{source_id} != {authenticated_instance_id}"
                    ),
                }
            source_id = authenticated_instance_id
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
        if getattr(peer, "role", "") != "root":
            return {
                "accepted": False,
                "error": (
                    "Soul updates are only accepted from root-authority peers; "
                    f"received role={getattr(peer, 'role', '') or 'unknown'}"
                ),
            }

        current_soul = self._get_current_soul()

        if not isinstance(soul_doc, dict) or not soul_doc:
            return {
                "accepted": False,
                "error": "Soul push payload must include a non-empty soul_document mapping",
            }

        try:
            incoming_soul = Soul(**soul_doc)
            lint_or_raise(incoming_soul)
        except (SoulStoreError, Exception) as exc:
            return {
                "accepted": False,
                "error": f"Invalid soul document: {exc}",
            }

        computed_hash = hash_soul(incoming_soul)
        if soul_hash and soul_hash != computed_hash:
            return {
                "accepted": False,
                "error": f"Soul hash mismatch: provided={soul_hash}, computed={computed_hash}",
            }
        soul_hash = computed_hash

        # Enforce MCP federation ceiling against the receiver's current
        # runtime Soul, not against the pushed document itself.
        mcp_ceiling_result = None
        if soul_doc.get("mcp_permissions") and current_soul is not None:
            try:
                from src.mcp.federation_ceiling import narrow_soul_mcp_permissions
                narrowed = narrow_soul_mcp_permissions(
                    child_soul_data=soul_doc,
                    root_soul_data=current_soul.model_dump(),
                )
                mcp_ceiling_result = narrowed
                soul_doc = dict(soul_doc)
                soul_doc["mcp_permissions"] = narrowed.get("mcp_permissions", soul_doc.get("mcp_permissions", []))
                if narrowed.get("ceiling_enforced"):
                    logger.info(
                        "MCP federation ceiling applied: %d violation(s)",
                        len(narrowed.get("violations", [])),
                    )
            except ImportError:
                logger.debug("MCP federation ceiling module not available")
            except Exception as e:
                logger.warning("MCP ceiling enforcement failed: %s", e)

        try:
            candidate_soul = Soul(**soul_doc)
            lint_or_raise(candidate_soul)
        except (SoulStoreError, Exception) as exc:
            return {
                "accepted": False,
                "error": f"Invalid narrowed soul document: {exc}",
            }
        soul_hash = hash_soul(candidate_soul)

        compatibility_level = CompatibilityLevel.GREEN
        compatibility_notes: List[str] = []
        if current_soul is not None:
            compatibility_level, compatibility_notes = classify_compatibility(
                current_soul,
                candidate_soul,
            )

        try:
            self._persist_and_apply_soul(candidate_soul, soul_doc)
        except Exception as exc:
            if self._receipt_mgr:
                try:
                    self._receipt_mgr.record_soul_handshake_ack(
                        parent_instance_id=source_id,
                        soul_version_hash=soul_hash,
                        compatible=False,
                    )
                except Exception:
                    pass
            return {
                "accepted": False,
                "error": f"Failed to apply Soul update: {exc}",
                "compatibility_level": compatibility_level,
                "compatibility_notes": compatibility_notes,
            }

        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_soul_handshake_ack(
                    parent_instance_id=source_id,
                    soul_version_hash=soul_hash,
                    compatible=True,
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
            "event_id": event_id,
            "confirmation_required": str(tier).upper() == "T3",
            "compatibility_level": compatibility_level,
            "compatibility_notes": compatibility_notes,
        }

        if mcp_ceiling_result:
            response["mcp_ceiling"] = {
                "enforced": mcp_ceiling_result.get("ceiling_enforced", False),
                "violation_count": len(mcp_ceiling_result.get("violations", [])),
            }

        if str(tier).upper() == "T3" and event_id and self._transport is not None:
            source_peer = self._topology.get_peer(source_id)
            if source_peer and getattr(source_peer, "address", ""):
                self._dispatch_t3_confirmation(
                    source_peer.address,
                    source_id,
                    event_id,
                )
            else:
                response["confirmation_error"] = (
                    f"Unable to resolve source peer address for confirmation: {source_id}"
                )

        return response

    async def handle_soul_confirmation(
        self,
        request_data: dict,
        authenticated_instance_id: Optional[str] = None,
    ) -> dict:
        """Record a T3 propagation confirmation and resume peers when complete."""
        if not self._propagation_engine:
            return {
                "accepted": False,
                "error": "Soul propagation engine not configured",
            }

        event_id = str(request_data.get("event_id", "")).strip()
        if not event_id:
            return {
                "accepted": False,
                "error": "Missing required field: event_id",
            }

        confirmer_id = str(request_data.get("instance_id", "")).strip()
        if authenticated_instance_id:
            if confirmer_id and confirmer_id != authenticated_instance_id:
                return {
                    "accepted": False,
                    "error": (
                        "Confirmation instance does not match authenticated peer: "
                        f"{confirmer_id} != {authenticated_instance_id}"
                    ),
                }
            confirmer_id = authenticated_instance_id

        if not confirmer_id:
            return {
                "accepted": False,
                "error": "Missing required field: instance_id",
            }

        event = self._propagation_engine.get_event(event_id)
        if event is None:
            return {
                "accepted": False,
                "error": f"Unknown propagation event: {event_id}",
            }
        if event.tier != PropagationTier.T3_CRITICAL:
            return {
                "accepted": False,
                "error": "Soul confirmation is only valid for T3 propagation events",
            }

        if not self._propagation_engine.record_confirmation(event_id, confirmer_id):
            return {
                "accepted": False,
                "error": f"Unable to record confirmation for {confirmer_id}",
            }

        event = self._propagation_engine.get_event(event_id)
        if event is None:
            return {
                "accepted": False,
                "error": f"Propagation event disappeared: {event_id}",
            }

        all_confirmed = all(
            instance.state == InstancePropState.CONFIRMED
            for instance in event.instances
        )
        resume_results: Dict[str, Dict[str, Any]] = {}
        if all_confirmed:
            resume_failed = False
            if self._local_resume_handler is not None:
                self._local_resume_handler(
                    f"Soul propagation ({event.tier.value}) confirmed: {event.reason}"
                )
            target_ids = [
                instance.instance_id
                for instance in event.instances
                if instance.instance_id != self._identity.instance_id
            ]
            peers = self._resolve_targets(target_ids)
            peer_dicts = [
                {"instance_id": p.instance_id, "address": p.address}
                for p in peers
            ]
            if peer_dicts:
                raw_resume_results = await self._transport.broadcast(
                    peers=peer_dicts,
                    method="POST",
                    path="/api/federation/resume",
                    body={
                        "reason": (
                            f"Soul propagation ({event.tier.value}) confirmed: {event.reason}"
                        ),
                        "issuer_instance_id": self._identity.instance_id,
                    },
                    timeout_override_s=self._timeout_s,
                )
                resume_results = {
                    pid: {
                        "success": result.success,
                        "error": result.error if not result.success else "",
                        "latency_ms": result.latency_ms,
                    }
                    for pid, result in raw_resume_results.items()
                }
                for pid, result in raw_resume_results.items():
                    if not result.success:
                        resume_failed = True
                        self._propagation_engine.record_rejection(
                            event.event_id,
                            pid,
                            result.error or "Resume failed after T3 confirmation",
                        )

            if not resume_failed:
                self._propagation_engine.complete_confirmed_event(event.event_id)
            event = self._propagation_engine.get_event(event.event_id) or event

        return {
            "accepted": True,
            "event_id": event_id,
            "instance_id": confirmer_id,
            "all_confirmed": all_confirmed,
            "propagation": self._event_to_dict(event),
            "resume": resume_results,
        }

    def _dispatch_t3_confirmation(
        self,
        source_peer_address: str,
        source_instance_id: str,
        event_id: str,
    ) -> None:
        import asyncio

        async def _send_confirmation() -> None:
            result = await self._transport.send(
                peer_address=source_peer_address,
                method="POST",
                path="/api/federation/soul/confirm",
                peer_id=source_instance_id,
                body={
                    "event_id": event_id,
                    "instance_id": self._identity.instance_id,
                },
                timeout_override_s=self._timeout_s,
            )
            if not result.success:
                logger.warning(
                    "T3 soul confirmation failed for %s via %s: %s",
                    source_instance_id,
                    event_id,
                    result.error or f"HTTP {result.status_code}",
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_send_confirmation())
            return

        loop.create_task(_send_confirmation())

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
        current_soul = self._get_current_soul()
        if current_soul is None:
            return {
                "instance_id": self._identity.instance_id,
                "soul_document": {},
                "soul_hash": "",
                "error": "No active runtime Soul available",
            }

        return {
            "instance_id": self._identity.instance_id,
            "soul_document": current_soul.model_dump(),
            "soul_hash": hash_soul(current_soul),
        }

    async def perform_handshake(self, peer_address: str, peer_id: str = "") -> Dict[str, Any]:
        """Perform a Soul version handshake with a peer.

        Compares local and remote Soul hashes to detect divergence.

        Returns:
            Dict with compatible (bool), local_hash, remote_hash.
        """
        local_soul = self._get_current_soul()
        if local_soul is None:
            return {
                "compatible": False,
                "error": "No local runtime Soul available",
            }

        result = await self._transport.send(
            peer_address=peer_address,
            method="POST",
            path="/api/federation/soul/handshake",
            peer_id=peer_id,
            body={
                "remote_instance_id": self._identity.instance_id,
                "remote_soul_hash": hash_soul(local_soul),
                "remote_soul_document": local_soul.model_dump(),
            },
        )

        if not result.success or not result.body:
            return {
                "compatible": False,
                "error": result.error or "Handshake failed",
            }
        return result.body

    def handle_handshake(self, request_data: dict) -> dict:
        """Evaluate a remote Soul handshake payload against the live local Soul."""
        local_soul = self._get_current_soul()
        if local_soul is None:
            return {
                "compatible": False,
                "error": "No local runtime Soul available",
                "instance_id": self._identity.instance_id,
                "soul_version_hash": "",
            }

        local_hash = hash_soul(local_soul)
        remote_hash = request_data.get("remote_soul_hash", "")
        remote_doc = request_data.get("remote_soul_document", {})
        remote_instance_id = request_data.get("remote_instance_id", "")

        if not remote_doc:
            return {
                "compatible": False,
                "error": "Handshake payload missing remote_soul_document",
                "instance_id": self._identity.instance_id,
                "soul_version_hash": local_hash,
                "local_hash": local_hash,
                "remote_hash": remote_hash,
                "remote_instance_id": remote_instance_id,
            }

        try:
            remote_soul = Soul(**remote_doc)
            lint_or_raise(remote_soul)
        except Exception as exc:
            return {
                "compatible": False,
                "error": f"Invalid remote Soul document: {exc}",
                "instance_id": self._identity.instance_id,
                "soul_version_hash": local_hash,
                "local_hash": local_hash,
                "remote_hash": remote_hash,
                "remote_instance_id": remote_instance_id,
            }

        computed_remote_hash = hash_soul(remote_soul)
        if remote_hash and remote_hash != computed_remote_hash:
            return {
                "compatible": False,
                "error": "Remote Soul hash mismatch",
                "instance_id": self._identity.instance_id,
                "soul_version_hash": local_hash,
                "local_hash": local_hash,
                "remote_hash": remote_hash,
                "computed_remote_hash": computed_remote_hash,
                "remote_instance_id": remote_instance_id,
            }

        compatibility_level, notes = classify_compatibility(local_soul, remote_soul)
        return {
            "compatible": compatibility_level != CompatibilityLevel.RED,
            "compatibility_level": compatibility_level,
            "notes": notes,
            "instance_id": self._identity.instance_id,
            "soul_version_hash": local_hash,
            "local_hash": local_hash,
            "remote_hash": computed_remote_hash,
            "remote_instance_id": remote_instance_id,
        }

    def get_local_soul_hash(self) -> str:
        """Return the current local Soul hash used for federation transport."""
        current_soul = self._get_current_soul()
        return hash_soul(current_soul) if current_soul is not None else ""

    def get_consistency_state(self) -> str:
        """Return the live propagation consistency state."""
        if not self._propagation_engine:
            return "synchronized"
        return self._propagation_engine.consistency_state.value

    def get_active_propagations(self) -> List[Dict[str, Any]]:
        """Return currently active Soul propagation events."""
        if not self._propagation_engine:
            return []
        self._finalize_stale_propagations()
        return [event.to_dict() for event in self._propagation_engine.get_active_events()]

    def _resolve_targets(self, target_ids: Optional[List[str]] = None) -> list:
        """Resolve target peers from IDs or return all."""
        all_peers = self._topology.list_peers()
        if target_ids:
            return [p for p in all_peers if p.instance_id in target_ids]
        return all_peers

    def _parse_tier(self, tier: str) -> PropagationTier:
        mapping = {
            "T1": PropagationTier.T1_MINOR,
            "T2": PropagationTier.T2_SIGNIFICANT,
            "T3": PropagationTier.T3_CRITICAL,
        }
        return mapping.get(str(tier).upper(), PropagationTier.T1_MINOR)

    def _event_to_dict(self, event) -> Dict[str, Any]:
        if event is None:
            return {}
        try:
            return event.to_dict()
        except Exception:
            return {}

    def _finalize_stale_propagations(self) -> None:
        if not self._propagation_engine:
            return
        try:
            active_events = list(self._propagation_engine.get_active_events())
        except Exception:
            return

        import time as _time

        now = _time.time()
        for event in active_events:
            try:
                initiated_ts = event.initiated_at
                if not initiated_ts:
                    continue
                started = SoulTransport._parse_timestamp(initiated_ts)
                if started is None:
                    continue
                if now - started < float(event.timeout_seconds or self._timeout_s):
                    continue
                for instance in event.instances:
                    if instance.state in {
                        InstancePropState.PENDING,
                        InstancePropState.PAUSED,
                        InstancePropState.ACTIVATED,
                    }:
                        self._propagation_engine.record_rejection(
                            event.event_id,
                            instance.instance_id,
                            "Soul propagation timed out waiting for peer acknowledgement",
                        )
                        break
            except Exception:
                continue

    @staticmethod
    def _parse_timestamp(value: str) -> Optional[float]:
        from datetime import datetime
        try:
            return datetime.fromisoformat(value).timestamp()
        except Exception:
            return None

    def _get_current_soul(self) -> Optional[Soul]:
        """Resolve the current live Soul from runtime or store."""
        if callable(self._current_soul_provider):
            soul = self._current_soul_provider()
            if soul is not None:
                return soul

        try:
            from src.core.soul.layers import load_active_soul_with_overlays
            return load_active_soul_with_overlays(self._soul_dir)
        except Exception:
            return None

    def _persist_and_apply_soul(self, soul: Soul, soul_document: dict) -> None:
        """Persist an incoming Soul version and refresh the live runtime."""
        if self._runtime_reload_callback is None and not self._soul_dir:
            raise RuntimeError("Soul runtime reload callback or soul_dir is required")

        soul_dir = _resolve_soul_dir(self._soul_dir)
        versions_dir = Path(soul_dir) / "soul_versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        version_path = versions_dir / f"soul_{soul.version}.yaml"

        previous_version = None
        previous_text = None
        had_existing_file = version_path.exists()
        if had_existing_file:
            previous_text = version_path.read_text(encoding="utf-8")

        try:
            previous_version = get_active_version(str(soul_dir))
        except Exception:
            previous_version = None

        version_path.write_text(
            yaml.safe_dump(soul_document, sort_keys=False),
            encoding="utf-8",
        )
        set_active_version(soul.version, str(soul_dir))

        try:
            if self._runtime_reload_callback is not None:
                self._runtime_reload_callback(soul)
        except Exception:
            if previous_version is not None:
                set_active_version(previous_version, str(soul_dir))
            if had_existing_file and previous_text is not None:
                version_path.write_text(previous_text, encoding="utf-8")
            elif not had_existing_file and version_path.exists():
                version_path.unlink()
            raise

        if self._heartbeat_emitter is not None:
            try:
                self._heartbeat_emitter.emit_once()
            except Exception as exc:
                logger.warning("Heartbeat refresh after Soul update failed: %s", exc)
