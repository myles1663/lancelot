"""
Tests for P71: Pattern → AutomationRule Proposal Generation.
"""

import uuid
import pytest

from src.core.governance.approval_learning.config import APLConfig, DetectionConfig
from src.core.governance.approval_learning.models import ApprovalPattern, AutomationRule
from src.core.governance.approval_learning.pattern_detector import PatternDetector


def _make_config() -> APLConfig:
    return APLConfig(
        detection=DetectionConfig(min_observations=20, confidence_threshold=0.85),
        never_automate=[
            "connector.stripe.charge_customer",
            "connector.stripe.refund_charge",
            "connector.*.delete_*",
        ],
    )


def _make_pattern(
    capability=None, target_domain=None, scope=None,
    time_range=None, day_range=None,
    pattern_type="approval", observations=30, consistent=30,
) -> ApprovalPattern:
    return ApprovalPattern(
        id=str(uuid.uuid4()),
        pattern_type=pattern_type,
        capability=capability,
        target_domain=target_domain,
        scope=scope,
        time_range=time_range,
        day_range=day_range,
        total_observations=observations,
        consistent_decisions=consistent,
        first_observed="2026-03-01T10:00:00+00:00",
        last_observed="2026-03-21T10:00:00+00:00",
        avg_decision_time_ms=340.0,
    )


class TestProposalGeneration:
    def test_generates_proposal_with_proposed_status(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(capability="connector.email.send_message")
        proposals = detector.generate_proposals([pattern], config)
        assert len(proposals) == 1
        assert proposals[0].status == "proposed"

    def test_never_automate_skips_pattern(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(capability="connector.stripe.charge_customer")
        proposals = detector.generate_proposals([pattern], config)
        assert len(proposals) == 0

    def test_wildcard_never_automate_blocks_delete(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(capability="connector.email.delete_message")
        proposals = detector.generate_proposals([pattern], config)
        assert len(proposals) == 0

    def test_name_includes_capability(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(
            capability="connector.email.send_message",
            target_domain="client.com",
        )
        proposals = detector.generate_proposals([pattern], config)
        assert "email" in proposals[0].name.lower()

    def test_description_includes_observation_count(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(capability="connector.email.send_message")
        proposals = detector.generate_proposals([pattern], config)
        assert "30" in proposals[0].description

    def test_conditions_only_non_none(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(capability="connector.email.send_message")
        proposals = detector.generate_proposals([pattern], config)
        conds = proposals[0].conditions
        assert "capability" in conds
        assert "target_domain" not in conds

    def test_guardrails_from_config(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(capability="connector.email.send_message")
        proposals = detector.generate_proposals([pattern], config)
        assert proposals[0].max_auto_decisions_per_day == 50
        assert proposals[0].max_auto_decisions_total == 500

    def test_owner_confirmed_false(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(capability="connector.email.send_message")
        proposals = detector.generate_proposals([pattern], config)
        assert proposals[0].owner_confirmed is False

    def test_multiple_patterns_multiple_proposals(self):
        config = _make_config()
        detector = PatternDetector(config)
        p1 = _make_pattern(capability="connector.email.send_message")
        p2 = _make_pattern(capability="connector.slack.post_message")
        proposals = detector.generate_proposals([p1, p2], config)
        assert len(proposals) == 2

    def test_denial_pattern_auto_deny(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(
            capability="connector.email.send_message",
            pattern_type="denial",
        )
        proposals = detector.generate_proposals([pattern], config)
        assert proposals[0].pattern_type == "auto_deny"

    def test_approval_pattern_auto_approve(self):
        config = _make_config()
        detector = PatternDetector(config)
        pattern = _make_pattern(
            capability="connector.email.send_message",
            pattern_type="approval",
        )
        proposals = detector.generate_proposals([pattern], config)
        assert proposals[0].pattern_type == "auto_approve"
