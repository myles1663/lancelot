# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federated Cost Aggregation Engine."""

import threading

import pytest
from src.federation.cost_aggregation import (
    CostThreshold,
    FederatedCostAggregator,
    InstanceCostData,
)


@pytest.fixture
def aggregator():
    return FederatedCostAggregator()


def _cost(instance_id, actual=0.0, projected=0.0, ceiling=10.0, spawns=0):
    return InstanceCostData(
        instance_id=instance_id,
        actual_today_usd=actual,
        projected_today_usd=projected,
        daily_ceiling_usd=ceiling,
        active_spawns=spawns,
    )


class TestThresholds:
    def test_normal_empty(self, aggregator):
        assert aggregator.current_threshold == CostThreshold.NORMAL

    def test_normal_under_75(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=7.0, ceiling=10.0))
        assert aggregator.current_threshold == CostThreshold.NORMAL

    def test_warning_at_75(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=7.5, ceiling=10.0))
        assert aggregator.current_threshold == CostThreshold.WARNING

    def test_spawn_restricted_at_85(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=8.5, ceiling=10.0))
        assert aggregator.current_threshold == CostThreshold.SPAWN_RESTRICTED

    def test_spawn_gated_at_95(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=9.5, ceiling=10.0))
        assert aggregator.current_threshold == CostThreshold.SPAWN_GATED

    def test_hard_stop_at_100(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=10.0, ceiling=10.0))
        assert aggregator.current_threshold == CostThreshold.HARD_STOP

    def test_multi_instance_aggregate(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=4.0, ceiling=10.0))
        aggregator.update_instance(_cost("i2", actual=4.0, ceiling=10.0))
        # Total: 8/20 = 40% — normal
        assert aggregator.current_threshold == CostThreshold.NORMAL


class TestThresholdCallback:
    def test_callback_fires(self):
        changes = []
        agg = FederatedCostAggregator(
            on_threshold_change=lambda old, new: changes.append((old, new))
        )
        agg.update_instance(_cost("i1", actual=8.0, ceiling=10.0))
        assert len(changes) == 1
        assert changes[0] == (CostThreshold.NORMAL, CostThreshold.WARNING)

    def test_callback_can_read_aggregate_without_deadlocking(self):
        observed = {}
        agg = None

        def on_threshold_change(old, new):
            observed["pair"] = (old, new)
            observed["utilization_pct"] = agg.get_aggregate().utilization_pct

        agg = FederatedCostAggregator(on_threshold_change=on_threshold_change)
        worker = threading.Thread(
            target=lambda: agg.update_instance(_cost("i1", actual=10.0, ceiling=10.0)),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=2.0)

        assert not worker.is_alive()
        assert observed["pair"] == (CostThreshold.NORMAL, CostThreshold.HARD_STOP)
        assert observed["utilization_pct"] == 100.0


class TestAggregate:
    def test_aggregate_sums(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=5.0, projected=7.0, ceiling=10.0, spawns=2))
        aggregator.update_instance(_cost("i2", actual=3.0, projected=4.0, ceiling=10.0, spawns=1))
        agg = aggregator.get_aggregate()
        assert agg.total_actual_usd == 8.0
        assert agg.total_projected_usd == 11.0
        assert agg.total_ceiling_usd == 20.0
        assert agg.total_active_spawns == 3
        assert agg.instance_count == 2

    def test_highest_utilization(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=9.0, ceiling=10.0))
        aggregator.update_instance(_cost("i2", actual=3.0, ceiling=10.0))
        agg = aggregator.get_aggregate()
        assert agg.highest_utilization_instance == "i1"
        assert agg.highest_utilization_pct == 90.0

    def test_aggregate_to_dict(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=5.0, ceiling=10.0))
        d = aggregator.get_aggregate().to_dict()
        assert "total_actual_usd" in d
        assert "threshold" in d


