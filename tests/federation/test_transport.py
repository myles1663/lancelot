# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Transport — HTTP client, retries, circuit breakers."""

import asyncio
import json

import pytest

from src.federation.identity import generate_identity
from src.federation.auth import FederationAuth
from src.federation.transport import (
    FederationTransport,
    PeerCircuitBreaker,
    CircuitState,
    TransportResult,
)


# ═══════════════════════════════════════════════════════════════
# Circuit Breaker Tests
# ═══════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = PeerCircuitBreaker(peer_id="test")
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request()

    def test_opens_after_threshold(self):
        cb = PeerCircuitBreaker(peer_id="test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.allow_request()

    def test_success_resets_in_half_open(self):
        cb = PeerCircuitBreaker(peer_id="test", failure_threshold=1, recovery_timeout_s=0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Immediate recovery (timeout=0)
        assert cb.allow_request()  # Transitions to HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_failure_in_half_open_reopens(self):
        cb = PeerCircuitBreaker(peer_id="test", failure_threshold=1, recovery_timeout_s=0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.allow_request()  # → HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_blocks_until_recovery(self):
        cb = PeerCircuitBreaker(peer_id="test", failure_threshold=1, recovery_timeout_s=9999)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.allow_request()

    def test_to_dict(self):
        cb = PeerCircuitBreaker(peer_id="test-peer")
        d = cb.to_dict()
        assert d["peer_id"] == "test-peer"
        assert d["state"] == "closed"

    def test_success_increments_counter(self):
        cb = PeerCircuitBreaker(peer_id="test")
        cb.record_success()
        cb.record_success()
        assert cb.success_count == 2


# ═══════════════════════════════════════════════════════════════
# Transport Client Tests (using httpx mock transport)
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def identity():
    return generate_identity()


@pytest.fixture
def auth(identity):
    return FederationAuth(identity=identity)


class TestTransportLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, auth):
        transport = FederationTransport(auth=auth)
        assert not transport.started
        await transport.start()
        assert transport.started
        await transport.stop()
        assert not transport.started

    @pytest.mark.asyncio
    async def test_send_without_start_fails(self, auth):
        transport = FederationTransport(auth=auth)
        result = await transport.send("http://test:8000", "GET", "/api/test")
        assert not result.success
        assert "not started" in result.error.lower()

    @pytest.mark.asyncio
    async def test_double_start_safe(self, auth):
        transport = FederationTransport(auth=auth)
        await transport.start()
        await transport.start()  # Should not raise
        assert transport.started
        await transport.stop()


class TestTransportSend:
    @pytest.mark.asyncio
    async def test_send_to_unreachable_peer(self, auth):
        """Send to a non-existent host — should fail after retries."""
        transport = FederationTransport(
            auth=auth,
            max_retries=1,
            base_backoff_s=0.01,
            connect_timeout_s=0.5,
        )
        await transport.start()
        try:
            result = await transport.send(
                "http://nonexistent-host:9999",
                "POST",
                "/api/federation/test",
                body={"test": True},
                peer_id="test-peer",
            )
            assert not result.success
            assert result.peer_id == "test-peer"
        finally:
            await transport.stop()

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self, auth):
        transport = FederationTransport(
            auth=auth,
            max_retries=1,
            base_backoff_s=0.01,
            connect_timeout_s=0.3,
            circuit_breaker_threshold=2,
        )
        await transport.start()
        try:
            # Fail twice to trip breaker
            for _ in range(2):
                await transport.send(
                    "http://nonexistent-host:9999", "GET", "/test",
                    peer_id="failing-peer",
                )

            # Next request should be circuit-broken
            result = await transport.send(
                "http://nonexistent-host:9999", "GET", "/test",
                peer_id="failing-peer",
            )
            assert not result.success
            assert result.circuit_open

            states = transport.get_circuit_breaker_states()
            assert "failing-peer" in states
            assert states["failing-peer"]["state"] == "open"
        finally:
            await transport.stop()

    @pytest.mark.asyncio
    async def test_reset_circuit_breaker(self, auth):
        transport = FederationTransport(
            auth=auth,
            max_retries=1,
            base_backoff_s=0.01,
            connect_timeout_s=0.3,
            circuit_breaker_threshold=1,
        )
        await transport.start()
        try:
            await transport.send(
                "http://nonexistent:9999", "GET", "/test",
                peer_id="reset-peer",
            )
            states = transport.get_circuit_breaker_states()
            assert states["reset-peer"]["state"] == "open"

            transport.reset_circuit_breaker("reset-peer")
            states = transport.get_circuit_breaker_states()
            assert states["reset-peer"]["state"] == "closed"
        finally:
            await transport.stop()


class TestTransportBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_empty_list(self, auth):
        transport = FederationTransport(auth=auth)
        await transport.start()
        try:
            results = await transport.broadcast([], "GET", "/test")
            assert results == {}
        finally:
            await transport.stop()

    @pytest.mark.asyncio
    async def test_broadcast_to_unreachable_peers(self, auth):
        transport = FederationTransport(
            auth=auth,
            max_retries=1,
            base_backoff_s=0.01,
            connect_timeout_s=0.3,
        )
        await transport.start()
        try:
            peers = [
                {"instance_id": "peer-1", "address": "http://fake1:9999"},
                {"instance_id": "peer-2", "address": "http://fake2:9999"},
            ]
            results = await transport.broadcast(peers, "POST", "/api/test", {"cmd": "hello"})
            assert len(results) == 2
            assert not results["peer-1"].success
            assert not results["peer-2"].success
        finally:
            await transport.stop()
