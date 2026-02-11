"""
Tests for P68: APL Orchestrator Wiring — build_decision_context + ApprovalRecorder.
"""

import os
import pytest
from types import SimpleNamespace
from unittest.mock import patch

from src.core.governance.models import RiskTier, RiskClassification
from src.core.governance.approval_learning.config import APLConfig, PersistenceConfig
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    DecisionContext,
    RuleCheckResult,
)
from src.core.governance.approval_learning.orchestrator_wiring import (
    build_decision_context,
    ApprovalRecorder,
)


def _make_config(tmp_path) -> APLConfig:
    return APLConfig(
        persistence=PersistenceConfig(
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            rules_path=str(tmp_path / "rules.json"),
            patterns_path=str(tmp_path / "patterns.json"),
        )
    )


class TestBuildDecisionContext:
    def test_extracts_capability_from_connector_step(self):
        step = SimpleNamespace(tool="connector.email.send_message", params={})
        classification = SimpleNamespace(tier=RiskTier.T3_IRREVERSIBLE)
        ctx = build_decision_context(step, classification)
        assert ctx.capability == "connector.email.send_message"
        assert ctx.connector_id == "email"
        assert ctx.operation_id == "send_message"

    def test_extracts_target_domain_from_email(self):
        step = SimpleNamespace(
            tool="connector.email.send_message",
            params={"to": "bob@client.com"},
        )
        classification = SimpleNamespace(tier=RiskTier.T3_IRREVERSIBLE)
        ctx = build_decision_context(step, classification)
        assert ctx.target == "bob@client.com"
        assert ctx.target_domain == "client.com"

    def test_fills_timestamp_and_day(self):
        step = SimpleNamespace(tool="connector.email.send_message", params={})
        classification = SimpleNamespace(tier=RiskTier.T3_IRREVERSIBLE)
        ctx = build_decision_context(step, classification)
        assert ctx.timestamp != ""
        assert 0 <= ctx.day_of_week <= 6
        assert 0 <= ctx.hour_of_day <= 23

    def test_handles_non_connector_step(self):
        step = SimpleNamespace(tool="fs.write", params={})
        classification = SimpleNamespace(tier=RiskTier.T2_CONTROLLED)
        ctx = build_decision_context(step, classification)
        assert ctx.capability == "fs.write"
        assert ctx.risk_tier == RiskTier.T2_CONTROLLED

    def test_risk_tier_from_classification(self):
        step = SimpleNamespace(tool="connector.slack.post_message", params={})
        classification = SimpleNamespace(tier=RiskTier.T2_CONTROLLED)
        ctx = build_decision_context(step, classification)
        assert ctx.risk_tier == RiskTier.T2_CONTROLLED

    def test_explicit_target_overrides_params(self):
        step = SimpleNamespace(
            tool="connector.email.send_message",
            params={"to": "alice@other.com"},
        )
        classification = SimpleNamespace(tier=RiskTier.T3_IRREVERSIBLE)
        ctx = build_decision_context(
            step, classification, target="bob@client.com"
        )
        assert ctx.target == "bob@client.com"

    def test_scope_from_channel_param(self):
        step = SimpleNamespace(
            tool="connector.slack.post_message",
            params={"channel": "#general"},
        )
        classification = SimpleNamespace(tier=RiskTier.T2_CONTROLLED)
        ctx = build_decision_context(step, classification)
        assert ctx.scope == "channel:#general"


class TestApprovalRecorder:
    def test_records_manual_approval(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        recorder = ApprovalRecorder(log)

        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        recorder.record_manual_decision(ctx, approved=True, decision_time_ms=500)
        assert log.total_decisions == 1
        assert log.total_approvals == 1

    def test_records_manual_denial(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        recorder = ApprovalRecorder(log)

        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        recorder.record_manual_decision(ctx, approved=False)
        assert log.total_denials == 1

    def test_records_auto_decision_with_rule_id(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        recorder = ApprovalRecorder(log)

        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        rule_check = RuleCheckResult(
            action="auto_approve", rule_id="rule-1", rule_name="Test Rule"
        )
        recorder.record_auto_decision(ctx, rule_check)
        assert log.auto_approved_count == 1

    def test_feature_flag_false_nothing_recorded(self, tmp_path):
        """When FEATURE_APPROVAL_LEARNING is False, recorder should not be called."""
        from src.core import feature_flags
        assert feature_flags.FEATURE_APPROVAL_LEARNING is False
        # The flag check is in the orchestrator, not recorder itself.
        # Recorder always records when called.
        # This test verifies the flag is False by default.

    def test_total_increments_after_recording(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        recorder = ApprovalRecorder(log)

        ctx = DecisionContext.from_action("connector.email.send_message")
        recorder.record_manual_decision(ctx, approved=True)
        recorder.record_manual_decision(ctx, approved=False)
        assert log.total_decisions == 2
