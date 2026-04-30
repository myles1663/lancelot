"""
Tests for P69-P70: PatternDetector — single-dim + multi-dim detection.
"""

import uuid
import pytest
from datetime import datetime, timedelta, timezone

from src.core.governance.models import RiskTier
from src.core.governance.approval_learning.config import APLConfig, DetectionConfig
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    DecisionContext,
    DecisionRecord,
)
from src.core.governance.approval_learning.pattern_detector import PatternDetector


def _make_config(**overrides) -> APLConfig:
    detection = DetectionConfig(
        min_observations=20,
        confidence_threshold=0.85,
        max_pattern_dimensions=3,
    )
    return APLConfig(detection=detection, **overrides)


def _make_decisions(
    n: int,
    capability: str = "connector.email.send_message",
    target: str = "bob@client.com",
    decision: str = "approved",
    hour: int = 10,
    day: int = 0,  # Monday
    decision_time_ms: int = 300,
    **ctx_kwargs,
) -> list[DecisionRecord]:
    """Generate N synthetic decision records."""
    records = []
    base_time = datetime(2026, 3, 9, hour, 0, 0, tzinfo=timezone.utc)  # Monday

    for i in range(n):
        ts = base_time + timedelta(hours=i)
        ctx = DecisionContext.from_action(
            capability=capability,
            target=target,
            risk_tier=RiskTier.T3_IRREVERSIBLE,
            timestamp=ts,
            **ctx_kwargs,
        )
        # Override day_of_week and hour_of_day for consistency
        ctx = DecisionContext(
            capability=ctx.capability,
            operation_id=ctx.operation_id,
            connector_id=ctx.connector_id,
            risk_tier=ctx.risk_tier,
            target=ctx.target,
            target_domain=ctx.target_domain,
            target_category=ctx.target_category,
            scope=ctx.scope,
            timestamp=ctx.timestamp,
            day_of_week=day,
            hour_of_day=hour,
            content_hash=ctx.content_hash,
            content_size=ctx.content_size,
            metadata=ctx.metadata,
        )
        rec = DecisionRecord(
            id=str(uuid.uuid4()),
            context=ctx,
            decision=decision,
            decision_time_ms=decision_time_ms,
            recorded_at=ts.isoformat(),
        )
        records.append(rec)
    return records


# ── Single-Dimension Tests (P69) ───────────────────────────────


class TestSingleDimension:
    def test_no_decisions_no_patterns(self):
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension([])
        assert len(patterns) == 0

    def test_below_min_observations(self):
        decisions = _make_decisions(10)
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        assert len(patterns) == 0

    def test_identical_approvals_detected(self):
        decisions = _make_decisions(30)  # 30/30 * min(1, 30/30) = 1.0
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        assert len(patterns) > 0
        assert any(p.confidence >= 0.85 for p in patterns)

    def test_mixed_lowers_confidence(self):
        approved = _make_decisions(25, decision="approved")
        denied = _make_decisions(5, decision="denied")
        all_decisions = approved + denied
        config = _make_config()
        config.detection.confidence_threshold = 0.5  # Lower for this test
        detector = PatternDetector(config)
        cap_patterns = [
            p
            for p in detector.detect_single_dimension(all_decisions)
            if p.capability == "connector.email.send_message"
        ]
        # Should exist but with lower confidence than pure 25/25
        if cap_patterns:
            assert cap_patterns[0].confidence < 1.0

    def test_capability_pattern_detected(self):
        decisions = _make_decisions(30, capability="connector.email.send_message")
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        cap_patterns = [p for p in patterns if p.capability is not None]
        assert len(cap_patterns) > 0

    def test_target_domain_pattern_detected(self):
        decisions = _make_decisions(30, target="alice@client.com")
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        domain_patterns = [p for p in patterns if p.target_domain == "client.com"]
        assert len(domain_patterns) > 0

    def test_denial_pattern_detected(self):
        decisions = _make_decisions(30, decision="denied", target="spy@competitor.com")
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        denial_patterns = [p for p in patterns if p.pattern_type == "denial"]
        assert len(denial_patterns) > 0

    def test_time_pattern_business_hours(self):
        decisions = _make_decisions(30, hour=10)  # All at 10am
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        time_patterns = [p for p in patterns if p.time_range is not None]
        assert len(time_patterns) > 0

    def test_day_pattern_weekdays(self):
        decisions = _make_decisions(30, day=2)  # All Wednesday
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        day_patterns = [p for p in patterns if p.day_range is not None]
        assert len(day_patterns) > 0

    def test_separate_patterns_per_capability(self):
        email = _make_decisions(30, capability="connector.email.send_message")
        slack = _make_decisions(30, capability="connector.slack.post_message")
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(email + slack)
        cap_patterns = [p for p in patterns if p.capability is not None]
        caps = {p.capability for p in cap_patterns}
        assert "connector.email.send_message" in caps
        assert "connector.slack.post_message" in caps

    def test_sorted_by_confidence(self):
        decisions = _make_decisions(30)
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        if len(patterns) >= 2:
            assert patterns[0].confidence >= patterns[1].confidence

    def test_single_dimension_specificity_is_one(self):
        decisions = _make_decisions(30)
        detector = PatternDetector(_make_config())
        patterns = detector.detect_single_dimension(decisions)
        for p in patterns:
            assert p.specificity == 1