class TestSpawnAllowed:
    def test_allowed_normal(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=5.0, ceiling=10.0))
        ok, _ = aggregator.check_spawn_allowed("i1")
        assert ok

    def test_blocked_spawn_gated(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=9.5, ceiling=10.0))
        ok, reason = aggregator.check_spawn_allowed("i1")
        assert not ok
        assert "95%" in reason

    def test_blocked_hard_stop(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=10.0, ceiling=10.0))
        ok, reason = aggregator.check_spawn_allowed("i1")
        assert not ok
        assert "hard stop" in reason.lower()

    def test_blocked_instance_over_ceiling(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=11.0, ceiling=10.0))
        # Federation-level may be hard stop, but also check per-instance
        ok, reason = aggregator.check_spawn_allowed("i1")
        assert not ok

    def test_blocked_spawn_restricted(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=8.5, ceiling=10.0))
        ok, reason = aggregator.check_spawn_allowed("i1")
        assert not ok
        assert "85%" in reason


class TestInstanceManagement:
    def test_remove_instance(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=9.5, ceiling=10.0))
        assert aggregator.current_threshold == CostThreshold.SPAWN_GATED
        aggregator.remove_instance("i1")
        assert aggregator.current_threshold == CostThreshold.NORMAL

    def test_get_instance(self, aggregator):
        aggregator.update_instance(_cost("i1", actual=5.0, ceiling=10.0))
        data = aggregator.get_instance_data("i1")
        assert data is not None
        assert data.actual_today_usd == 5.0

    def test_get_all(self, aggregator):
        aggregator.update_instance(_cost("i1"))
        aggregator.update_instance(_cost("i2"))
        assert len(aggregator.get_all_instances()) == 2

    def test_instance_to_dict(self):
        data = _cost("i1", actual=5.0, projected=7.0, ceiling=10.0)
        d = data.to_dict()
        assert d["utilization_pct"] == 50.0
        assert d["projected_utilization_pct"] == 70.0


class TestFreshness:
    def test_stale_remote_snapshot_blocks_spawn(self):
        aggregator = FederatedCostAggregator(stale_after_s=1.0)
        aggregator.update_instance(
            InstanceCostData(
                instance_id="peer-1",
                actual_today_usd=1.0,
                daily_ceiling_usd=10.0,
                updated_at="2000-01-01T00:00:00+00:00",
            )
        )
        ok, reason = aggregator.check_spawn_allowed("self-1")
        assert not ok
        assert "stale" in reason.lower()
        assert "peer-1" in reason

    def test_stale_remote_snapshot_excluded_from_threshold_math(self):
        aggregator = FederatedCostAggregator(stale_after_s=1.0)
        aggregator.update_instance(
            InstanceCostData(
                instance_id="peer-1",
                actual_today_usd=9.6,
                daily_ceiling_usd=10.0,
                updated_at="2000-01-01T00:00:00+00:00",
            )
        )
        assert aggregator.current_threshold == CostThreshold.NORMAL
        assert aggregator.get_aggregate().utilization_pct == 0.0
        assert aggregator.get_stale_instance_ids() == ["peer-1"]


class TestPersistence:
    def test_threshold_survives_restart(self, tmp_path):
        path = tmp_path / "cost_aggregate.json"
        aggregator = FederatedCostAggregator(persistence_path=str(path))
        aggregator.update_instance(_cost("i1", actual=9.6, ceiling=10.0))

        reloaded = FederatedCostAggregator(persistence_path=str(path))
        assert reloaded.current_threshold == CostThreshold.SPAWN_GATED
        assert reloaded.get_aggregate().utilization_pct == 96.0

    def test_remove_instance_persists_cleanup(self, tmp_path):
        path = tmp_path / "cost_aggregate.json"
        aggregator = FederatedCostAggregator(persistence_path=str(path))
        aggregator.update_instance(_cost("i1", actual=9.6, ceiling=10.0))
        assert aggregator.remove_instance("i1")

        reloaded = FederatedCostAggregator(persistence_path=str(path))
        assert reloaded.current_threshold == CostThreshold.NORMAL
        assert reloaded.get_aggregate().utilization_pct == 0.0
