# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Cost Reporter — Periodic cost data reporting between peers.

In hierarchical mode, children report their cost data upward to the root
instance at a configurable interval. The root alone aggregates remote cost
data into federation-wide threshold decisions.

In non-hierarchical/peer mode, runtime cost governance remains local; remote
peer reports are not allowed to drive threshold actions on this instance.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.federation.cost_aggregation import InstanceCostData
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

    def _root_peers(self):
        return [peer for peer in self._topology.list_peers() if peer.role == "root"]

    def _child_peers(self):
        return [peer for peer in self._topology.list_peers() if peer.role == "child"]

    def _accepts_remote_budget_reports(self) -> bool:
        return self._cost_aggregator is not None and bool(self._child_peers())

    def _gather_local_usage(self) -> Optional[dict]:
        """Collect a local cost snapshot from the configured provider."""
        if not self._usage_provider:
            return None
        try:
            return self._usage_provider()
        except Exception as exc:
            logger.warning("Failed to gather local cost data: %s", exc)
            return None

    def _update_aggregator_with_local_usage(self, local_data: Optional[dict]) -> None:
        """Keep the root/local aggregate truthful for this instance."""
        if not self._cost_aggregator or not local_data:
            return
        try:
            self._cost_aggregator.update_instance(InstanceCostData(
                instance_id=self._identity.instance_id,
                actual_today_usd=local_data.get("actual_today_usd", 0.0),
                projected_today_usd=local_data.get("projected_today_usd", 0.0),
                daily_ceiling_usd=local_data.get("daily_ceiling_usd", 10.0),
                active_spawns=local_data.get("active_spawns", 0),
                spawn_cost_rate_usd_hr=local_data.get("spawn_cost_rate_usd_hr", 0.0),
                total_tokens_today=local_data.get("total_tokens_today", 0),
            ))
        except Exception as exc:
            logger.warning("Failed to refresh local federation cost aggregate: %s", exc)

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
                logger.debug("Cost reporter background task cancelled during stop")
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
        local_data = self._gather_local_usage()
        if not local_data:
            return {}

        self._update_aggregator_with_local_usage(local_data)

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
        root_peers = self._root_peers()
        if not root_peers:
            return {}

        peer_dicts = [
            {"instance_id": p.instance_id, "address": p.address}
            for p in root_peers
        ]

        results = await self._transport.broadcast(
            peers=peer_dicts,
            method="POST",
            path="/api/federation/budget/report",
            body=cost_report,
        )

        return {pid: r.success for pid, r in results.items()}

    def handle_cost_report(
        self,
        request_data: dict,
        authenticated_instance_id: Optional[str] = None,
    ) -> dict:
        """Handle an incoming cost report from a peer.

        Feeds the data into the FederatedCostAggregator if available.

        Args:
            request_data: The cost report payload.

        Returns:
            Response dict with acceptance.
        """
        instance_id = request_data.get("instance_id", "")
        if authenticated_instance_id:
            if instance_id and instance_id != authenticated_instance_id:
                return {
                    "accepted": False,
                    "error": (
                        "Reported instance does not match authenticated peer: "
                        f"{instance_id} != {authenticated_instance_id}"
                    ),
                }
            instance_id = authenticated_instance_id

        # Validate source is a known peer
        peer = self._topology.get_peer(instance_id)
        if not peer:
            return {
                "accepted": False,
                "error": f"Unknown peer: {instance_id}",
            }

        if not self._accepts_remote_budget_reports():
            return {
                "accepted": False,
                "error": "Local federation budget aggregation is not enabled for remote peers",
                "instance_id": self._identity.instance_id,
            }

        if peer.role != "child":
            return {
                "accepted": False,
                "error": (
                    "Peer role is not permitted to influence local federation budget governance: "
                    f"{peer.role}"
                ),
                "instance_id": self._identity.instance_id,
            }

        # Feed to aggregator
        try:
            self._cost_aggregator.update_instance(InstanceCostData(
                instance_id=instance_id,
                actual_today_usd=request_data.get("actual_today_usd", 0.0),
                projected_today_usd=request_data.get("projected_today_usd", 0.0),
                daily_ceiling_usd=request_data.get("daily_ceiling_usd", 10.0),
                active_spawns=request_data.get("active_spawns", 0),
                spawn_cost_rate_usd_hr=request_data.get("spawn_cost_rate_usd_hr", 0.0),
                total_tokens_today=request_data.get("total_tokens_today", 0),
            ))
        except Exception as exc:
            logger.warning("Cost aggregator update failed: %s", exc)
            return {
                "accepted": False,
                "error": f"Cost aggregator update failed: {exc}",
                "instance_id": self._identity.instance_id,
            }

        return {
            "accepted": True,
            "instance_id": self._identity.instance_id,
        }

    def get_aggregate_status(self) -> dict:
        """Get current aggregated cost status.

        Returns aggregator data if this is a root instance, otherwise
        returns local cost data only.
        """
        local_data = self._gather_local_usage()
        self._update_aggregator_with_local_usage(local_data)

        if self._cost_aggregator:
            try:
                aggregate = self._cost_aggregator.get_aggregate()
                payload = aggregate.to_dict() if hasattr(aggregate, "to_dict") else aggregate
                if isinstance(payload, dict):
                    payload["stale_instance_ids"] = self._cost_aggregator.get_stale_instance_ids()
                return payload
            except Exception as exc:
                logger.warning("Failed to compute federated cost aggregate: %s", exc)
                return {
                    "error": f"Failed to compute federated cost aggregate: {exc}",
                    "stale_instance_ids": [],
                }

        if local_data:
            return local_data

        return {"error": "No cost data available"}

    @property
    def running(self) -> bool:
        return self._running
