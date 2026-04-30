"""
Tests for P74: APL War Room Panel + Hardening + End-to-End.
"""

import uuid
import threading
import pytest
from datetime import datetime, timedelta, timezone

from src.core.governance.models import RiskTier
from src.core.governance.approval_learning.config import (
    APLConfig,
    DetectionConfig,
    PersistenceConfig,
    RulesConfig,
)
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import (
    ApprovalPattern,
    AutomationRule,
    DecisionContext,
    DecisionRecord,
    RuleCheckResult,
)
from src.core.governance.approval_learning.pattern_detector import PatternDetector
from src.core.governance.approval_learning.rule_engine import RuleEngine
from src.core.governance.approval_learning.analyzer import APLAnalyzer
from src.core.governance.approval_learning.orchestrator_wiring import ApprovalRecorder
from src.core.governance.approval_learning.war_room_panel import (
    render_apl_panel,
    format_proposal_for_owner,
)


def _make_config(tmp_path, **overrides) -> APLConfig:
    return APLConfig(
        detection=DetectionConfig(
            min_observations=overrides.get("min_obs", 20),
            confidence_threshold=overrides.get("confidence", 0.85),
            analysis_trigger_interval=overrides.get("trigger", 10),
            max_pattern_dimensions=overrides.get("max_dims", 3),
        ),
        rules=RulesConfig(
            max_active_rules=overrides.get("max_rules", 50),
            max_auto_decisions_per_day=overrides.get("daily_limit", 50),
            max_auto_decisions_total=overrides.get("total_limit", 500),
            cooldown_after_decline=overrides.get("cooldown", 30),
        ),
        never_automate=overrides.get("never_automate", []),
        persistence=PersistenceConfig(
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            rules_path=str(tmp_path / "rules.json"),
            patterns_path=str(tmp_path / "patterns.json"),
        ),
    )


def _make_rule(engine, pattern_type="auto_approve", conditions=None, **kwargs):
    rule = AutomationRule(
        id=str(uuid.uuid4()),
        name=kwargs.get("name", "Test Rule"),
        description="Test",
        pattern_id=str(uuid.uuid4()),
        pattern_type=pattern_type,
        conditions=conditions or {"capability": "connector.email.send_message"},
        created_at=datetime.now(timezone.utc).isoformat(),
        last_reset_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        max_auto_decisions_per_day=kwargs.get("daily_limit", 50),
        max_auto_decisions_total=kwargs.get("total_limit", 500),
    )
    engine.add_proposal(rule)
    engine.activate_rule(rule.id)
    return rule


# ── War Room Panel Tests ────────────────────────────────────────


