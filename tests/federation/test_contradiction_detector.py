# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Receipt DAG Contradiction Detector."""

import pytest
from datetime import datetime, timezone, timedelta
from src.federation.contradiction_detector import (
    AssumptionCategory,
    ContradictionDetector,
    ContradictionSeverity,
    ContradictionState,
)


@pytest.fixture
def detector():
    return ContradictionDetector()


class TestFactualCheck:
    def test_passes_when_all_keys_present(self, detector):
        result = detector.check_factual(
            "c-1", "quest-1", "src", "tgt",
            "payload must have name and age",
            expected_schema={"name": "str", "age": "int"},
            actual_data={"name": "Alice", "age": 30, "extra": True},
        )
        assert result is None

    def test_detects_missing_keys(self, detector):
        result = detector.check_factual(
            "c-1", "quest-1", "src", "tgt",
            "payload must have name and age",
            expected_schema={"name": "str", "age": "int"},
            actual_data={"name": "Alice"},
        )
        assert result is not None
        assert result.category == AssumptionCategory.FACTUAL
        assert result.severity == ContradictionSeverity.HIGH
        assert "age" in result.description


class TestConstraintCheck:
    def test_passes_in_range(self, detector):
        result = detector.check_constraint(
            "c-1", "quest-1", "src", "tgt",
            "temperature must be 0-100",
            "temperature", 50.0, min_value=0.0, max_value=100.0,
        )
        assert result is None

    def test_detects_below_min(self, detector):
        result = detector.check_constraint(
            "c-1", "quest-1", "src", "tgt",
            "temperature must be 0-100",
            "temperature", -5.0, min_value=0.0,
        )
        assert result is not None
        assert "min" in result.description

    def test_detects_above_max(self, detector):
        result = detector.check_constraint(
            "c-1", "quest-1", "src", "tgt",
            "temperature must be 0-100",
            "temperature", 150.0, max_value=100.0,
        )
        assert result is not None
        assert "max" in result.description


class TestTemporalCheck:
    def test_passes_correct_order(self, detector):
        t1 = datetime.now(timezone.utc).isoformat()
        t2 = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        result = detector.check_temporal(
            "c-1", "quest-1", "src", "tgt",
            "downstream must be after upstream",
            upstream_timestamp=t1,
            downstream_timestamp=t2,
        )
        assert result is None

    def test_detects_reversed_order(self, detector):
        t1 = datetime.now(timezone.utc).isoformat()
        t2 = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        result = detector.check_temporal(
            "c-1", "quest-1", "src", "tgt",
            "downstream must be after upstream",
            upstream_timestamp=t1,
            downstream_timestamp=t2,
        )
        assert result is not None
        assert result.category == AssumptionCategory.TEMPORAL

    def test_bad_timestamp_format(self, detector):
        result = detector.check_temporal(
            "c-1", "quest-1", "src", "tgt",
            "order check",
            upstream_timestamp="not-a-date",
            downstream_timestamp="also-not",
        )
        assert result is not None
        assert result.severity == ContradictionSeverity.LOW


class TestLifecycle:
    def test_acknowledge(self, detector):
        detector.check_factual(
            "c-1", "quest-1", "src", "tgt",
            "check", {"a": 1}, {},
        )
        assert detector.acknowledge("c-1", "admin")
        c = detector.get_contradiction("c-1")
        assert c.state == ContradictionState.ACKNOWLEDGED

    def test_resolve(self, detector):
        detector.check_factual(
            "c-1", "quest-1", "src", "tgt",
            "check", {"a": 1}, {},
        )
        assert detector.resolve("c-1", "admin", "fixed payload")
        c = detector.get_contradiction("c-1")
        assert c.state == ContradictionState.RESOLVED
        assert c.resolved_at is not None

    def test_escalate(self, detector):
        detector.check_factual(
            "c-1", "quest-1", "src", "tgt",
            "check", {"a": 1}, {},
        )
        assert detector.escalate("c-1")
        c = detector.get_contradiction("c-1")
        assert c.state == ContradictionState.ESCALATED
        assert c.severity == ContradictionSeverity.CRITICAL

    def test_resolve_after_escalate_fails(self, detector):
        detector.check_factual("c-1", "quest-1", "src", "tgt", "check", {"a": 1}, {})
        detector.escalate("c-1")
        assert not detector.resolve("c-1", "admin", "fix")

    def test_acknowledge_already_acknowledged(self, detector):
        detector.check_factual("c-1", "quest-1", "src", "tgt", "check", {"a": 1}, {})
        detector.acknowledge("c-1", "admin")
        assert not detector.acknowledge("c-1", "admin")


class TestQueries:
    def test_get_active(self, detector):
        detector.check_factual("c-1", "q-1", "s", "t", "check", {"a": 1}, {})
        detector.check_factual("c-2", "q-1", "s", "t", "check", {"b": 1}, {})
        detector.resolve("c-1", "admin", "fixed")
        assert len(detector.get_active()) == 1

    def test_get_by_quest(self, detector):
        detector.check_factual("c-1", "q-1", "s", "t", "check", {"a": 1}, {})
        detector.check_factual("c-2", "q-2", "s", "t", "check", {"a": 1}, {})
        assert len(detector.get_by_quest("q-1")) == 1

    def test_get_all(self, detector):
        detector.check_factual("c-1", "q-1", "s", "t", "check", {"a": 1}, {})
        assert len(detector.get_all()) == 1

    def test_to_dict(self, detector):
        c = detector.check_factual("c-1", "q-1", "s", "t", "check", {"a": 1}, {})
        d = c.to_dict()
        assert d["contradiction_id"] == "c-1"
        assert d["category"] == "factual"

    def test_callback_fires(self):
        detected = []
        det = ContradictionDetector(on_contradiction=lambda c: detected.append(c))
        det.check_factual("c-1", "q-1", "s", "t", "check", {"a": 1}, {})
        assert len(detected) == 1
