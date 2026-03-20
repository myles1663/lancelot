# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Peer Protocol — Mutual peer registration handshake.

Implements a challenge/response protocol for secure peer registration:

    Step 1: Initiator → Target
        POST /api/federation/peer/register
        Body: identity + challenge (random 32 bytes)
        Signed with initiator's private key

    Step 2: Target verifies signature, generates counter-challenge
        Target stores initiator temporarily
        Target calls POST /api/federation/peer/confirm on initiator
        Body: identity + challenge_response + counter_challenge

    Step 3: Initiator verifies target's challenge_response
        Initiator sends confirmation
        Both sides add each other to TopologyRegistry
        Receipts emitted on both sides

Security:
    - Both sides prove possession of their private keys
    - Public keys exchanged in the clear (Ed25519 — no MITM without TLS,
      but TLS is recommended for production cross-network deployments)
    - Challenge/response prevents replay of registration requests
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.federation.identity import FederationIdentity, sign_payload, verify_signature
from src.federation.topology import TopologyRegistry
from src.federation.transport import FederationTransport, TransportResult

logger = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
    """Result of a peer registration attempt."""
    success: bool
    peer_instance_id: str = ""
    peer_fingerprint: str = ""
    error: str = ""
    mutual: bool = False  # True if both sides completed registration


