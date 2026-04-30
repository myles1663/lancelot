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

import json
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from src.federation.identity import FederationIdentity, sign_payload, verify_signature
from src.federation.topology import TopologyRegistry
from src.federation.transport import FederationTransport

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
    registration_id: str
    instance_id: str
    public_key_hex: str
    fingerprint: str
    address: str
    role: str
    soul_version_hash: str
    challenge: str
    expected_target_address: str = ""
    direction: str = "outbound"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "registration_id": self.registration_id,
            "instance_id": self.instance_id,
            "public_key_hex": self.public_key_hex,
            "fingerprint": self.fingerprint,
            "address": self.address,
            "role": self.role,
            "soul_version_hash": self.soul_version_hash,
            "challenge": self.challenge,
            "expected_target_address": self.expected_target_address,
            "direction": self.direction,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PendingRegistration":
        return cls(
            registration_id=data.get("registration_id", ""),
            instance_id=data.get("instance_id", ""),
            public_key_hex=data.get("public_key_hex", ""),
            fingerprint=data.get("fingerprint", ""),
            address=data.get("address", ""),
            role=data.get("role", "peer"),
            soul_version_hash=data.get("soul_version_hash", ""),
            challenge=data.get("challenge", ""),
            expected_target_address=data.get("expected_target_address", ""),
            direction=data.get("direction", "outbound"),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
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
        self_address: str = "",
        on_peer_registered=None,
        on_peer_removed=None,
        persistence_path: str = "",
        pending_ttl_s: float = 300.0,
    ):
        self._identity = identity
        self._topology = topology
        self._transport = transport
        self._receipt_mgr = receipt_mgr
        self._audit = audit
        self._pending: dict[str, PendingRegistration] = {}
        self._max_pending = max_pending
        self._self_address = self._normalize_address(self_address)
        self._on_peer_registered = on_peer_registered
        self._on_peer_removed = on_peer_removed
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._pending_ttl_s = pending_ttl_s
        self._load_pending()

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
        self._prune_expired_pending()
        target_address = self._normalize_address(target_address)
        if not target_address:
            return RegistrationResult(
                success=False,
                error="Target address is required for federation registration",
            )

        if not self._self_address:
            return RegistrationResult(
                success=False,
                error="Federation self_address must be configured before peer registration",
            )

        if len(self._pending) >= self._max_pending:
            return RegistrationResult(
                success=False,
                error="Too many pending federation registrations",
            )

        challenge = os.urandom(32).hex()
        registration_id = os.urandom(16).hex()
        self._pending[registration_id] = PendingRegistration(
            registration_id=registration_id,
            instance_id="",
            public_key_hex="",
            fingerprint="",
            address=self._self_address,
            role=target_role,
            soul_version_hash="",
            challenge=challenge,
            expected_target_address=target_address,
            direction="outbound",
        )
        self._persist_pending()

        request_body = {
            "registration_id": registration_id,
            "instance_id": self._identity.instance_id,
            "public_key_hex": self._identity.public_key_hex(),
            "fingerprint": self._identity.fingerprint,
            "address": self._self_address,
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
            self._pending.pop(registration_id, None)
            self._persist_pending()
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
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return RegistrationResult(
                success=False,
                error="Incomplete registration response from target",
            )

        # Verify the target signed our challenge correctly
        try:
            target_pub_bytes = bytes.fromhex(target_public_key_hex)
            response_sig_bytes = bytes.fromhex(challenge_response)
        except ValueError:
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return RegistrationResult(
                success=False,
                error="Invalid key or signature format in response",
            )

        if not verify_signature(
            target_pub_bytes, challenge.encode("utf-8"), response_sig_bytes,
        ):
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return RegistrationResult(
                success=False,
                error="Challenge response verification failed — target cannot prove identity",
            )

        if registration_id in self._pending:
            return RegistrationResult(
                success=False,
                error="Mutual confirmation did not complete",
            )

        target_peer = self._topology.get_peer(target_instance_id)
        if target_peer is None:
            return RegistrationResult(
                success=False,
                error="Peer confirmation completed but target was not pinned locally",
            )

        logger.info(
            "Peer registration completed: %s (fingerprint=%s, role=%s)",
            target_instance_id, target_peer.fingerprint[:8], target_role,
        )

        return RegistrationResult(
            success=True,
            peer_instance_id=target_instance_id,
            peer_fingerprint=target_peer.fingerprint,
            mutual=True,
        )

    async def handle_registration_request(self, request_data: dict) -> dict:
        """Handle an incoming peer registration request.

        Called by the API endpoint. Verifies the request, registers the
        peer after the mutual confirm leg completes, and returns our identity
        + signed challenge response.

        Args:
            request_data: The registration request body.

        Returns:
            Response dict with our identity and challenge response,
            or error dict.
        """
        self._prune_expired_pending()
        registration_id = request_data.get("registration_id", "")
        instance_id = request_data.get("instance_id", "")
        public_key_hex = request_data.get("public_key_hex", "")
        claimed_fingerprint = request_data.get("fingerprint", "")
        address = request_data.get("address", "")
        role = request_data.get("role", "peer")
        soul_version_hash = request_data.get("soul_version_hash", "")
        challenge = request_data.get("challenge", "")
        challenge_sig = request_data.get("challenge_signature", "")

        # Validate required fields
        if not all([registration_id, instance_id, public_key_hex, address, challenge, challenge_sig]):
            return {
                "error": "Missing required fields: registration_id, instance_id, public_key_hex, address, challenge, challenge_signature",
                "accepted": False,
            }

        address = self._normalize_address(address)
        if not address:
            return {
                "error": "Federation registration requires a valid initiator address",
                "accepted": False,
            }

        expected_fingerprint = self._fingerprint_for_public_key_hex(public_key_hex)
        if claimed_fingerprint and claimed_fingerprint != expected_fingerprint:
            return {
                "error": "Fingerprint does not match submitted public key",
                "accepted": False,
            }

        conflict = self._registration_conflict(
            instance_id=instance_id,
            public_key_hex=public_key_hex,
            address=address,
        )
        if conflict:
            return {"error": conflict, "accepted": False}

        # Verify the sender signed the challenge (proof of private key)
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

        challenge_response = sign_payload(
            self._identity, challenge.encode("utf-8"),
        ).hex()
        counter_challenge = os.urandom(32).hex()

        pending = PendingRegistration(
            registration_id=registration_id,
            instance_id=instance_id,
            public_key_hex=public_key_hex,
            fingerprint=expected_fingerprint,
            address=address,
            role=role,
            soul_version_hash=soul_version_hash,
            challenge=counter_challenge,
            direction="inbound",
        )
        if len(self._pending) >= self._max_pending and registration_id not in self._pending:
            return {
                "error": "Too many pending federation registrations",
                "accepted": False,
            }
        self._pending[registration_id] = pending
        self._persist_pending()

        confirm_body = {
            "registration_id": registration_id,
            "instance_id": self._identity.instance_id,
            "public_key_hex": self._identity.public_key_hex(),
            "fingerprint": self._identity.fingerprint,
            "address": self._self_address,
            "challenge_response": challenge_response,
            "counter_challenge": counter_challenge,
            "soul_version_hash": "",
        }

        confirm_result = await self._transport.send(
            peer_address=address,
            method="POST",
            path="/api/federation/peer/confirm",
            body=confirm_body,
            peer_id="registration-confirm-target",
            timeout_override_s=10.0,
        )
        if not confirm_result.success or not confirm_result.body:
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return {
                "error": confirm_result.error or f"Peer confirmation failed: HTTP {confirm_result.status_code}",
                "accepted": False,
            }

        confirm_payload = confirm_result.body
        if not confirm_payload.get("accepted"):
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return {
                "error": confirm_payload.get("error", "Peer confirmation rejected"),
                "accepted": False,
            }

        counter_response = confirm_payload.get("counter_challenge_response", "")
        try:
            counter_response_bytes = bytes.fromhex(counter_response)
        except ValueError:
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return {
                "error": "Invalid confirmation signature format",
                "accepted": False,
            }

        if not verify_signature(
            pub_bytes, counter_challenge.encode("utf-8"), counter_response_bytes,
        ):
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return {
                "error": "Counter-challenge verification failed",
                "accepted": False,
            }

        try:
            self._topology.register_peer(
                instance_id=instance_id,
                fingerprint=expected_fingerprint,
                public_key_hex=public_key_hex,
                address=address,
                role=role,
                soul_version_hash=soul_version_hash,
            )
        except ValueError as e:
            self._pending.pop(registration_id, None)
            self._persist_pending()
            return {
                "error": f"Registration rejected: {e}",
                "accepted": False,
            }
        self._notify_peer_registered(instance_id, address)

        # Emit receipt
        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_peer_registered(
                    peer_id=instance_id,
                    fingerprint=expected_fingerprint,
                    role=role,
                )
            except Exception as exc:
                logger.warning("Failed to record inbound peer registration receipt for %s: %s", instance_id, exc)

        if self._audit:
            try:
                self._audit.record(
                    event_type="peer_registered",
                    instance_id=instance_id,
                    details={
                        "fingerprint": expected_fingerprint,
                        "role": role,
                        "direction": "inbound",
                        "address": address,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to write inbound peer registration audit for %s: %s", instance_id, exc)

        logger.info(
            "Accepted peer registration: %s (fingerprint=%s, role=%s)",
            instance_id, expected_fingerprint[:8], role,
        )

        self._pending.pop(registration_id, None)
        self._persist_pending()

        return {
            "accepted": True,
            "registration_id": registration_id,
            "instance_id": self._identity.instance_id,
            "public_key_hex": self._identity.public_key_hex(),
            "fingerprint": self._identity.fingerprint,
            "challenge_response": challenge_response,
            "soul_version_hash": "",
            "mutual": True,
        }

    def handle_registration_confirm(self, request_data: dict) -> dict:
        """Handle the mutual confirm leg for a pending outbound registration."""
        self._prune_expired_pending()
        registration_id = request_data.get("registration_id", "")
        instance_id = request_data.get("instance_id", "")
        public_key_hex = request_data.get("public_key_hex", "")
        claimed_fingerprint = request_data.get("fingerprint", "")
        challenge_response = request_data.get("challenge_response", "")
        counter_challenge = request_data.get("counter_challenge", "")
        soul_version_hash = request_data.get("soul_version_hash", "")

        if not all([registration_id, instance_id, public_key_hex, challenge_response, counter_challenge]):
            return {
                "accepted": False,
                "error": "Missing required confirmation fields",
            }

        pending = self._pending.get(registration_id)
        if pending is None or pending.direction != "outbound":
            return {
                "accepted": False,
                "error": "Unknown or expired registration confirmation",
            }

        expected_fingerprint = self._fingerprint_for_public_key_hex(public_key_hex)
        if claimed_fingerprint and claimed_fingerprint != expected_fingerprint:
            return {
                "accepted": False,
                "error": "Fingerprint does not match submitted public key",
            }

        conflict = self._registration_conflict(
            instance_id=instance_id,
            public_key_hex=public_key_hex,
            address=pending.expected_target_address,
        )
        if conflict:
            return {"accepted": False, "error": conflict}

        try:
            target_pub_bytes = bytes.fromhex(public_key_hex)
            challenge_response_bytes = bytes.fromhex(challenge_response)
        except ValueError:
            return {
                "accepted": False,
                "error": "Invalid key or signature format",
            }

        if not verify_signature(
            target_pub_bytes, pending.challenge.encode("utf-8"), challenge_response_bytes,
        ):
            return {
                "accepted": False,
                "error": "Challenge response verification failed",
            }

        try:
            self._topology.register_peer(
                instance_id=instance_id,
                fingerprint=expected_fingerprint,
                public_key_hex=public_key_hex,
                address=pending.expected_target_address,
                role=pending.role,
                soul_version_hash=soul_version_hash,
            )
        except ValueError as e:
            return {
                "accepted": False,
                "error": f"Failed to register peer: {e}",
            }
        self._notify_peer_registered(instance_id, pending.expected_target_address)

        if self._receipt_mgr:
            try:
                self._receipt_mgr.record_peer_registered(
                    peer_id=instance_id,
                    fingerprint=expected_fingerprint,
                    role=pending.role,
                )
            except Exception as exc:
                logger.warning("Failed to record outbound peer registration receipt for %s: %s", instance_id, exc)

        if self._audit:
            try:
                self._audit.record(
                    event_type="peer_registered",
                    instance_id=instance_id,
                    details={
                        "fingerprint": expected_fingerprint,
                        "role": pending.role,
                        "direction": "outbound",
                        "address": pending.expected_target_address,
                    },
                )
            except Exception as exc:
                logger.warning("Failed to write outbound peer registration audit for %s: %s", instance_id, exc)

        self._pending.pop(registration_id, None)
        self._persist_pending()
        return {
            "accepted": True,
            "registration_id": registration_id,
            "instance_id": self._identity.instance_id,
            "fingerprint": self._identity.fingerprint,
            "counter_challenge_response": sign_payload(
                self._identity, counter_challenge.encode("utf-8"),
            ).hex(),
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
            self._notify_peer_removed(instance_id)

        if removed:
            if self._receipt_mgr:
                try:
                    self._receipt_mgr.record_peer_removed(
                        peer_id=instance_id,
                        reason="operator_request",
                    )
                except Exception as exc:
                    logger.warning("Failed to record peer removal receipt for %s: %s", instance_id, exc)
            if self._audit:
                try:
                    self._audit.record(
                        event_type="peer_removed",
                        instance_id=instance_id,
                        details={"reason": "operator_request"},
                    )
                except Exception as exc:
                    logger.warning("Failed to write peer removal audit for %s: %s", instance_id, exc)

        return {
            "removed": removed,
            "instance_id": instance_id,
        }

    def _notify_peer_registered(self, instance_id: str, address: str) -> None:
        if not callable(self._on_peer_registered):
            return
        try:
            self._on_peer_registered(instance_id, address)
        except Exception as exc:
            logger.warning("Peer registration callback failed for %s: %s", instance_id, exc)

    def _notify_peer_removed(self, instance_id: str) -> None:
        if not callable(self._on_peer_removed):
            return
        try:
            self._on_peer_removed(instance_id)
        except Exception as exc:
            logger.warning("Peer removal callback failed for %s: %s", instance_id, exc)

    @staticmethod
    def _normalize_address(address: str) -> str:
        value = (address or "").strip().rstrip("/")
        if not value:
            return ""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return value

    @staticmethod
    def _fingerprint_for_public_key_hex(public_key_hex: str) -> str:
        return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:16]

    def _registration_conflict(
        self,
        instance_id: str,
        public_key_hex: str,
        address: str,
    ) -> str:
        existing = self._topology.get_peer(instance_id)
        if not existing:
            return ""

        if existing.public_key_hex and existing.public_key_hex != public_key_hex:
            return "Registration rejected: known peer public key mismatch; explicit rekey flow required"

        existing_address = self._normalize_address(existing.address)
        new_address = self._normalize_address(address)
        if existing_address and new_address and existing_address != new_address:
            return "Registration rejected: known peer address mismatch; explicit rekey flow required"

        expected_fingerprint = self._fingerprint_for_public_key_hex(public_key_hex)
        if existing.fingerprint and existing.fingerprint != expected_fingerprint:
            return "Registration rejected: known peer fingerprint mismatch"

        return ""

    def _persist_pending(self) -> None:
        if self._persistence_path is None:
            return
        self._prune_expired_pending()
        payload = {
            "pending": [pending.to_dict() for pending in self._pending.values()],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistence_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_pending(self) -> None:
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        try:
            payload = json.loads(self._persistence_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load federation pending registrations: %s", exc)
            return
        pending: dict[str, PendingRegistration] = {}
        for item in payload.get("pending", []) or []:
            try:
                record = PendingRegistration.from_dict(item)
            except Exception as exc:
                logger.warning("Skipping invalid pending federation registration: %s", exc)
                continue
            if record.registration_id and not self._is_pending_expired(record):
                pending[record.registration_id] = record
        self._pending = pending

    def _prune_expired_pending(self) -> None:
        expired = [
            registration_id
            for registration_id, pending in self._pending.items()
            if self._is_pending_expired(pending)
        ]
        for registration_id in expired:
            self._pending.pop(registration_id, None)

    def _is_pending_expired(self, pending: PendingRegistration) -> bool:
        if self._pending_ttl_s <= 0:
            return False
        try:
            created_at = datetime.fromisoformat(pending.created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except Exception:
            return True
        age_s = (datetime.now(timezone.utc) - created_at).total_seconds()
        return age_s > self._pending_ttl_s
