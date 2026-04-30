"""
Tests for P66: APL Data Models — DecisionContext, DecisionRecord,
ApprovalPattern, AutomationRule, RuleCheckResult.
"""

import pytest
from datetime import datetime, timezone

from src.core.governance.models import RiskTier
from src.core.governance.approval_learning.models import (
    DecisionContext,
    DecisionRecord,
    ApprovalPattern,
    AutomationRule,
    RuleCheckResult,
)


# ── DecisionContext ─────────────────────────────────────────────


class TestDecisionContext:
    def test_from_action_extracts_domain(self):
        ctx = DecisionContext.from_action(
            "connector.email.send_message",
            target="bob@client.com",
        )
        assert ctx.target_domain == "client.com"

    def test_from_action_fills_temporal(self):
        ctx = DecisionContext.from_action("connector.email.send_message")
        assert ctx.timestamp != ""
        assert 0 <= ctx.day_of_week <= 6
        assert 0 <= ctx.hour_of_day <= 23

    def test_from_action_extracts_connector_and_operation(self):
        ctx = DecisionContext.from_action("connector.slack.post_message")
        assert ctx.connector_id == "slack"
        assert ctx.operation_id == "post_message"

    def test_frozen(self):
        ctx = DecisionContext.from_action("connector.email.send_message")
        with pytest.raises(AttributeError):
            ctx.capability = "changed"

    def test_from_action_with_explicit_timestamp(self):
        ts = datetime(2026, 3, 9, 14, 30, 0, tzinfo=timezone.utc)  # Monday
        ctx = DecisionContext.from_action(
            "connector.email.send_message",
            target="alice@example.com",
            timestamp=ts,
        )
        assert ctx.day_of_week == 0  # Monday
        assert ctx.hour_of_day == 14


# ── DecisionRecord ──────────────────────────────────────────────


class TestDecisionRecord:
    def test_is_auto_true(self):
        ctx = DecisionContext.from_action("connector.email.send_message")
        rec = DecisionRecord(
            id="r1", context=ctx, decision="approved", rule_id="rule-1"
        )
        assert rec.is_auto is True

    def test_is_auto_false(self):
        ctx = DecisionContext.from_action("connector.email.send_message")
        rec = DecisionRecord(id="r2", context=ctx, decision="approved")
        assert rec.is_auto is False

    def test_is_approval_true(self):
        ctx = DecisionContext.from_action("connector.email.send_message")
        rec = DecisionRecord(id="r3", context=ctx, decision="approved")
        assert rec.is_approval is True

    def test_is_approval_false(self):
        ctx = DecisionContext.from_action("connector.email.send_message")
        rec = DecisionRecord(id="r4", context=ctx, decision="denied")
        assert rec.is_approval is False


# ── ApprovalPattern ─────────────────────────────────────────────


