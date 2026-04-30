"""
Tests for P67: DecisionLog — Append-Only Journal.
"""

import os
import pytest
from datetime import datetime, timedelta, timezone

from src.core.governance.approval_learning.config import APLConfig, PersistenceConfig
from src.core.governance.approval_learning.decision_log import DecisionLog
from src.core.governance.approval_learning.models import DecisionContext, RiskTier


def _make_config(tmp_path) -> APLConfig:
    return APLConfig(
        persistence=PersistenceConfig(
            decision_log_path=str(tmp_path / "decisions.jsonl"),
            rules_path=str(tmp_path / "rules.json"),
            patterns_path=str(tmp_path / "patterns.json"),
        )
    )


def _make_context(capability="connector.email.send_message",
                   target="bob@client.com", **kwargs) -> DecisionContext:
    return DecisionContext.from_action(capability, target=target, **kwargs)


class TestDecisionLog:
    def test_empty_log(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        assert log.total_decisions == 0

    def test_record_creates_with_uuid(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        rec = log.record(_make_context(), "approved")
        assert rec.id != ""
        assert len(rec.id) == 36  # UUID format

    def test_record_increments_total(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context(), "approved")
        assert log.total_decisions == 1

    def test_record_approved_increments_approvals(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context(), "approved")
        assert log.total_approvals == 1

    def test_record_denied_increments_denials(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context(), "denied")
        assert log.total_denials == 1

    def test_record_with_rule_id_increments_auto(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context(), "approved", rule_id="rule-1")
        assert log.auto_approved_count == 1

    def test_get_recent(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        for i in range(10):
            log.record(_make_context(), "approved")
        recent = log.get_recent(5)
        assert len(recent) == 5
        # Newest first
        assert recent[0].recorded_at >= recent[-1].recorded_at

    def test_get_window_filters_by_days(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        # Add 3 recent records
        for _ in range(3):
            log.record(_make_context(), "approved")
        window = log.get_window(7)
        assert len(window) == 3

    def test_get_by_capability(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context("connector.email.send_message"), "approved")
        log.record(_make_context("connector.slack.post_message"), "approved")
        log.record(_make_context("connector.email.send_message"), "denied")
        result = log.get_by_capability("connector.email.send_message")
        assert len(result) == 2

    def test_get_by_target_domain(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context(target="bob@client.com"), "approved")
        log.record(_make_context(target="alice@other.com"), "approved")
        log.record(_make_context(target="carol@client.com"), "approved")
        result = log.get_by_target_domain("client.com")
        assert len(result) == 2

    def test_persists_to_file(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        log.record(_make_context(), "approved")
        path = config.persistence.decision_log_path
        assert os.path.exists(path)
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 1

    def test_survives_restart(self, tmp_path):
        config = _make_config(tmp_path)
        log1 = DecisionLog(config)
        log1.record(_make_context(), "approved")
        log1.record(_make_context(), "denied")
        log1.record(_make_context(), "approved", rule_id="r1")

        log2 = DecisionLog(config)
        assert log2.total_decisions == 3
        assert log2.total_approvals == 2
        assert log2.total_denials == 1
        assert log2.auto_approved_count == 1

    def test_count_since_analysis_starts_at_total(self, tmp_path):
        config = _make_config(tmp_path)
        log = DecisionLog(config)
        log.record(_make_context(), "approved")
        log.record(_make_context(), "approved")
        assert log.count_since_last_analysis() == 2

    def test_mark_analysis_resets_counter(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context(), "approved")
        log.mark_analysis_complete()
        assert log.count_since_last_analysis() == 0

    def test_new_records_after_mark_increment(self, tmp_path):
        log = DecisionLog(_make_config(tmp_path))
        log.record(_make_context(), "approved")
        log.mark_analysis_complete()
        log.record(_make_context(), "denied")
        log.record(_make_context(), "approved")
        assert log.count_since_last_analysis() == 2
