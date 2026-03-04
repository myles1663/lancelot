"""Tests for HIVE Scoped Soul Generator."""

import pytest

from src.core.soul.store import Soul, AutonomyPosture, RiskRule, SchedulingBoundaries
from src.hive.scoped_soul import ScopedSoulGenerator
from src.hive.types import TaskSpec, ControlMethod


def _make_parent_soul() -> Soul:
    """Create a minimal parent Soul for testing."""
    return Soul(
        version="v1",
        mission="Test mission",
        allegiance="Test allegiance",
        autonomy_posture=AutonomyPosture(
            level="governed",
            description="Test posture",
            allowed_autonomous=["classify_intent", "summarize", "health_check"],
            requires_approval=["deploy", "delete", "financial_transaction"],
        ),
        risk_rules=[
            RiskRule(name="no_delete_without_approval", description="Test rule", enforced=True),
            RiskRule(name="no_silent_degradation", description="Test rule 2", enforced=True),
        ],
        tone_invariants=["Be honest"],
        memory_ethics=["No leaks"],
        scheduling_boundaries=SchedulingBoundaries(
            max_concurrent_jobs=5,
            max_job_duration_seconds=300,
            no_autonomous_irreversible=True,
            require_ready_state=True,
            description="Test boundaries",
        ),
    )


class TestScopedSoulGeneration:
    def test_basic_generation(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec(description="Test task")
        scoped = gen.generate(parent, spec)
        assert scoped.version == parent.version
        assert scoped.mission == parent.mission
        assert scoped.allegiance == parent.allegiance

    def test_parent_risk_rules_preserved(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        scoped = gen.generate(parent, spec)
        parent_rule_names = {r.name for r in parent.risk_rules}
        scoped_rule_names = {r.name for r in scoped.risk_rules}
        assert parent_rule_names.issubset(scoped_rule_names)

    def test_adds_hive_specific_rule(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        scoped = gen.generate(parent, spec)
        scoped_rule_names = {r.name for r in scoped.risk_rules}
        hive_rules = [n for n in scoped_rule_names if n.startswith("hive_scoped_")]
        assert len(hive_rules) == 1

    def test_extra_risk_rules_added(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        extra = [RiskRule(name="custom_rule", description="Custom", enforced=True)]
        scoped = gen.generate(parent, spec, extra_risk_rules=extra)
        scoped_rule_names = {r.name for r in scoped.risk_rules}
        assert "custom_rule" in scoped_rule_names

    def test_manual_confirm_moves_all_to_requires_approval(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec(control_method=ControlMethod.MANUAL_CONFIRM)
        scoped = gen.generate(parent, spec)
        assert len(scoped.autonomy_posture.allowed_autonomous) == 0
        assert len(scoped.autonomy_posture.requires_approval) > 0

    def test_allowed_categories_filter(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec(allowed_categories=["health"])
        scoped = gen.generate(parent, spec)
        # Only "health_check" should remain
        assert "health_check" in scoped.autonomy_posture.allowed_autonomous
        assert "classify_intent" not in scoped.autonomy_posture.allowed_autonomous

    def test_scheduling_boundaries_tightened(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec(timeout_seconds=60)
        scoped = gen.generate(parent, spec)
        assert scoped.scheduling_boundaries.max_concurrent_jobs == 1
        assert scoped.scheduling_boundaries.max_job_duration_seconds <= 60
        assert scoped.scheduling_boundaries.no_autonomous_irreversible is True

    def test_tone_invariants_preserved(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        scoped = gen.generate(parent, spec)
        assert parent.tone_invariants == scoped.tone_invariants

    def test_memory_ethics_preserved(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        scoped = gen.generate(parent, spec)
        assert parent.memory_ethics == scoped.memory_ethics


class TestValidateMoreRestrictive:
    def test_valid_scoped_soul(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        scoped = gen.generate(parent, spec)
        assert gen.validate_more_restrictive(scoped, parent) is True

    def test_missing_parent_rules_fails(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        # Create a scoped soul that's missing a parent rule
        scoped = Soul(
            version="v1",
            mission="Test mission",
            allegiance="Test allegiance",
            autonomy_posture=AutonomyPosture(
                level="scoped",
                description="Test",
                allowed_autonomous=[],
                requires_approval=[],
            ),
            risk_rules=[],  # Missing parent rules!
            scheduling_boundaries=SchedulingBoundaries(
                max_concurrent_jobs=1,
                max_job_duration_seconds=60,
            ),
        )
        assert gen.validate_more_restrictive(scoped, parent) is False

    def test_new_allowed_autonomous_fails(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        scoped = gen.generate(parent, spec)
        # Manually add a new allowed action not in parent
        scoped.autonomy_posture.allowed_autonomous.append("new_dangerous_action")
        assert gen.validate_more_restrictive(scoped, parent) is False

    def test_loosened_scheduling_fails(self):
        gen = ScopedSoulGenerator()
        parent = _make_parent_soul()
        spec = TaskSpec()
        scoped = gen.generate(parent, spec)
        # Loosen scheduling beyond parent
        scoped.scheduling_boundaries.max_job_duration_seconds = 9999
        assert gen.validate_more_restrictive(scoped, parent) is False


class TestHashSoul:
    def test_deterministic_hash(self):
        parent = _make_parent_soul()
        h1 = ScopedSoulGenerator.hash_soul(parent)
        h2 = ScopedSoulGenerator.hash_soul(parent)
        assert h1 == h2

    def test_different_souls_different_hash(self):
        s1 = _make_parent_soul()
        s2 = Soul(
            version="v1",
            mission="Different mission",
            allegiance="Different allegiance",
            autonomy_posture=AutonomyPosture(
                level="governed",
                description="Different",
            ),
        )
        h1 = ScopedSoulGenerator.hash_soul(s1)
        h2 = ScopedSoulGenerator.hash_soul(s2)
        assert h1 != h2

    def test_hash_is_16_chars(self):
        parent = _make_parent_soul()
        h = ScopedSoulGenerator.hash_soul(parent)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)
