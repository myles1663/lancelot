# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Heartbeat Mesh — SSE subscription manager for peer health monitoring.

Each instance subscribes to its known peers' SSE heartbeat streams
(GET /api/federation/stream). Incoming heartbeats update the TopologyRegistry
with fresh timestamps, enabling the health classification system.

Features:
- Per-peer SSE connection with automatic reconnect on disconnect
- Exponential backoff for reconnection (1s → 2s → 4s → ... → 30s cap)
- Graceful shutdown of all subscription tasks
- Dynamic peer add/remove without restart
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Optional

import httpx

from src.core.outbound_http import assert_url_allowed
from src.federation.auth import FederationAuth
from src.federation.topology import TopologyRegistry

logger = logging.getLogger(__name__)

# Maximum reconnect backoff (seconds)
MAX_RECONNECT_BACKOFF_S = 30.0


class HeartbeatMesh:
    """Manages SSE heartbeat subscriptions to all known federation peers."""

    def __init__(
        self,
        topology: TopologyRegistry,
        divergence_detector=None,
        auth: Optional[FederationAuth] = None,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 120.0,
        current_soul_hash_provider=None,
        active_task_count_provider=None,
        hive_spawn_count_provider=None,
        hive_spawn_states_provider=None,
        pending_handoffs_provider=None,
        budget_utilization_provider=None,
        on_diverged=None,
        on_reconnecting=None,
        network_interceptor=None,
    ):
        self._topology = topology
        self._divergence = divergence_detector
        self._auth = auth
        self._connect_timeout = connect_timeout_s
        self._read_timeout = read_timeout_s
        self._current_soul_hash_provider = current_soul_hash_provider
        self._active_task_count_provider = active_task_count_provider
        self._hive_spawn_count_provider = hive_spawn_count_provider
        self._hive_spawn_states_provider = hive_spawn_states_provider
        self._pending_handoffs_provider = pending_handoffs_provider
        self._budget_utilization_provider = budget_utilization_provider
        self._on_diverged = on_diverged
        self._on_reconnecting = on_reconnecting
        self._network_interceptor = network_interceptor

        self._tasks: Dict[str, asyncio.Task] = {}
        self._subscription_status: Dict[str, str] = {}
        self._stream_outcome_status: Dict[str, str] = {}
        self._stream_errors: Dict[str, str] = {}
        self._running = False
        self._client: Optional[httpx.AsyncClient] = None
        self._divergence_evaluation_failed = False
        self._divergence_status_error: Optional[str] = None

    async def start(self) -> None:
        """Start heartbeat subscriptions for all known peers."""
        if self._running:
            return

        self._running = True
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self._read_timeout,
                write=5.0,
                pool=5.0,
            ),
            follow_redirects=False,
        )

        # Subscribe to all known peers
        for peer in self._topology.list_peers():
            if peer.address:
                self._start_subscription(peer.instance_id, peer.address)

        self._evaluate_divergence()

        logger.info(
            "Heartbeat mesh started: %d subscriptions", len(self._tasks)
        )

    async def stop(self) -> None:
        """Stop all heartbeat subscriptions."""
        self._running = False

        # Cancel all tasks
        for pid, task in self._tasks.items():
            task.cancel()

        # Wait for all tasks to finish
        if self._tasks:
            await asyncio.gather(
                *self._tasks.values(), return_exceptions=True
            )

        self._tasks.clear()
        self._subscription_status.clear()
        self._stream_outcome_status.clear()
        self._stream_errors.clear()

        if self._client:
            await self._client.aclose()
            self._client = None

        logger.info("Heartbeat mesh stopped")

    def on_peer_added(self, instance_id: str, address: str) -> None:
        """Start a heartbeat subscription for a newly added peer."""
        if not self._running:
            return
        if instance_id in self._tasks:
            return
        self._start_subscription(instance_id, address)
        self._evaluate_divergence()

    def on_peer_removed(self, instance_id: str) -> None:
        """Cancel the heartbeat subscription for a removed peer."""
        task = self._tasks.pop(instance_id, None)
        if task:
            task.cancel()
            logger.info("Heartbeat subscription cancelled for %s", instance_id)
        self._subscription_status.pop(instance_id, None)
        self._stream_outcome_status.pop(instance_id, None)
        self._stream_errors.pop(instance_id, None)
        self._evaluate_divergence()

    def _start_subscription(self, instance_id: str, address: str) -> None:
        """Start an async task to subscribe to a peer's heartbeat stream."""
        task = asyncio.create_task(
            self._subscribe_loop(instance_id, address),
            name=f"hb-mesh-{instance_id[:8]}",
        )
        self._tasks[instance_id] = task
        self._subscription_status[instance_id] = "connecting"
        self._stream_outcome_status[instance_id] = "unknown"
        self._stream_errors.pop(instance_id, None)

    async def _subscribe_loop(self, instance_id: str, address: str) -> None:
        """Long-running SSE subscription with reconnect logic."""
        backoff = 1.0
        url = f"{address.rstrip('/')}/api/federation/stream"

        while self._running:
            try:
                await self._consume_stream(instance_id, url)
                # Stream ended cleanly — reconnect immediately
                backoff = 1.0
                self._subscription_status[instance_id] = "reconnecting"
            except httpx.ConnectError:
                logger.debug("Heartbeat connection failed for %s", instance_id)
                self._subscription_status[instance_id] = "reconnecting"
                self._stream_outcome_status[instance_id] = "failed"
                self._stream_errors[instance_id] = "connect failed"
            except httpx.TimeoutException:
                logger.debug("Heartbeat stream timeout for %s", instance_id)
                self._subscription_status[instance_id] = "reconnecting"
                self._stream_outcome_status[instance_id] = "failed"
                self._stream_errors[instance_id] = "timeout"
            except asyncio.CancelledError:
                self._subscription_status[instance_id] = "disconnected"
                return
            except Exception as exc:
                logger.warning(
                    "Heartbeat subscription error for %s: %s", instance_id, exc
                )
                self._subscription_status[instance_id] = "reconnecting"
                self._stream_outcome_status[instance_id] = "failed"
                self._stream_errors[instance_id] = str(exc)

            if not self._running:
                return

            # Exponential backoff for reconnect
            logger.debug(
                "Reconnecting heartbeat for %s in %.1fs", instance_id, backoff
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF_S)

    async def _consume_stream(self, instance_id: str, url: str) -> None:
        """Connect to and consume an SSE heartbeat stream."""
        if not self._client:
            return
        assert_url_allowed(
            url,
            component="Federation heartbeat stream",
            network_interceptor=self._network_interceptor,
        )

        path = "/api/federation/stream"
        headers = self._auth.sign_request("GET", path, b"") if self._auth else {}

        async with self._client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                logger.warning(
                    "Heartbeat stream %s returned HTTP %d",
                    instance_id, response.status_code,
                )
                self._subscription_status[instance_id] = "failed"
                self._stream_outcome_status[instance_id] = "failed"
                self._stream_errors[instance_id] = f"HTTP {response.status_code}"
                return

            self._subscription_status[instance_id] = "connected"
            self._stream_outcome_status[instance_id] = "ok"
            self._stream_errors.pop(instance_id, None)
            buffer = ""
            async for chunk in response.aiter_text():
                if not self._running:
                    return

                buffer += chunk
                # Parse SSE events from buffer
                while "\n\n" in buffer:
                    event_text, buffer = buffer.split("\n\n", 1)
                    self._process_sse_event(instance_id, event_text)

    def _process_sse_event(self, instance_id: str, event_text: str) -> None:
        """Parse and process a single SSE event."""
        data_line = ""
        for line in event_text.strip().split("\n"):
            if line.startswith("data: "):
                data_line = line[6:]

        if not data_line:
            return

        try:
            heartbeat = json.loads(data_line)
        except json.JSONDecodeError:
            return

        # Update topology with fresh heartbeat
        timestamp = heartbeat.get("timestamp")
        soul_hash = heartbeat.get("soul_version_hash")

        self._topology.update_heartbeat(
            instance_id=instance_id,
            timestamp=timestamp,
            soul_version_hash=soul_hash,
        )

        # Feed to divergence detector
        self._evaluate_divergence(instance_id)

    def get_subscription_status(self) -> Dict[str, str]:
        """Return status of all heartbeat subscriptions."""
        return dict(self._subscription_status)

    def get_stream_outcome_status(self) -> Dict[str, str]:
        """Return the last observed stream outcome for each peer."""
        return dict(self._stream_outcome_status)

    def get_stream_errors(self) -> Dict[str, str]:
        """Return last stream error detail for peers with a failed outcome."""
        return dict(self._stream_errors)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def subscription_count(self) -> int:
        return len(self._tasks)

    @property
    def divergence_evaluation_failed(self) -> bool:
        return self._divergence_evaluation_failed

    @property
    def divergence_status_error(self) -> Optional[str]:
        return self._divergence_status_error

    def _safe_provider(self, provider, default):
        if not callable(provider):
            return default
        try:
            return provider()
        except Exception:
            return default

    def _evaluate_divergence(self, instance_id: str = "") -> None:
        if not self._divergence:
            return
        try:
            previous_state = getattr(self._divergence, "state", None)
            peer_heartbeats = self._topology.get_peer_heartbeats()
            state, snapshot = self._divergence.check_connectivity(
                peer_heartbeats,
                current_soul_hash=self._safe_provider(self._current_soul_hash_provider, ""),
                active_task_count=self._safe_provider(self._active_task_count_provider, 0),
                hive_spawn_count=self._safe_provider(self._hive_spawn_count_provider, 0),
                hive_spawn_states=self._safe_provider(self._hive_spawn_states_provider, {}),
                pending_handoffs=self._safe_provider(self._pending_handoffs_provider, []),
                budget_utilization_pct=self._safe_provider(self._budget_utilization_provider, 0.0),
            )
            if (
                getattr(state, "value", "") == "diverged"
                and previous_state != state
                and snapshot is not None
                and callable(self._on_diverged)
            ):
                self._on_diverged(instance_id, snapshot)
            elif (
                getattr(state, "value", "") == "reconnecting"
                and previous_state != state
                and callable(self._on_reconnecting)
            ):
                self._on_reconnecting(instance_id, self._divergence)
            self._divergence_evaluation_failed = False
            self._divergence_status_error = None
        except Exception as exc:
            self._divergence_evaluation_failed = True
            self._divergence_status_error = str(exc)
            logger.exception("Federation divergence evaluation failed")
