# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Transport — Resilient async HTTP client for peer-to-peer communication.

All outbound federation calls flow through FederationTransport. Features:
- Ed25519 request signing via FederationAuth
- Per-peer circuit breakers (CLOSED → OPEN → HALF_OPEN)
- Exponential backoff retry (configurable attempts)
- Connection pooling via httpx.AsyncClient
- Timeout management (connect, read, write, pool)
- Broadcast (fan-out) to multiple peers concurrently
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from src.federation.auth import FederationAuth

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════════

class CircuitState(str, Enum):
    CLOSED = "closed"        # Normal — requests flow through
    OPEN = "open"            # Tripped — requests rejected immediately
    HALF_OPEN = "half_open"  # Recovery — single test request allowed


@dataclass
class PeerCircuitBreaker:
    """Per-peer circuit breaker to prevent cascade failures.

    State transitions:
        CLOSED → OPEN:     failure_count >= threshold
        OPEN → HALF_OPEN:  recovery_timeout_s elapsed since last failure
        HALF_OPEN → CLOSED: test request succeeds
        HALF_OPEN → OPEN:  test request fails
    """
    peer_id: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    failure_threshold: int = 5
    recovery_timeout_s: float = 60.0
    last_failure_at: Optional[float] = None  # monotonic time
    last_success_at: Optional[float] = None

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self.last_failure_at is None:
                return False
            elapsed = time.monotonic() - self.last_failure_at
            if elapsed >= self.recovery_timeout_s:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit breaker HALF_OPEN for peer %s (%.1fs elapsed)",
                    self.peer_id, elapsed,
                )
                return True
            return False
        # HALF_OPEN — allow one test request
        return True

    def record_success(self) -> None:
        """Record a successful request."""
        self.last_success_at = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info("Circuit breaker CLOSED for peer %s (recovered)", self.peer_id)
        self.success_count += 1

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_at = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN for peer %s (half-open test failed)",
                self.peer_id,
            )
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN for peer %s (%d consecutive failures)",
                self.peer_id, self.failure_count,
            )

    def to_dict(self) -> dict:
        return {
            "peer_id": self.peer_id,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
        }


# ═══════════════════════════════════════════════════════════════
# Transport Result
# ═══════════════════════════════════════════════════════════════

@dataclass
class TransportResult:
    """Result of a federation HTTP call."""
    success: bool
    status_code: int = 0
    body: Optional[Dict[str, Any]] = None
    error: str = ""
    latency_ms: float = 0.0
    retries: int = 0
    peer_id: str = ""
    circuit_open: bool = False


# ═══════════════════════════════════════════════════════════════
# Federation Transport Client
# ═══════════════════════════════════════════════════════════════

