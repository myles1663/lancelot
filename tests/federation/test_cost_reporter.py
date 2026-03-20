# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Tests for Federation Cost Reporter — cost data handlers."""

import pytest

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


class TestGetAggregateStatus:
    def test_no_data_returns_error(self, root_reporter):
        result = root_reporter.get_aggregate_status()
        assert "error" in result

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


class TestLifecycle:
    def test_not_running_by_default(self, root_reporter):
        assert not root_reporter.running
