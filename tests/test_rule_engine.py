"""
Tests for P72: RuleEngine Core.
"""

import uuid
import pytest
from datetime import datetime, timezone

from src.core.governance.models import RiskTier
from src.core.governance.approval_learning.config import APLConfig, PersistenceConfig, RulesConfig
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    AutomationRule,
    DecisionContext,
    RuleCheckResult,
)
from src.core.governance.approval_learning.rule_engine import RuleEngine


def _make_config(tmp_path, **overrides) -> APLConfig:
    rules = RulesConfig(**(overrides.get("rules_kwargs", {})))
    return APLConfig(
        persistence=PersistenceConfig(
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            rules_path=str(tmp_path / "rules.json"),
            patterns_path=str(tmp_path / "patterns.json"),
        ),
        rules=rules,
    )


def _make_rule(
    rule_id=None,
    name="Test Rule",
    pattern_type="auto_approve",
    conditions=None,
    status="proposed",
    owner_confirmed=False,
    **kwargs,
) -> AutomationRule:
    return AutomationRule(
        id=rule_id or str(uuid.uuid4()),
        name=name,
        description="Test rule",
        pattern_id=str(uuid.uuid4()),
        pattern_type=pattern_type,
        conditions=conditions or {"capability": "connector.email.send_message"},
        status=status,
        owner_confirmed=owner_confirmed,
        created_at=datetime.now(timezone.utc).isoformat(),
        last_reset_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        **kwargs,
    )


def _make_context(
    capability="connector.email.send_message",
    target="bob@client.com",
) -> DecisionContext:
    return DecisionContext.from_action(capability, target=target)


class TestRuleEngine:
    def test_add_proposal(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        result = engine.add_proposal(rule)
        assert result.status == "proposed"
        assert len(engine.list_rules()) == 1

    def test_activate(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        result = engine.activate_rule(rule.id)
        assert result.status == "active"
        assert result.owner_confirmed is True
        assert result.activated_at != ""

    def test_decline(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        result = engine.decline_rule(rule.id)
        assert result.status == "revoked"
        assert engine.is_pattern_declined(rule.pattern_id) is True

    def test_pause(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        result = engine.pause_rule(rule.id)
        assert result.status == "paused"

    def test_resume(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        engine.pause_rule(rule.id)
        result = engine.resume_rule(rule.id)
        assert result.status == "active"

    def test_revoke(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        result = engine.revoke_rule(rule.id)
        assert result.status == "revoked"
        assert result.revoked_at != ""

    def test_check_no_rules(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        result = engine.check(_make_context())
        assert result.action == "ask_owner"

    def test_check_matching_approve(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        result = engine.check(_make_context())
        assert result.action == "auto_approve"
        assert result.rule_id == rule.id

    def test_check_matching_deny(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule(pattern_type="auto_deny")
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        result = engine.check(_make_context())
        assert result.action == "auto_deny"

    def test_deny_wins_over_approve(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        approve = _make_rule(pattern_type="auto_approve")
        deny = _make_rule(
            pattern_type="auto_deny",
            conditions={"capability": "connector.email.send_message"},
        )
        engine.add_proposal(approve)
        engine.activate_rule(approve.id)
        engine.add_proposal(deny)
        engine.activate_rule(deny.id)

        result = engine.check(_make_context())
        assert result.action == "auto_deny"

    def test_most_specific_wins(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        broad = _make_rule(
            conditions={"capability": "connector.email.send_message"},
        )
        narrow = _make_rule(
            conditions={
                "capability": "connector.email.send_message",
                "target_domain": "client.com",
            },
        )
        engine.add_proposal(broad)
        engine.activate_rule(broad.id)
        engine.add_proposal(narrow)
        engine.activate_rule(narrow.id)

        result = engine.check(_make_context())
        assert result.rule_id == narrow.id

    def test_check_increments_usage(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        engine.check(_make_context())
        assert rule.auto_decisions_today == 1
        assert rule.auto_decisions_total == 1

    def test_check_daily_limit_reached(self, tmp_path):
        config = _make_config(tmp_path, rules_kwargs={"max_auto_decisions_per_day": 2})
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule(max_auto_decisions_per_day=2)
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        engine.check(_make_context())
        engine.check(_make_context())
        result = engine.check(_make_context())
        assert result.action == "ask_owner"

    def test_check_total_limit_reached(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule(max_auto_decisions_total=2)
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        engine.check(_make_context())
        engine.check(_make_context())
        result = engine.check(_make_context())
        assert result.action == "ask_owner"

    def test_circuit_breakers(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule(max_auto_decisions_per_day=2)
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        engine.check(_make_context())
        engine.check(_make_context())
        tripped = engine.check_circuit_breakers()
        assert len(tripped) == 1

    def test_reconfirmation(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule(max_auto_decisions_total=2)
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        engine.check(_make_context())
        engine.check(_make_context())
        needing = engine.check_reconfirmation()
        assert len(needing) == 1

    def test_is_pattern_declined_cooldown(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        engine.decline_rule(rule.id)
        assert engine.is_pattern_declined(rule.pattern_id) is True

    def test_persist_and_load(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine1 = RuleEngine(config, log)
        rule = _make_rule()
        engine1.add_proposal(rule)
        engine1.activate_rule(rule.id)

        # Load fresh
        engine2 = RuleEngine(config, log)
        loaded = engine2.get_rule(rule.id)
        assert loaded is not None
        assert loaded.status == "active"
        assert loaded.owner_confirmed is True

    def test_max_active_rules_enforced(self, tmp_path):
        config = _make_config(tmp_path, rules_kwargs={"max_active_rules": 2})
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        r1 = _make_rule()
        r2 = _make_rule()
        r3 = _make_rule()
        engine.add_proposal(r1)
        engine.activate_rule(r1.id)
        engine.add_proposal(r2)
        engine.activate_rule(r2.id)

        with pytest.raises(ValueError, match="Max active rules"):
            engine.add_proposal(r3)

    def test_get_stats(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule()
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)
        stats = engine.get_stats()
        assert stats["active"] == 1
        assert stats["proposed"] == 0