class TestWarRoomPanel:
    def test_render_panel_structure(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        panel = render_apl_panel(engine, log)
        assert "summary" in panel
        assert "rules" in panel
        assert "proposals" in panel
        assert "circuit_breakers" in panel
        assert "reconfirmation_needed" in panel
        assert "recent_decisions" in panel

    def test_render_panel_counts(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        _make_rule(engine)

        panel = render_apl_panel(engine, log)
        assert panel["summary"]["active_rules"] == 1
        assert panel["summary"]["total_decisions"] == 0

    def test_format_proposal(self):
        rule = AutomationRule(
            id="r1", name="Auto-approve emails to @client.com",
            description="Based on 47 decisions over 21 days.",
            pattern_id="p1", pattern_type="auto_approve",
            max_auto_decisions_per_day=50,
            max_auto_decisions_total=500,
        )
        text = format_proposal_for_owner(rule)
        assert "Auto-approve emails to @client.com" in text
        assert "50" in text
        assert "500" in text
        assert "Activate Rule" in text


# ── Hardening Tests ─────────────────────────────────────────────


class TestDenyWins:
    def test_deny_beats_broad_approve(self, tmp_path):
        """Denial rule for specific target beats broad approval rule."""
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        # Broad approve
        _make_rule(
            engine,
            pattern_type="auto_approve",
            conditions={"capability": "connector.email.*"},
            name="Broad approve",
        )
        # Specific deny
        _make_rule(
            engine,
            pattern_type="auto_deny",
            conditions={
                "capability": "connector.email.send_message",
                "target_domain": "evil.com",
            },
            name="Deny evil.com",
        )

        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="spy@evil.com"
        )
        result = engine.check(ctx)
        assert result.action == "auto_deny"


class TestCircuitBreaker:
    def test_trips_at_daily_limit(self, tmp_path):
        config = _make_config(tmp_path, daily_limit=5)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        _make_rule(engine, daily_limit=5)

        ctx = DecisionContext.from_action("connector.email.send_message")
        for _ in range(5):
            result = engine.check(ctx)
            assert result.action == "auto_approve"

        # 6th should trip
        result = engine.check(ctx)
        assert result.action == "ask_owner"


class TestReConfirmation:
    def test_pauses_at_total_limit(self, tmp_path):
        config = _make_config(tmp_path, total_limit=3)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule(engine, total_limit=3)

        ctx = DecisionContext.from_action("connector.email.send_message")
        for _ in range(3):
            engine.check(ctx)

        # Should need reconfirmation
        needing = engine.check_reconfirmation()
        assert len(needing) == 1

        # Next check returns ask_owner
        result = engine.check(ctx)
        assert result.action == "ask_owner"

        # Owner re-activates (reset counters)
        rule.auto_decisions_total = 0
        rule.auto_decisions_today = 0
        result = engine.check(ctx)
        assert result.action == "auto_approve"


class TestNeverAutomate:
    def test_stripe_never_proposed(self, tmp_path):
        config = _make_config(
            tmp_path,
            never_automate=["connector.stripe.*"],
            min_obs=5,
            confidence=0.80,
            trigger=5,
        )
        log = DecisionLog(config)
        detector = PatternDetector(config)
        engine = RuleEngine(config, log)
        analyzer = APLAnalyzer(config, log, detector, engine)

        for _ in range(50):
            ctx = DecisionContext.from_action("connector.stripe.charge_customer")
            log.record(ctx, "approved")

        proposals = analyzer.maybe_analyze()
        stripe_proposals = [
            p
            for p in proposals
            if "stripe" in p.conditions.get("capability", "")
        ]
        assert len(stripe_proposals) == 0


class TestCooldown:
    def test_declined_pattern_cooldown(self, tmp_path):
        config = _make_config(tmp_path, cooldown=10)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        rule = AutomationRule(
            id=str(uuid.uuid4()),
            name="Test",
            description="Test",
            pattern_id="p-cooldown",
            pattern_type="auto_approve",
            conditions={"capability": "connector.email.send_message"},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        engine.add_proposal(rule)
        engine.decline_rule(rule.id)

        assert engine.is_pattern_declined("p-cooldown") is True

        # After cooldown decrements
        for _ in range(10):
            engine.decrement_cooldowns()

        assert engine.is_pattern_declined("p-cooldown") is False


class TestSpecificityPreference:
    def test_narrow_rule_wins(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        # Broad: all emails
        _make_rule(
            engine,
            conditions={"capability": "connector.email.send_message"},
            name="All emails",
        )
        # Narrow: emails to client.com
        narrow = _make_rule(
            engine,
            conditions={
                "capability": "connector.email.send_message",
                "target_domain": "client.com",
            },
            name="Emails to client.com",
        )

        ctx = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        result = engine.check(ctx)
        assert result.rule_id == narrow.id


class TestConcurrentSafety:
    def test_concurrent_checks(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)
        rule = _make_rule(engine)

        results = []
        errors = []

        def check_once():
            try:
                ctx = DecisionContext.from_action("connector.email.send_message")
                r = engine.check(ctx)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=check_once) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        # All should be auto_approve
        assert all(r.action == "auto_approve" for r in results)
        # Usage should be exactly 10
        assert rule.auto_decisions_total == 10


class TestPersistence:
    def test_survives_restart(self, tmp_path):
        config = _make_config(tmp_path)
        log1 = DecisionLog(config)
        engine1 = RuleEngine(config, log1)
        rule = _make_rule(engine1)

        # Add a decision
        ctx = DecisionContext.from_action("connector.email.send_message")
        log1.record(ctx, "approved")

        # Restart
        log2 = DecisionLog(config)
        engine2 = RuleEngine(config, log2)

        assert log2.total_decisions == 1
        loaded_rule = engine2.get_rule(rule.id)
        assert loaded_rule is not None
        assert loaded_rule.status == "active"


class TestTimeBasedRules:
    def test_time_range_enforcement(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        engine = RuleEngine(config, log)

        rule = AutomationRule(
            id=str(uuid.uuid4()),
            name="Business hours only",
            description="Test",
            pattern_id="p-time",
            pattern_type="auto_approve",
            conditions={
                "capability": "connector.email.send_message",
                "time_range": [9, 17],
            },
            created_at=datetime.now(timezone.utc).isoformat(),
            last_reset_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        engine.add_proposal(rule)
        engine.activate_rule(rule.id)

        # Hour 10 → approved
        ts_10 = datetime(2026, 3, 9, 10, 0, 0, tzinfo=timezone.utc)
        ctx_10 = DecisionContext.from_action(
            "connector.email.send_message",
            target="bob@client.com",
            timestamp=ts_10,
        )
        result_10 = engine.check(ctx_10)
        assert result_10.action == "auto_approve"

        # Hour 22 → ask_owner
        ts_22 = datetime(2026, 3, 9, 22, 0, 0, tzinfo=timezone.utc)
        ctx_22 = DecisionContext.from_action(
            "connector.email.send_message",
            target="bob@client.com",
            timestamp=ts_22,
        )
        result_22 = engine.check(ctx_22)
        assert result_22.action == "ask_owner"


# ── Full Lifecycle End-to-End ───────────────────────────────────


class TestFullLifecycleE2E:
    def test_complete_lifecycle(self, tmp_path):
        """
        Full E2E lifecycle:
        a. Start empty
        b. 30 manual approvals for emails to bob@client.com
        c. Pattern detected, proposal generated
        d. Activate proposal
        e. Next email to bob@client.com → auto-approved
        f. Email to alice@other.com → ask_owner
        g. Manual denial doesn't kill the rule
        h. Hit daily limit → circuit breaker
        i. New day → auto resumes
        """
        config = _make_config(
            tmp_path,
            min_obs=10,
            confidence=0.80,
            trigger=5,
            daily_limit=5,
            total_limit=500,
        )
        log = DecisionLog(config)
        detector = PatternDetector(config)
        engine = RuleEngine(config, log)
        analyzer = APLAnalyzer(config, log, detector, engine)
        recorder = ApprovalRecorder(log)

        # a. Empty
        assert log.total_decisions == 0
        assert len(engine.list_rules()) == 0

        # b. 30 manual approvals
        for _ in range(30):
            ctx = DecisionContext.from_action(
                "connector.email.send_message", target="bob@client.com"
            )
            recorder.record_manual_decision(ctx, approved=True, decision_time_ms=200)

        assert log.total_decisions == 30

        # c. Pattern detection + proposal
        proposals = analyzer.maybe_analyze()
        assert len(proposals) > 0

        # d. Activate
        proposal = proposals[0]
        engine.activate_rule(proposal.id)
        assert engine.get_rule(proposal.id).is_active

        # e. Auto-approve matching
        ctx_bob = DecisionContext.from_action(
            "connector.email.send_message", target="bob@client.com"
        )
        result = engine.check(ctx_bob)
        assert result.action == "auto_approve"

        # f. Non-matching → ask_owner
        ctx_alice = DecisionContext.from_action(
            "connector.slack.post_message", target="channel:#random"
        )
        result = engine.check(ctx_alice)
        assert result.action == "ask_owner"

        # g. One manual denial for bob doesn't kill the auto-approve rule
        recorder.record_manual_decision(
            DecisionContext.from_action(
                "connector.email.send_message", target="bob@client.com"
            ),
            approved=False,
        )
        # Rule still active
        assert engine.get_rule(proposal.id).status == "active"

        # h. Hit daily limit (already used 1 in step e, use 4 more)
        for _ in range(4):
            engine.check(ctx_bob)

        # Circuit breaker should trip
        result = engine.check(ctx_bob)
        assert result.action == "ask_owner"
        tripped = engine.check_circuit_breakers()
        assert len(tripped) == 1

        # i. Simulate new day (reset daily counter)
        rule = engine.get_rule(proposal.id)
        rule.auto_decisions_today = 0
        rule.last_reset_date = "2020-01-01"  # Force reset on next increment
        result = engine.check(ctx_bob)
        assert result.action == "auto_approve"
