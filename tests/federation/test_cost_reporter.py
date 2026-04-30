# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Cost Reporter — cost data handlers."""

import pytest

from src.federation.cost_aggregation import FederatedCostAggregator, InstanceCostData
from src.federation.identity import generate_identity
from src.federation.topology import TopologyRegistry
from src.federation.cost_reporter import CostReporter


@pytest.fixture
def root_identity():
    return generate_identity()


@pytest.fixture
def child_identity():
    return generate_identity()


@pytest.fixture
def root_topology(root_identity, child_identity):
    topo = TopologyRegistry(self_instance_id=root_identity.instance_id)
    topo.register_peer(
        instance_id=child_identity.instance_id,
        address="http://child:8000",
        role="child",
    )
    return topo


@pytest.fixture
def root_reporter(root_identity, root_topology):
    return CostReporter(
        identity=root_identity,
        transport=None,
        topology=root_topology,
        cost_aggregator=FederatedCostAggregator(),
    )


class TestHandleCostReport:
    def test_valid_report_accepted(self, root_reporter, child_identity):
        result = root_reporter.handle_cost_report({
            "instance_id": child_identity.instance_id,
            "actual_today_usd": 1.50,
            "projected_today_usd": 3.00,
            "daily_ceiling_usd": 5.00,
            "active_spawns": 2,
            "spawn_cost_rate_usd_hr": 0.25,
            "total_tokens_today": 50000,
        })
        assert result["accepted"]

    def test_unknown_peer_rejected(self, root_reporter):
        result = root_reporter.handle_cost_report({
            "instance_id": "unknown-peer",
            "actual_today_usd": 1.00,
        })
        assert not result["accepted"]
        assert "Unknown" in result["error"]

    def test_report_rejects_body_instance_spoof(self, root_reporter, child_identity):
        result = root_reporter.handle_cost_report(
            {
                "instance_id": "spoofed-peer",
                "actual_today_usd": 1.50,
            },
            authenticated_instance_id=child_identity.instance_id,
        )
        assert result["accepted"] is False
        assert "does not match authenticated peer" in result["error"]

    def test_report_rejects_when_aggregator_update_fails(self, root_identity, root_topology, child_identity):
        class BrokenAggregator:
            def update_instance(self, data):
                raise RuntimeError("sqlite write failed")

        reporter = CostReporter(
            identity=root_identity,
            transport=None,
            topology=root_topology,
            cost_aggregator=BrokenAggregator(),
        )

        result = reporter.handle_cost_report({
            "instance_id": child_identity.instance_id,
            "actual_today_usd": 1.50,
        })

        assert result["accepted"] is False
        assert "sqlite write failed" in result["error"]

    def test_report_rejects_non_child_peer_for_budget_influence(self, root_identity):
        topology = TopologyRegistry(self_instance_id=root_identity.instance_id)
        child_identity = generate_identity()
        peer_identity = generate_identity()
        topology.register_peer(
            instance_id=child_identity.instance_id,
            address="http://child:8000",
            role="child",
        )
        topology.register_peer(
            instance_id=peer_identity.instance_id,
            address="http://peer:8000",
            role="peer",
        )
        reporter = CostReporter(
            identity=root_identity,
            transport=None,
            topology=topology,
            cost_aggregator=FederatedCostAggregator(),
        )

        result = reporter.handle_cost_report(
            {"instance_id": peer_identity.instance_id, "actual_today_usd": 50.0}
        )

        assert result["accepted"] is False
        assert "not permitted" in result["error"]