class FederationTransport:
    """Async HTTP transport for federation peer-to-peer communication.

    Handles request signing, retries with backoff, circuit breaking,
    and connection pooling. All federation outbound calls go through
    send() or broadcast().
    """

    def __init__(
        self,
        auth: FederationAuth,
        max_retries: int = 3,
        base_backoff_s: float = 1.0,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_recovery_s: float = 60.0,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 30.0,
        write_timeout_s: float = 5.0,
        pool_timeout_s: float = 5.0,
        max_connections: int = 100,
        max_keepalive: int = 20,
        tls_verify: bool = True,
    ):
        self._auth = auth
        self._max_retries = max_retries
        self._base_backoff_s = base_backoff_s
        self._cb_threshold = circuit_breaker_threshold
        self._cb_recovery_s = circuit_breaker_recovery_s
        self._connect_timeout = connect_timeout_s
        self._read_timeout = read_timeout_s
        self._write_timeout = write_timeout_s
        self._pool_timeout = pool_timeout_s
        self._max_connections = max_connections
        self._max_keepalive = max_keepalive
        self._tls_verify = tls_verify

        self._client: Optional[httpx.AsyncClient] = None
        self._circuit_breakers: Dict[str, PeerCircuitBreaker] = {}
        self._started = False

    async def start(self) -> None:
        """Create the httpx.AsyncClient with connection pooling."""
        if self._started:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=self._write_timeout,
                pool=self._pool_timeout,
            ),
            limits=httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_keepalive,
            ),
            verify=self._tls_verify,
            follow_redirects=False,
        )
        self._started = True
        logger.info("Federation transport started (pool=%d/%d)", self._max_keepalive, self._max_connections)

    async def stop(self) -> None:
        """Close the HTTP client gracefully."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._started = False
        logger.info("Federation transport stopped")

    def _get_cb(self, peer_id: str) -> PeerCircuitBreaker:
        """Get or create a circuit breaker for a peer."""
        if peer_id not in self._circuit_breakers:
            self._circuit_breakers[peer_id] = PeerCircuitBreaker(
                peer_id=peer_id,
                failure_threshold=self._cb_threshold,
                recovery_timeout_s=self._cb_recovery_s,
            )
        return self._circuit_breakers[peer_id]

    async def send(
        self,
        peer_address: str,
        method: str,
        path: str,
        body: Optional[dict] = None,
        peer_id: str = "",
        timeout_override_s: Optional[float] = None,
    ) -> TransportResult:
        """Send a signed HTTP request to a federation peer.

        Includes retry with exponential backoff and circuit breaker checks.

        Args:
            peer_address: Base URL of the peer (e.g., "https://east.example.com:8000")
            method: HTTP method ("GET" or "POST")
            path: Request path (e.g., "/api/federation/killswitch")
            body: JSON body dict (for POST requests)
            peer_id: Peer instance ID (for circuit breaker tracking)
            timeout_override_s: Override the default read timeout for this call

        Returns:
            TransportResult with success/failure details.
        """
        if not self._client:
            return TransportResult(
                success=False, error="Transport not started", peer_id=peer_id,
            )

        # Circuit breaker check
        cb = self._get_cb(peer_id or peer_address)
        if not cb.allow_request():
            return TransportResult(
                success=False,
                error=f"Circuit breaker OPEN for {peer_id or peer_address}",
                peer_id=peer_id,
                circuit_open=True,
            )

        # Prepare body bytes
        body_bytes = json.dumps(body).encode("utf-8") if body else b""
        url = f"{peer_address.rstrip('/')}{path}"

        # Sign request
        auth_headers = self._auth.sign_request(method, path, body_bytes)

        # Prepare httpx request kwargs
        request_kwargs: Dict[str, Any] = {
            "headers": {
                **auth_headers,
                "Content-Type": "application/json",
            },
        }
        if body is not None:
            request_kwargs["content"] = body_bytes
        if timeout_override_s:
            request_kwargs["timeout"] = httpx.Timeout(
                connect=self._connect_timeout,
                read=timeout_override_s,
                write=self._write_timeout,
                pool=self._pool_timeout,
            )

        # Retry loop with exponential backoff
        last_error = ""
        retries = 0

        for attempt in range(self._max_retries):
            start_time = time.monotonic()
            try:
                if method.upper() == "GET":
                    response = await self._client.get(url, **request_kwargs)
                else:
                    response = await self._client.post(url, **request_kwargs)

                latency_ms = (time.monotonic() - start_time) * 1000

                if response.status_code < 500:
                    # Success (or 4xx client error — don't retry)
                    cb.record_success()
                    try:
                        response_body = response.json()
                    except Exception:
                        response_body = None

                    return TransportResult(
                        success=200 <= response.status_code < 300,
                        status_code=response.status_code,
                        body=response_body,
                        latency_ms=latency_ms,
                        retries=retries,
                        peer_id=peer_id,
                    )

                # 5xx — retry
                last_error = f"HTTP {response.status_code}"
                retries = attempt

            except httpx.ConnectError as exc:
                last_error = f"Connection error: {exc}"
                retries = attempt
            except httpx.TimeoutException as exc:
                last_error = f"Timeout: {exc}"
                retries = attempt
            except httpx.HTTPError as exc:
                last_error = f"HTTP error: {exc}"
                retries = attempt
            except Exception as exc:
                last_error = f"Unexpected: {exc}"
                retries = attempt

            # Backoff before retry (not on last attempt)
            if attempt < self._max_retries - 1:
                backoff = self._base_backoff_s * (2 ** attempt)
                logger.warning(
                    "Federation request failed (attempt %d/%d, peer=%s): %s — retrying in %.1fs",
                    attempt + 1, self._max_retries, peer_id or peer_address,
                    last_error, backoff,
                )
                await asyncio.sleep(backoff)

        # All retries exhausted
        cb.record_failure()
        logger.error(
            "Federation request failed after %d attempts (peer=%s): %s",
            self._max_retries, peer_id or peer_address, last_error,
        )
        return TransportResult(
            success=False,
            error=last_error,
            retries=retries,
            peer_id=peer_id,
        )

    async def broadcast(
        self,
        peers: List[dict],
        method: str,
        path: str,
        body: Optional[dict] = None,
        timeout_override_s: Optional[float] = None,
    ) -> Dict[str, TransportResult]:
        """Fan-out a signed request to multiple peers concurrently.

        Args:
            peers: List of dicts with 'instance_id' and 'address' keys.
            method: HTTP method
            path: Request path
            body: JSON body (same for all peers)
            timeout_override_s: Override read timeout

        Returns:
            Dict mapping instance_id to TransportResult.
        """
        if not peers:
            return {}

        async def _send_one(peer: dict) -> tuple[str, TransportResult]:
            pid = peer.get("instance_id", "")
            addr = peer.get("address", "")
            result = await self.send(
                peer_address=addr,
                method=method,
                path=path,
                body=body,
                peer_id=pid,
                timeout_override_s=timeout_override_s,
            )
            return pid, result

        tasks = [_send_one(p) for p in peers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: Dict[str, TransportResult] = {}
        for r in results:
            if isinstance(r, Exception):
                logger.error("Broadcast task error: %s", r)
                continue
            pid, result = r
            output[pid] = result

        successes = sum(1 for r in output.values() if r.success)
        logger.info(
            "Federation broadcast: %d/%d peers succeeded (path=%s)",
            successes, len(peers), path,
        )
        return output

    def get_circuit_breaker_states(self) -> Dict[str, dict]:
        """Return all circuit breaker states for monitoring."""
        return {pid: cb.to_dict() for pid, cb in self._circuit_breakers.items()}

    def reset_circuit_breaker(self, peer_id: str) -> bool:
        """Manually reset a circuit breaker to CLOSED."""
        cb = self._circuit_breakers.get(peer_id)
        if cb:
            cb.state = CircuitState.CLOSED
            cb.failure_count = 0
            logger.info("Circuit breaker manually reset for peer %s", peer_id)
            return True
        return False

    @property
    def started(self) -> bool:
        return self._started
