# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Soul compatibility — intersection, monotonic narrowing, classification."""

import pytest
from src.core.soul.store import (
    ApprovalRules,
    AutonomyPosture,
    RiskRule,
    SchedulingBoundaries,
    Soul,
    SpawnBudgetGovernance,
)
from src.federation.soul_compat import (
    CompatibilityLevel,
    classify_compatibility,
    compute_soul_intersection,
    hash_soul,
    validate_more_restrictive,
)


def _make_soul(**overrides) -> Soul:
    """Helper to create a Soul with sensible defaults."""
    defaults = dict(
        version="v1",
        mission="Test mission",
        allegiance="Test allegiance",
        autonomy_posture=AutonomyPosture(
            level="supervised",
            description="test",
            allowed_autonomous=["classify", "summarize", "health_check"],
            requires_approval=["deploy", "delete"],
        ),
        risk_rules=[
            RiskRule(name="destructive_actions_require_approval", description="test"),
        ],
        approval_rules=ApprovalRules(
            default_timeout_seconds=3600,
            channels=["war_room"],
        ),
        tone_invariants=["Never mislead", "Report failures immediately"],
        memory_ethics=["No PII without consent"],
        scheduling_boundaries=SchedulingBoundaries(
            max_concurrent_jobs=5,
            max_job_duration_seconds=300,
        ),
        spawn_budget=SpawnBudgetGovernance(
            max_concurrent_spawns=10,
            max_spawn_model_tier="T2",
        ),
    )
    defaults.update(overrides)
    return Soul(**defaults)


class TestHashSoul:
    def test_deterministic(self):
        soul = _make_soul()
        h1 = hash_soul(soul)
        h2 = hash_soul(soul)
        assert h1 == h2
        assert len(h1) == 16

    def test_different_souls_different_hashes(self):
        s1 = _make_soul(mission="Mission A")
        s2 = _make_soul(mission="Mission B")
        assert hash_soul(s1) != hash_soul(s2)


class TestComputeSoulIntersection:
    def test_identical_souls(self):
        soul = _make_soul()
        result = compute_soul_intersection(soul, soul)
        assert set(result.autonomy_posture.allowed_autonomous) == {"classify", "summarize", "health_check"}
        assert "deploy" in result.autonomy_posture.requires_approval
        assert "delete" in result.autonomy_posture.requires_approval

    def test_allowed_autonomous_intersection(self):
        s1 = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["classify", "summarize"],
            requires_approval=["deploy"],
        ))
        s2 = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["summarize", "health_check"],
            requires_approval=["delete"],
        ))
        result = compute_soul_intersection(s1, s2)
        # Only "summarize" is in both
        assert result.autonomy_posture.allowed_autonomous == ["summarize"]
        # Union of requires_approval
        assert "delete" in result.autonomy_posture.requires_approval
        assert "deploy" in result.autonomy_posture.requires_approval

    def test_risk_rules_union(self):
        s1 = _make_soul(risk_rules=[
            RiskRule(name="rule_a", description="a"),
        ])
        s2 = _make_soul(risk_rules=[
            RiskRule(name="rule_b", description="b"),
        ])
        result = compute_soul_intersection(s1, s2)
        names = {r.name for r in result.risk_rules}
        assert "rule_a" in names
        assert "rule_b" in names

    def test_scheduling_takes_tighter(self):
        s1 = _make_soul(scheduling_boundaries=SchedulingBoundaries(
            max_concurrent_jobs=5, max_job_duration_seconds=300,
        ))
        s2 = _make_soul(scheduling_boundaries=SchedulingBoundaries(
            max_concurrent_jobs=3, max_job_duration_seconds=600,
        ))
        result = compute_soul_intersection(s1, s2)
        assert result.scheduling_boundaries.max_concurrent_jobs == 3
        assert result.scheduling_boundaries.max_job_duration_seconds == 300

    def test_spawn_budget_takes_tighter(self):
        s1 = _make_soul(spawn_budget=SpawnBudgetGovernance(
            max_concurrent_spawns=10, max_spawn_model_tier="T2",
        ))
        s2 = _make_soul(spawn_budget=SpawnBudgetGovernance(
            max_concurrent_spawns=5, max_spawn_model_tier="T1",
        ))
        result = compute_soul_intersection(s1, s2)
        assert result.spawn_budget.max_concurrent_spawns == 5
        assert result.spawn_budget.max_spawn_model_tier == "T1"

    def test_tone_invariants_union(self):
        s1 = _make_soul(tone_invariants=["Rule A", "Rule B"])
        s2 = _make_soul(tone_invariants=["Rule B", "Rule C"])
        result = compute_soul_intersection(s1, s2)
        assert set(result.tone_invariants) == {"Rule A", "Rule B", "Rule C"}

    def test_approval_timeout_takes_min(self):
        s1 = _make_soul(approval_rules=ApprovalRules(
            default_timeout_seconds=3600, channels=["war_room"],
        ))
        s2 = _make_soul(approval_rules=ApprovalRules(
            default_timeout_seconds=1800, channels=["chat"],
        ))
        result = compute_soul_intersection(s1, s2)
        assert result.approval_rules.default_timeout_seconds == 1800
        assert "war_room" in result.approval_rules.channels
        assert "chat" in result.approval_rules.channels

    def test_no_autonomous_irreversible_or(self):
        s1 = _make_soul(scheduling_boundaries=SchedulingBoundaries(
            no_autonomous_irreversible=False,
        ))
        s2 = _make_soul(scheduling_boundaries=SchedulingBoundaries(
            no_autonomous_irreversible=True,
        ))
        result = compute_soul_intersection(s1, s2)
        assert result.scheduling_boundaries.no_autonomous_irreversible is True

    def test_intersection_version_tag(self):
        result = compute_soul_intersection(_make_soul(), _make_soul())
        assert result.version == "intersection"

    def test_mission_from_receiving(self):
        s1 = _make_soul(mission="Receiving mission")
        s2 = _make_soul(mission="Context mission")
        result = compute_soul_intersection(s1, s2)
        assert result.mission == "Receiving mission"