class TestApprovalPattern:
    def test_confidence_empty(self):
        p = ApprovalPattern(id="p1", pattern_type="approval")
        assert p.confidence == 0.0

    def test_confidence_20_of_20(self):
        p = ApprovalPattern(
            id="p2", pattern_type="approval",
            total_observations=20, consistent_decisions=20,
        )
        # consistency=1.0, observation_factor=20/30=0.667
        assert abs(p.confidence - (1.0 * 20 / 30)) < 0.01

    def test_confidence_30_of_30(self):
        p = ApprovalPattern(
            id="p3", pattern_type="approval",
            total_observations=30, consistent_decisions=30,
        )
        assert p.confidence == 1.0

    def test_specificity_two_conditions(self):
        p = ApprovalPattern(
            id="p4", pattern_type="approval",
            capability="connector.email.send_message",
            target_domain="client.com",
        )
        assert p.specificity == 2

    def test_matches_capability(self):
        p = ApprovalPattern(
            id="p5", pattern_type="approval",
            capability="connector.email.send_message",
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        assert p.matches(ctx) is True

    def test_matches_capability_wildcard(self):
        p = ApprovalPattern(
            id="p6", pattern_type="approval",
            capability="connector.email.*",
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        assert p.matches(ctx) is True

    def test_matches_time_range_inside(self):
        ts = datetime(2026, 3, 10, 10, 0, 0, tzinfo=timezone.utc)
        p = ApprovalPattern(
            id="p7", pattern_type="approval", time_range=(9, 17),
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", timestamp=ts,
        )
        assert p.matches(ctx) is True

    def test_matches_time_range_outside(self):
        ts = datetime(2026, 3, 10, 22, 0, 0, tzinfo=timezone.utc)
        p = ApprovalPattern(
            id="p8", pattern_type="approval", time_range=(9, 17),
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", timestamp=ts,
        )
        assert p.matches(ctx) is False

    def test_matches_day_range_weekday(self):
        ts = datetime(2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc)  # Monday
        p = ApprovalPattern(
            id="p9", pattern_type="approval", day_range=(0, 4),  # Mon-Fri
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", timestamp=ts,
        )
        assert p.matches(ctx) is True

    def test_matches_day_range_weekend(self):
        ts = datetime(2026, 3, 14, 10, 0, 0, tzinfo=timezone.utc)  # Saturday
        p = ApprovalPattern(
            id="p10", pattern_type="approval", day_range=(0, 4),
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", timestamp=ts,
        )
        assert p.matches(ctx) is False

    def test_matches_all_none(self):
        """All None conditions → matches everything."""
        p = ApprovalPattern(id="p11", pattern_type="approval")
        ctx = DecisionContext.from_action(
            "connector.slack.post_message", target="channel:#general",
        )
        assert p.matches(ctx) is True

    def test_matches_time_range_wraparound(self):
        """Night shift: (22, 6) should match hour 23."""
        ts = datetime(2026, 3, 10, 23, 0, 0, tzinfo=timezone.utc)
        p = ApprovalPattern(
            id="p12", pattern_type="approval", time_range=(22, 6),
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", timestamp=ts,
        )
        assert p.matches(ctx) is True


# ── AutomationRule ──────────────────────────────────────────────


class TestAutomationRule:
    def _make_rule(self, **overrides) -> AutomationRule:
        defaults = dict(
            id="rule-1",
            name="Test Rule",
            description="Test",
            pattern_id="p1",
            pattern_type="auto_approve",
            conditions={"capability": "connector.email.send_message"},
            status="active",
            owner_confirmed=True,
            max_auto_decisions_per_day=50,
            max_auto_decisions_total=500,
            auto_decisions_today=0,
            auto_decisions_total=0,
            last_reset_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        defaults.update(overrides)
        return AutomationRule(**defaults)

    def test_is_active_all_met(self):
        rule = self._make_rule()
        assert rule.is_active is True

    def test_is_active_daily_limit(self):
        rule = self._make_rule(auto_decisions_today=50)
        assert rule.is_active is False

    def test_is_active_total_limit(self):
        rule = self._make_rule(auto_decisions_total=500)
        assert rule.is_active is False

    def test_is_active_not_confirmed(self):
        rule = self._make_rule(owner_confirmed=False)
        assert rule.is_active is False

    def test_is_active_paused(self):
        rule = self._make_rule(status="paused")
        assert rule.is_active is False

    def test_increment_usage(self):
        rule = self._make_rule()
        rule.increment_usage()
        assert rule.auto_decisions_today == 1
        assert rule.auto_decisions_total == 1
        assert rule.last_auto_decision != ""

    def test_increment_resets_daily_on_new_day(self):
        rule = self._make_rule(
            auto_decisions_today=10,
            last_reset_date="2020-01-01",
        )
        rule.increment_usage()
        # Daily should have been reset then incremented
        assert rule.auto_decisions_today == 1

    def test_matches_context(self):
        rule = self._make_rule(
            conditions={"capability": "connector.email.send_message"}
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        assert rule.matches_context(ctx) is True

    def test_matches_context_domain(self):
        rule = self._make_rule(
            conditions={
                "capability": "connector.email.send_message",
                "target_domain": "client.com",
            }
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        assert rule.matches_context(ctx) is True

    def test_matches_context_mismatch(self):
        rule = self._make_rule(
            conditions={"target_domain": "client.com"}
        )
        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@other.com"
        )
        assert rule.matches_context(ctx) is False


# ── RuleCheckResult ─────────────────────────────────────────────


class TestRuleCheckResult:
    def test_auto_approve(self):
        r = RuleCheckResult(
            action="auto_approve", rule_id="rule-1", rule_name="Test"
        )
        assert r.action == "auto_approve"
        assert r.rule_id == "rule-1"

    def test_ask_owner(self):
        r = RuleCheckResult(action="ask_owner")
        assert r.rule_id == ""
