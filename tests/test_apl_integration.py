"""
Tests for P73: RuleEngine ↔ Orchestrator + Soul Integration.
"""

import uuid
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.core.governance.models import RiskTier
from src.core.governance.approval_learning.config import (
    APLConfig,
    DetectionConfig,
    PersistenceConfig,
    RulesConfig,
)
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    AutomationRule,
    DecisionContext,
    DecisionRecord,
    RuleCheckResult,
)
from src.core.governance.approval_learning.pattern_detector import PatternDetector
from src.core.governance.approval_learning.rule_engine import RuleEngine
from src.core.governance.approval_learning.analyzer import APLAnalyzer
from src.core.governance.approval_learning.orchestrator_wiring import (
    ApprovalRecorder,
    build_decision_context,
)


def _make_config(tmp_path, **overrides) -> APLConfig:
    return APLConfig(
        detection=DetectionConfig(
            min_observations=20,
            confidence_threshold=0.85,
            analysis_trigger_interval=10,
        ),
        rules=RulesConfig(**overrides.get("rules_kwargs", {})),
        never_automate=overrides.get("never_automate", []),
        persistence=PersistenceConfig(
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            rules_path=str(tmp_path / "rules.json"),
            patterns_path=str(tmp_path / "patterns.json"),
        ),
    )


class TestAPLIntegration:
    def test_feature_flag_false_unchanged(self):
        from src.core import feature_flags
        assert feature_flags.FEATURE_APPROVAL_LEARNING is False

    def test_no_matching_rule_ask_owner(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        result = engine.check(ctx)
        assert result.action == "ask_owner"

    def test_matching_approve_skips_owner(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        rule = AutomationRule(
            id=str(uuid.uuid4()),
            name="Auto-approve emails",
            description="Test",
            pattern_id="p1",
            pattern_type="auto_approve",
            conditions={"capability": "connector.email.send_message"},
            created_at=datetime.now(timezone.utc).isoformat(),
            last_reset_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)

        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        result = engine.check(ctx)
        assert result.action == "auto_approve"

    def test_matching_deny_blocks(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        rule = AutomationRule(
            id=str(uuid.uuid4()),
            name="Auto-deny",
            description="Test",
            pattern_id="p2",
            pattern_type="auto_deny",
            conditions={"capability": "connector.email.send_message"},
            created_at=datetime.now(timezone.utc).isoformat(),
            last_reset_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)

        ctx = DecisionContext.from_action("connector.email.send_message")
        result = engine.check(ctx)
        assert result.action == "auto_deny"

    def test_auto_approved_emits_receipt_with_rule_id(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        recorder = ApprovalRecorder(log)

        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        rule_check = RuleCheckResult(
            action="auto_approve", rule_id="rule-abc", rule_name="Test"
        )
        recorder.record_auto_decision(ctx, rule_check)

        recent = log.get_recent(1)
        assert recent[0].rule_id == "rule-abc"

    def test_manual_decision_recorded_to_log(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        recorder = ApprovalRecorder(log)

        ctx = DecisionContext.from_action("connector.email.send_message")
        recorder.record_manual_decision(ctx, approved=True, decision_time_ms=450)

        recent = log.get_recent(1)
        assert recent[0].decision == "approved"
        assert recent[0].decision_time_ms == 450

    def test_soul_never_automate_blocks_proposal(self, tmp_path):
        config = _make_config(
            tmp_path, never_automate=["connector.stripe.*"]
        )
        log = DecisionLog(config)
        detector = PatternDetector(config)
        engine = RuleEngine(config, log)
        analyzer = APLAnalyzer(config, log, detector, engine)

        # Simulate 30 Stripe approvals
        for _ in range(30):
            ctx = DecisionContext.from_action("connector.stripe.charge_customer")
            log.record(ctx, "approved")

        proposals = analyzer.maybe_analyze()
        # No proposals for Stripe since it's in never_automate
        assert len(proposals) == 0

    def test_maybe_analyze_triggers_after_threshold(self, tmp_path):
        config = _make_config(tmp_path)
        config.detection.analysis_trigger_interval = 5
        log = DecisionLog(config)
        detector = PatternDetector(config)
        engine = RuleEngine(config, log)
        analyzer = APLAnalyzer(config, log, detector, engine)

        # Add 5 decisions (= trigger interval)
        for _ in range(5):
            ctx = DecisionContext.from_action(
                "connector.email.send_message", target="bob@client.com"
            )
            log.record(ctx, "approved")

        # Should trigger but may not produce proposals (below min_observations=20)
        # The point is that analysis runs
        proposals = analyzer.maybe_analyze()
        assert log.count_since_last_analysis() == 0  # Analysis ran

    def test_maybe_analyze_generates_proposals(self, tmp_path):
        config = _make_config(tmp_path)
        config.detection.analysis_trigger_interval = 5
        config.detection.min_observations = 5
        config.detection.confidence_threshold = 0.80
        log = DecisionLog(config)
        detector = PatternDetector(config)
        engine = RuleEngine(config, log)
        analyzer = APLAnalyzer(config, log, detector, engine)

        # Add enough decisions for a pattern
        for _ in range(30):
            ctx = DecisionContext.from_action(
                "connector.email.send_message", target="bob@client.com"
            )
            log.record(ctx, "approved")

        proposals = analyzer.maybe_analyze()
        assert len(proposals) > 0

    def test_proposals_appear_in_list(self, tmp_path):
        config = _make_config(tmp_path)
        config.detection.analysis_trigger_interval = 5
        config.detection.min_observations = 5
        config.detection.confidence_threshold = 0.80
        log = DecisionLog(config)
        detector = PatternDetector(config)
        engine = RuleEngine(config, log)
        analyzer = APLAnalyzer(config, log, detector, engine)

        for _ in range(30):
            ctx = DecisionContext.from_action(
                "connector.email.send_message", target="bob@client.com"
            )
            log.record(ctx, "approved")

        analyzer.maybe_analyze()
        proposed = engine.list_rules(status="proposed")
        assert len(proposed) > 0

    def test_full_lifecycle(self, tmp_path):
        """Full lifecycle: manual approvals → pattern → proposal → activate → auto-approve."""
        config = _make_config(tmp_path)
        config.detection.analysis_trigger_interval = 5
        config.detection.min_observations = 10
        config.detection.confidence_threshold = 0.80
        log = DecisionLog(config)
        detector = PatternDetector(config)
        engine = RuleEngine(config, log)
        analyzer = APLAnalyzer(config, log, detector, engine)
        recorder = ApprovalRecorder(log)

        # 1. Simulate 30 manual approvals
        for _ in range(30):
            ctx = DecisionContext.from_action(
                "connector.email.send_message", target="bob@client.com"
            )
            recorder.record_manual_decision(ctx, approved=True, decision_time_ms=200)

        # 2. Analysis detects pattern
        proposals = analyzer.maybe_analyze()
        assert len(proposals) > 0

        # 3. Owner activates first proposal
        engine.activate_rule(proposals[0].id)

        # 4. Next matching action → auto-approved
        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        result = engine.check(ctx)
        assert result.action == "auto_approve"

        # 5. Non-matching action → ask_owner
        ctx2 = DecisionContext.from_action(
            "connector.slack.post_message", target="channel:#general"
        )
        result2 = engine.check(ctx2)
        assert result2.action == "ask_owner"
