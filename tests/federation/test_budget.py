# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation budget tracking and spawn governance."""

import pytest
from src.federation.budget import (
    BudgetThreshold,
    FederationBudgetTracker,
    SpawnDecision,
    SpawnRecord,
)


class TestSpawnDecisionChecks:
    @pytest.fixture
    def tracker(self):
        return FederationBudgetTracker(
            max_concurrent_spawns=3,
            max_spawn_model_tier="T2",
            max_estimated_tokens_per_spawn=50000,
            budget_warning_pct=80.0,
            budget_critical_pct=95.0,
        )

    def test_allowed_spawn(self, tracker):
        decision, reason = tracker.check_spawn(model_tier="T1", estimated_tokens=10000)
        assert decision == SpawnDecision.ALLOWED

    def test_blocked_tier_too_high(self, tracker):
        decision, reason = tracker.check_spawn(model_tier="T3")
        assert decision == SpawnDecision.BLOCKED
        assert "tier" in reason.lower()

    def test_blocked_max_concurrent(self, tracker):
        for i in range(3):
            tracker.record_spawn(f"agent-{i}", "inst-1", "T1", 1000)
        decision, reason = tracker.check_spawn(model_tier="T1")
        assert decision == SpawnDecision.BLOCKED
        assert "maximum" in reason.lower()

    def test_blocked_tokens_too_high(self, tracker):
        decision, reason = tracker.check_spawn(
            model_tier="T1", estimated_tokens=60000,
        )
        assert decision == SpawnDecision.BLOCKED
        assert "token" in reason.lower()

    def test_collapse_frees_slot(self, tracker):
        tracker.record_spawn("agent-0", "inst-1", "T1", 1000)
        tracker.record_spawn("agent-1", "inst-1", "T1", 1000)
        tracker.record_spawn("agent-2", "inst-1", "T1", 1000)
        # All slots full
        decision, _ = tracker.check_spawn(model_tier="T1")
        assert decision == SpawnDecision.BLOCKED
        # Collapse one
        tracker.record_collapse("agent-0")
        decision, _ = tracker.check_spawn(model_tier="T1")
        assert decision == SpawnDecision.ALLOWED

    def test_tier_t0_always_allowed(self, tracker):
        decision, _ = tracker.check_spawn(model_tier="T0")
        assert decision == SpawnDecision.ALLOWED

    def test_tier_at_ceiling_allowed(self, tracker):
        decision, _ = tracker.check_spawn(model_tier="T2")
        assert decision == SpawnDecision.ALLOWED


class TestBudgetThresholds:
    def test_normal_threshold(self):
        tracker = FederationBudgetTracker(
            max_concurrent_spawns=10,
            max_estimated_tokens_per_spawn=100000,
        )
        assert tracker.threshold_level == BudgetThreshold.NORMAL
        assert tracker.utilization_pct == 0.0

    def test_warning_threshold(self):
        tracker = FederationBudgetTracker(
            max_concurrent_spawns=10,
            max_estimated_tokens_per_spawn=100000,
            budget_warning_pct=80.0,
        )
        # Use 85% of budget
        tracker.record_spawn("a1", "i1", "T1", 850000)
        assert tracker.threshold_level == BudgetThreshold.WARNING
        decision, _ = tracker.check_spawn(model_tier="T1", estimated_tokens=1000)
        assert decision == SpawnDecision.RESTRICTED

    def test_critical_threshold_blocks(self):
        tracker = FederationBudgetTracker(
            max_concurrent_spawns=10,
            max_estimated_tokens_per_spawn=100000,
            budget_critical_pct=95.0,
        )
        # Use 96% of budget
        tracker.record_spawn("a1", "i1", "T1", 960000)
        assert tracker.threshold_level == BudgetThreshold.CRITICAL
        decision, _ = tracker.check_spawn(model_tier="T1")
        assert decision == SpawnDecision.BLOCKED

    def test_exceeded_threshold(self):
        tracker = FederationBudgetTracker(
            max_concurrent_spawns=10,
            max_estimated_tokens_per_spawn=100000,
        )
        tracker.record_spawn("a1", "i1", "T1", 1000001)
        assert tracker.threshold_level == BudgetThreshold.EXCEEDED


class TestSpawnRecording:
    def test_record_and_count(self):
        tracker = FederationBudgetTracker(max_concurrent_spawns=5)
        tracker.record_spawn("agent-1", "inst-1", "T1", 10000)
        tracker.record_spawn("agent-2", "inst-1", "T2", 20000)
        assert tracker.active_spawn_count == 2

    def test_collapse_deactivates(self):
        tracker = FederationBudgetTracker(max_concurrent_spawns=5)
        tracker.record_spawn("agent-1", "inst-1", "T1", 10000)
        assert tracker.active_spawn_count == 1
        tracker.record_collapse("agent-1", actual_tokens=8000)
        assert tracker.active_spawn_count == 0

    def test_collapse_unknown_returns_false(self):
        tracker = FederationBudgetTracker()
        assert not tracker.record_collapse("nonexistent")

    def test_collapse_adjusts_tokens(self):
        tracker = FederationBudgetTracker(
            max_concurrent_spawns=10,
            max_estimated_tokens_per_spawn=100000,
        )
        tracker.record_spawn("agent-1", "inst-1", "T1", 50000)
        initial_pct = tracker.utilization_pct
        tracker.record_collapse("agent-1", actual_tokens=30000)
        # Should have reduced by the difference (50000 - 30000 = 20000)
        assert tracker.utilization_pct < initial_pct


class TestBudgetSnapshot:
    def test_snapshot_fields(self):
        tracker = FederationBudgetTracker(
            max_concurrent_spawns=5,
            max_spawn_model_tier="T2",
            budget_warning_pct=80.0,
            budget_critical_pct=95.0,
        )
        tracker.record_spawn("agent-1", "inst-1", "T1", 10000)
        snap = tracker.get_snapshot()
        assert snap["active_spawns"] == 1
        assert snap["max_concurrent_spawns"] == 5
        assert snap["max_spawn_model_tier"] == "T2"
        assert snap["threshold_level"] == "normal"
        assert "utilization_pct" in snap
        assert "total_tokens_used" in snap
