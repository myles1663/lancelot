# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Cost Reporter — Periodic cost data reporting between peers.

In hierarchical mode, children report their cost data to the root instance
at a configurable interval. The root aggregates this data via the
FederatedCostAggregator.

In federated mode, peers report to each other for mutual visibility.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.federation.identity import FederationIdentity
from src.federation.topology import TopologyRegistry
from src.federation.transport import FederationTransport

logger = logging.getLogger(__name__)


class CostReporter:
    """Periodic cost data reporter for federation cost governance."""

    def __init__(
        self,
        identity: FederationIdentity,
        transport: FederationTransport,
        topology: TopologyRegistry,
        cost_aggregator=None,
        usage_provider=None,
        interval_s: float = 30.0,
    ):
        """
        Args:
            identity: This instance's federation identity.
            transport: Federation transport client.
            topology: Peer registry.
            cost_aggregator: FederatedCostAggregator (root instances).
            usage_provider: Callable returning local cost data dict with keys:
                actual_today_usd, projected_today_usd, daily_ceiling_usd,
                active_spawns, spawn_cost_rate_usd_hr, total_tokens_today.
            interval_s: Reporting interval in seconds.
        """
        self._identity = identity
        self._transport = transport
        self._topology = topology
        self._cost_aggregator = cost_aggregator
        self._usage_provider = usage_provider
        self._interval_s = interval_s
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the background cost reporting loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._report_loop())
        logger.info("Cost reporter started (interval=%.1fs)", self._interval_s)

    async def stop(self) -> None:
        """Stop the background cost reporting loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Cost reporter stopped")

    async def _report_loop(self) -> None:
        """Background loop — report cost data at configured interval."""
        while self._running:
            try:
                await self.report_once()
            except Exception as exc:
                logger.error("Cost reporting error: %s", exc)
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                break

    async def report_once(self) -> Dict[str, bool]:
        """Send a single cost report to parent/peers.

        Returns:
            Dict mapping peer instance_id to delivery success.
        """
        if not self._usage_provider:
            return {}

        # Gather local cost data
        try:
            local_data = self._usage_provider()
        except Exception as exc:
            logger.warning("Failed to gather local cost data: %s", exc)
            return {}

        cost_report = {
            "instance_id": self._identity.instance_id,
            "fingerprint": self._identity.fingerprint,
            "actual_today_usd": local_data.get("actual_today_usd", 0.0),
            "projected_today_usd": local_data.get("projected_today_usd", 0.0),
            "daily_ceiling_usd": local_data.get("daily_ceiling_usd", 10.0),
            "active_spawns": local_data.get("active_spawns", 0),
            "spawn_cost_rate_usd_hr": local_data.get("spawn_cost_rate_usd_hr", 0.0),
            "total_tokens_today": local_data.get("total_tokens_today", 0),
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }

        # Determine reporting targets
        peers = self._topology.list_peers()
        if not peers:
            return {}

        # In hierarchical mode, report to root only
        root_peers = [p for p in peers if p.role == "root"]
        targets = root_peers if root_peers else peers

        peer_dicts = [
            {"instance_id": p.instance_id, "address": p.address}
            for p in targets
        ]

        results = await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/budget/report",
            body=cost_report,
        )

        return {pid: r.success for pid, r in results.items()}

    def handle_cost_report(self, request_data: dict) -> dict:
        """Handle an incoming cost report from a peer.

        Feeds the data into the FederatedCostAggregator if available.

        Args:
            request_data: The cost report payload.

        Returns:
            Response dict with acceptance.
        """
        instance_id = request_data.get("instance_id", "")

        # Validate source is a known peer
        peer = self._topology.get_peer(instance_id)
        if not peer:
            return {
                "accepted": False,
                "error": f"Unknown peer: {instance_id}",
            }

        # Feed to aggregator
        if self._cost_aggregator:
            try:
                self._cost_aggregator.update_instance(
                    instance_id=instance_id,
                    actual_today_usd=request_data.get("actual_today_usd", 0.0),
                    projected_today_usd=request_data.get("projected_today_usd", 0.0),
                    daily_ceiling_usd=request_data.get("daily_ceiling_usd", 10.0),
                    active_spawns=request_data.get("active_spawns", 0),
                    spawn_cost_rate_usd_hr=request_data.get("spawn_cost_rate_usd_hr", 0.0),
                    total_tokens_today=request_data.get("total_tokens_today", 0),
                )
            except Exception as exc:
                logger.warning("Cost aggregator update failed: %s", exc)

        return {
            "accepted": True,
            "instance_id": self._identity.instance_id,
        }

    def get_aggregate_status(self) -> dict:
        """Get current aggregated cost status.

        Returns aggregator data if this is a root instance, otherwise
        returns local cost data only.
        """
        if self._cost_aggregator:
            try:
                return self._cost_aggregator.get_aggregate()
            except Exception:
                pass

        if self._usage_provider:
            try:
                return self._usage_provider()
            except Exception:
                pass

        return {"error": "No cost data available"}

    @property
    def running(self) -> bool:
        return self._running