class TestValidateMoreRestrictive:
    def test_identical_souls_pass(self):
        soul = _make_soul()
        valid, violations = validate_more_restrictive(soul, soul)
        assert valid
        assert violations == []

    def test_intersection_is_valid(self):
        s1 = _make_soul()
        s2 = _make_soul()
        intersection = compute_soul_intersection(s1, s2)
        valid, violations = validate_more_restrictive(intersection, s1)
        assert valid, f"Violations: {violations}"
        valid2, violations2 = validate_more_restrictive(intersection, s2)
        assert valid2, f"Violations: {violations2}"

    def test_extra_allowed_autonomous_fails(self):
        parent = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["classify"],
            requires_approval=["deploy"],
        ))
        child = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["classify", "extra_thing"],
            requires_approval=["deploy"],
        ))
        valid, violations = validate_more_restrictive(child, parent)
        assert not valid
        assert any("allowed_autonomous" in v for v in violations)

    def test_missing_risk_rule_fails(self):
        parent = _make_soul(risk_rules=[
            RiskRule(name="rule_a", description="a"),
            RiskRule(name="rule_b", description="b"),
        ])
        child = _make_soul(risk_rules=[
            RiskRule(name="rule_a", description="a"),
        ])
        valid, violations = validate_more_restrictive(child, parent)
        assert not valid
        assert any("risk_rules" in v for v in violations)

    def test_relaxed_concurrent_jobs_fails(self):
        parent = _make_soul(scheduling_boundaries=SchedulingBoundaries(
            max_concurrent_jobs=3,
        ))
        child = _make_soul(scheduling_boundaries=SchedulingBoundaries(
            max_concurrent_jobs=5,
        ))
        valid, violations = validate_more_restrictive(child, parent)
        assert not valid

    def test_relaxed_spawn_tier_fails(self):
        parent = _make_soul(spawn_budget=SpawnBudgetGovernance(
            max_spawn_model_tier="T1",
        ))
        child = _make_soul(spawn_budget=SpawnBudgetGovernance(
            max_spawn_model_tier="T2",
        ))
        valid, violations = validate_more_restrictive(child, parent)
        assert not valid


class TestClassifyCompatibility:
    def test_identical_souls_green(self):
        soul = _make_soul()
        level, notes = classify_compatibility(soul, soul)
        assert level == CompatibilityLevel.GREEN

    def test_different_mission_red(self):
        s1 = _make_soul(mission="Mission A")
        s2 = _make_soul(mission="Mission B")
        level, notes = classify_compatibility(s1, s2)
        assert level == CompatibilityLevel.RED

    def test_autonomy_conflict_yellow(self):
        s1 = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["classify", "deploy"],  # deploy is autonomous in source
            requires_approval=[],
        ))
        s2 = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["classify"],
            requires_approval=["deploy"],  # deploy requires approval in target
        ))
        level, notes = classify_compatibility(s1, s2)
        assert level == CompatibilityLevel.YELLOW

    def test_reduced_autonomy_yellow(self):
        s1 = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["classify", "summarize", "health_check"],
            requires_approval=["deploy"],
        ))
        s2 = _make_soul(autonomy_posture=AutonomyPosture(
            level="supervised", description="t",
            allowed_autonomous=["classify"],
            requires_approval=["deploy"],
        ))
        level, notes = classify_compatibility(s1, s2)
        assert level == CompatibilityLevel.YELLOW