class TestGetAggregateStatus:
    def test_no_data_returns_error(self, root_reporter):
        result = root_reporter.get_aggregate_status()
        assert result["instance_count"] == 0
        assert result["threshold"] == "normal"

    def test_with_usage_provider(self, root_identity, root_topology):
        def usage():
            return {"actual_today_usd": 2.0, "daily_ceiling_usd": 10.0}

        reporter = CostReporter(
            identity=root_identity,
            transport=None,
            topology=root_topology,
            usage_provider=usage,
        )
        result = reporter.get_aggregate_status()
        assert result["actual_today_usd"] == 2.0

    def test_with_usage_provider_and_aggregator_returns_live_threshold(self, root_identity, root_topology):
        def usage():
            return {
                "actual_today_usd": 9.0,
                "projected_today_usd": 9.5,
                "daily_ceiling_usd": 10.0,
                "active_spawns": 2,
                "spawn_cost_rate_usd_hr": 0.25,
                "total_tokens_today": 12000,
            }

        reporter = CostReporter(
            identity=root_identity,
            transport=None,
            topology=root_topology,
            cost_aggregator=FederatedCostAggregator(),
            usage_provider=usage,
        )
        result = reporter.get_aggregate_status()
        assert result["instance_count"] == 1
        assert result["threshold"] == "spawn_restricted"
        assert result["total_actual_usd"] == 9.0

    def test_aggregate_status_includes_stale_peer_ids(self, root_identity, root_topology, child_identity):
        aggregator = FederatedCostAggregator(stale_after_s=1.0)
        aggregator.update_instance(
            InstanceCostData(
                instance_id=child_identity.instance_id,
                actual_today_usd=1.0,
                daily_ceiling_usd=10.0,
                updated_at="2000-01-01T00:00:00+00:00",
            )
        )
        reporter = CostReporter(
            identity=root_identity,
            transport=None,
            topology=root_topology,
            cost_aggregator=aggregator,
        )
        result = reporter.get_aggregate_status()
        assert result["stale_instance_ids"] == [child_identity.instance_id]

    def test_aggregate_status_fails_closed_when_aggregator_errors(self, root_identity, root_topology):
        class BrokenAggregator:
            def get_aggregate(self):
                raise RuntimeError("aggregate unavailable")

        reporter = CostReporter(
            identity=root_identity,
            transport=None,
            topology=root_topology,
            cost_aggregator=BrokenAggregator(),
            usage_provider=lambda: {"actual_today_usd": 2.0, "daily_ceiling_usd": 10.0},
        )

        result = reporter.get_aggregate_status()
        assert "error" in result
        assert "aggregate unavailable" in result["error"]
        assert result["stale_instance_ids"] == []


class TestLifecycle:
    def test_not_running_by_default(self, root_reporter):
        assert not root_reporter.running

    @pytest.mark.asyncio
    async def test_report_once_targets_root_peers_only(self, root_identity):
        child_topology = TopologyRegistry(self_instance_id=root_identity.instance_id)
        root_peer = generate_identity()
        child_peer = generate_identity()
        child_topology.register_peer(
            instance_id=root_peer.instance_id,
            address="http://root:8000",
            role="root",
        )
        child_topology.register_peer(
            instance_id=child_peer.instance_id,
            address="http://child:8000",
            role="child",
        )
        captured = {}

        class FakeTransport:
            async def broadcast(self, peers, method, path, body):
                captured["peers"] = peers
                captured["method"] = method
                captured["path"] = path
                return {peer["instance_id"]: type("R", (), {"success": True})() for peer in peers}

        reporter = CostReporter(
            identity=root_identity,
            transport=FakeTransport(),
            topology=child_topology,
            usage_provider=lambda: {"actual_today_usd": 2.0, "daily_ceiling_usd": 10.0},
        )

        result = await reporter.report_once()

        assert list(result.keys()) == [root_peer.instance_id]
        assert captured["method"] == "POST"
        assert captured["path"] == "/api/federation/budget/report"
        assert captured["peers"] == [{"instance_id": root_peer.instance_id, "address": "http://root:8000"}]

    @pytest.mark.asyncio
    async def test_report_once_skips_broadcast_without_root_peer(self, root_identity):
        topology = TopologyRegistry(self_instance_id=root_identity.instance_id)
        peer_identity = generate_identity()
        topology.register_peer(
            instance_id=peer_identity.instance_id,
            address="http://peer:8000",
            role="peer",
        )

        class FakeTransport:
            async def broadcast(self, peers, method, path, body):
                raise AssertionError("broadcast should not be called without a root peer")

        reporter = CostReporter(
            identity=root_identity,
            transport=FakeTransport(),
            topology=topology,
            usage_provider=lambda: {"actual_today_usd": 2.0, "daily_ceiling_usd": 10.0},
        )

        result = await reporter.report_once()
        assert result == {}