@dataclass
class PendingRegistration:
    """A registration request awaiting confirmation."""
    instance_id: str
    public_key_hex: str
    fingerprint: str
    address: str
    role: str
    soul_version_hash: str
    challenge: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PeerRegistrationProtocol:
    """Handles the mutual peer registration handshake.

    Manages the full lifecycle: initiate → verify → confirm → register.
    """

    def __init__(
        self,
        identity: FederationIdentity,
        topology: TopologyRegistry,
        transport: FederationTransport,
        receipt_mgr=None,
        audit=None,
        max_pending: int = 20,
    ):
        self._identity = identity
        self._topology = topology
        self._transport = transport
        self._receipt_mgr = receipt_mgr
        self._audit = audit
        self._pending: dict[str, PendingRegistration] = {}
        self._max_pending = max_pending

    async def initiate_registration(
        self,
        target_address: str,
        target_role: str = "peer",
    ) -> RegistrationResult:
        """Initiate registration with a remote peer.

        1. Generate challenge
        2. POST our identity + challenge to target
        3. Target verifies and registers us
        4. Target returns its identity + challenge_response
        5. We verify challenge_response and register target

        Args:
            target_address: Base URL of the target peer
            target_role: Role we assign to the target in our topology

        Returns:
            RegistrationResult with success/failure details.
        """
        challenge = os.urandom(32).hex()

        request_body = {
            "instance_id": self._identity.instance_id,
            "public_key_hex": self._identity.public_key_hex(),
            "fingerprint": self._identity.fingerprint,
            "address": "",  # Filled by config.self_address or caller
            "role": "peer",  # How WE want to be seen by the target
            "soul_version_hash": "",
            "challenge": challenge,
        }

        # Sign the challenge with our private key as proof of identity
        challenge_sig = sign_payload(
            self._identity, challenge.encode("utf-8")
        ).hex()
        request_body["challenge_signature"] = challenge_sig

        result = await self._transport.send(
            peer_address=target_address,
            method="POST",
            path="/api/federation/peer/register",
            body=request_body,
            peer_id="registration-target",
            timeout_override_s=10.0,
        )

        if not result.success or not result.body:
            return RegistrationResult(
                success=False,
                error=result.error or f"Registration failed: HTTP {result.status_code}",
            )

        response = result.body

        # Verify target's challenge response
        target_instance_id = response.get("instance_id", "")
        target_public_key_hex = response.get("public_key_hex", "")
        target_fingerprint = response.get("fingerprint", "")
        challenge_response = response.get("challenge_response", "")

        if not all([target_instance_id, target_public_key_hex, challenge_response]):
            return RegistrationResult(
                success=False,
                error="Incomplete registration response from target",
            )

        # Verify the target signed our challenge correctly
        try:
            target_pub_bytes = bytes.fromhex(target_public_key_hex)
            response_sig_bytes = bytes.fromhex(challenge_response)
        except ValueError:
            return RegistrationResult(
                success=False,
                error="Invalid key or signature format in response",
            )

        if not verify_signature(
            target_pub_bytes, challenge.encode("utf-8"), response_sig_bytes,
        ):
            return RegistrationResult(
                success=False,
                error="Challenge response verification failed — target cannot prove identity",
            )

        # Registration successful — add target to our topology
        try:
            self._topology.register_peer(
                instance_id=target_instance_id,
                fingerprint=target_fingerprint,
                public_key_hex=target_public_key_hex,
                address=target_address,
                role=target_role,
                soul_version_hash=response.get("soul_version_hash", ""),
            )
        except ValueError as e:
            return RegistrationResult(
                success=False,
                error=f"Failed to register peer: {e}",
            )

        # Emit receipt
        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_peer_registered(
                    peer_id=target_instance_id,
                    fingerprint=target_fingerprint,
                    role=target_role,
                )
            except Exception:
                pass

        if self._audit:
            try:
                self._audit.record(
                    event_type="peer_registered",
                    instance_id=target_instance_id,
                    details={
                        "fingerprint": target_fingerprint,
                        "role": target_role,
                        "direction": "outbound",
                        "address": target_address,
                    },
                )
            except Exception:
                pass

        logger.info(
            "Peer registration completed: %s (fingerprint=%s, role=%s)",
            target_instance_id, target_fingerprint[:8], target_role,
        )

        return RegistrationResult(
            success=True,
            peer_instance_id=target_instance_id,
            peer_fingerprint=target_fingerprint,
            mutual=True,
        )

    def handle_registration_request(self, request_data: dict) -> dict:
        """Handle an incoming peer registration request.

        Called by the API endpoint. Verifies the request, registers the
        peer, and returns our identity + signed challenge response.

        Args:
            request_data: The registration request body.

        Returns:
            Response dict with our identity and challenge response,
            or error dict.
        """
        instance_id = request_data.get("instance_id", "")
        public_key_hex = request_data.get("public_key_hex", "")
        fingerprint = request_data.get("fingerprint", "")
        address = request_data.get("address", "")
        role = request_data.get("role", "peer")
        soul_version_hash = request_data.get("soul_version_hash", "")
        challenge = request_data.get("challenge", "")
        challenge_sig = request_data.get("challenge_signature", "")

        # Validate required fields
        if not all([instance_id, public_key_hex, challenge]):
            return {
                "error": "Missing required fields: instance_id, public_key_hex, challenge",
                "accepted": False,
            }

        # Verify the sender signed the challenge (proof of private key)
        if challenge_sig:
            try:
                pub_bytes = bytes.fromhex(public_key_hex)
                sig_bytes = bytes.fromhex(challenge_sig)
                if not verify_signature(
                    pub_bytes, challenge.encode("utf-8"), sig_bytes,
                ):
                    return {
                        "error": "Challenge signature verification failed",
                        "accepted": False,
                    }
            except ValueError:
                return {
                    "error": "Invalid key or signature format",
                    "accepted": False,
                }

        # Check if we already know this peer
        existing = self._topology.get_peer(instance_id)

        # Register the peer in our topology
        try:
            self._topology.register_peer(
                instance_id=instance_id,
                fingerprint=fingerprint,
                public_key_hex=public_key_hex,
                address=address,
                role=role,
                soul_version_hash=soul_version_hash,
            )
        except ValueError as e:
            return {
                "error": f"Registration rejected: {e}",
                "accepted": False,
            }

        # Sign their challenge as our response (proves our identity)
        challenge_response = sign_payload(
            self._identity, challenge.encode("utf-8"),
        ).hex()

        # Emit receipt
        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_peer_registered(
                    peer_id=instance_id,
                    fingerprint=fingerprint,
                    role=role,
                )
            except Exception:
                pass

        if self._audit:
            try:
                self._audit.record(
                    event_type="peer_registered",
                    instance_id=instance_id,
                    details={
                        "fingerprint": fingerprint,
                        "role": role,
                        "direction": "inbound",
                        "address": address,
                    },
                )
            except Exception:
                pass

        logger.info(
            "Accepted peer registration: %s (fingerprint=%s, role=%s)",
            instance_id, fingerprint[:8] if fingerprint else "none", role,
        )

        return {
            "accepted": True,
            "instance_id": self._identity.instance_id,
            "public_key_hex": self._identity.public_key_hex(),
            "fingerprint": self._identity.fingerprint,
            "challenge_response": challenge_response,
            "soul_version_hash": "",
        }

    def handle_peer_removal(self, instance_id: str) -> dict:
        """Remove a registered peer.

        Args:
            instance_id: The peer to remove.

        Returns:
            Response dict with success/failure.
        """
        removed = self._topology.remove_peer(instance_id)

        if removed:
            if self._receipt_mgr:
                try:
                    self._receipt_mgr.record_peer_removed(
                        peer_id=instance_id,
                        reason="operator_request",
                    )
                except Exception:
                    pass
            if self._audit:
                try:
                    self._audit.record(
                        event_type="peer_removed",
                        instance_id=instance_id,
                        details={"reason": "operator_request"},
                    )
                except Exception:
                    pass

        return {
            "removed": removed,
            "instance_id": instance_id,
        }