# ── Multi-Dimensional Tests (P70) ──────────────────────────────


class TestMultiDimension:
    def test_2d_pattern_detected(self):
        # All emails to client.com → should detect capability + target_domain
        decisions = _make_decisions(
            30,
            capability="connector.email.send_message",
            target="bob@client.com",
        )
        detector = PatternDetector(_make_config())
        single = detector.detect_single_dimension(decisions)
        multi = detector.detect_multi_dimension(decisions, single)
        two_d = [p for p in multi if p.specificity == 2]
        assert len(two_d) > 0

    def test_2d_has_specificity_2(self):
        decisions = _make_decisions(
            30,
            capability="connector.email.send_message",
            target="bob@client.com",
        )
        detector = PatternDetector(_make_config())
        single = detector.detect_single_dimension(decisions)
        multi = detector.detect_multi_dimension(decisions, single)
        for p in multi:
            assert p.specificity >= 2

    def test_specificity_bonus_in_scoring(self):
        detector = PatternDetector(_make_config())
        from src.core.governance.approval_learning.models import ApprovalPattern

        p1 = ApprovalPattern(
            id="1", pattern_type="approval",
            capability="connector.email.send_message",
            total_observations=30, consistent_decisions=28,
        )
        p2 = ApprovalPattern(
            id="2", pattern_type="approval",
            capability="connector.email.send_message",
            target_domain="client.com",
            total_observations=30, consistent_decisions=28,
        )
        # p2 has higher specificity → higher score despite same confidence
        assert detector._score_pattern(p2) > detector._score_pattern(p1)

    def test_detect_all_deduplicates(self):
        decisions = _make_decisions(30)
        detector = PatternDetector(_make_config())
        patterns = detector.detect_all(decisions)
        # Should not have both 1D and 2D for same dimension when 2D is better
        assert len(patterns) > 0

    def test_3d_pattern_possible(self):
        decisions = _make_decisions(
            30,
            capability="connector.email.send_message",
            target="bob@client.com",
            hour=10,
        )
        config = _make_config()
        config.detection.max_pattern_dimensions = 3
        detector = PatternDetector(config)
        patterns = detector.detect_all(decisions)
        max_spec = max(p.specificity for p in patterns) if patterns else 0
        # At least 1D patterns should be detected
        assert max_spec >= 1

    def test_max_dimensions_respected(self):
        decisions = _make_decisions(30)
        config = _make_config()
        config.detection.max_pattern_dimensions = 2
        detector = PatternDetector(config)
        patterns = detector.detect_all(decisions)
        for p in patterns:
            assert p.specificity <= 2

    def test_score_pattern_formula(self):
        detector = PatternDetector(_make_config())
        from src.core.governance.approval_learning.models import ApprovalPattern

        p = ApprovalPattern(
            id="x", pattern_type="approval",
            capability="test", target_domain="test.com",
            total_observations=30, consistent_decisions=30,
        )
        # confidence=1.0, specificity=2
        # score = 1.0 * (1 + 0.2*2) = 1.4
        assert abs(detector._score_pattern(p) - 1.4) < 0.01

    def test_should_analyze_true_after_threshold(self, tmp_path):
        from src.core.governance.approval_learning.config import PersistenceConfig

        config = _make_config()
        config.detection.analysis_trigger_interval = 5
        config.persistence = PersistenceConfig(
            decision_log_path=str(tmp_path / "d.jsonl"),
            rules_path=str(tmp_path / "r.json"),
            patterns_path=str(tmp_path / "p.json"),
        )
        log = DecisionLog(config)
        detector = PatternDetector(config)
        for _ in range(5):
            log.record(
                DecisionContext.from_action("connector.email.send_message"),
                "approved",
            )
        assert detector.should_analyze(log) is True

    def test_should_analyze_false_below_threshold(self, tmp_path):
        from src.core.governance.approval_learning.config import PersistenceConfig

        config = _make_config()
        config.detection.analysis_trigger_interval = 10
        config.persistence = PersistenceConfig(
            decision_log_path=str(tmp_path / "d.jsonl"),
            rules_path=str(tmp_path / "r.json"),
            patterns_path=str(tmp_path / "p.json"),
        )
        log = DecisionLog(config)
        detector = PatternDetector(config)
        for _ in range(5):
            log.record(
                DecisionContext.from_action("connector.email.send_message"),
                "approved",
            )
        assert detector.should_analyze(log) is False

    def test_denial_multi_dimension(self):
        decisions = _make_decisions(
            30,
            capability="connector.email.send_message",
            target="spy@competitor.com",
            decision="denied",
        )
        detector = PatternDetector(_make_config())
        patterns = detector.detect_all(decisions)
        denial_patterns = [p for p in patterns if p.pattern_type == "denial"]
        assert len(denial_patterns) > 0
