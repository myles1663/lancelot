"""
Tests for Prompt 41: Trust Data Models + Configuration.
"""

import pytest
from src.core.governance.models import RiskTier
from src.core.governance.trust_models import (
    GraduationEvent,
    GraduationProposal,
    TrustRecord,
    TrustGraduationConfig,
    TrustGraduationThresholds,
    TrustRevocationConfig,
    load_trust_config,
)


class TestTrustRecordDefaults:
    def test_defaults_correct(self):
        rec = TrustRecord(
            capability="connector.slack.post_message",
            scope="channel:#general",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T2_CONTROLLED,
        )
        assert rec.consecutive_successes == 0
        assert rec.total_successes == 0
        assert rec.total_failures == 0
        assert rec.total_rollbacks == 0
        assert rec.last_success == ""
        assert rec.last_failure == ""
        assert rec.graduation_history == []
        assert rec.pending_proposal is None
        assert rec.cooldown_remaining == 0


class TestSuccessRate:
    def test_empty_returns_zero(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T2_CONTROLLED,
        )
        assert rec.success_rate == 0.0

    def test_eight_of_ten(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T2_CONTROLLED,
            total_successes=8, total_failures=2,
        )
        assert rec.success_rate == pytest.approx(0.8)


class TestIsGraduated:
    def test_graduated_when_below_default(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T1_REVERSIBLE,
            default_tier=RiskTier.T2_CONTROLLED,
        )
        assert rec.is_graduated is True

    def test_not_graduated_at_default(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T2_CONTROLLED,
        )
        assert rec.is_graduated is False


class TestCanGraduate:
    def test_can_graduate_above_minimum(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T3_IRREVERSIBLE,
            soul_minimum_tier=RiskTier.T0_INERT,
        )
        assert rec.can_graduate is True

    def test_cannot_at_soul_minimum(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T3_IRREVERSIBLE,
            soul_minimum_tier=RiskTier.T2_CONTROLLED,
        )
        assert rec.can_graduate is False

    def test_cannot_with_cooldown(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T3_IRREVERSIBLE,
            cooldown_remaining=10,
        )
        assert rec.can_graduate is False

    def test_cannot_with_pending_proposal(self):
        rec = TrustRecord(
            capability="test", scope="s",
            current_tier=RiskTier.T2_CONTROLLED,
            default_tier=RiskTier.T3_IRREVERSIBLE,
            pending_proposal=GraduationProposal(),
        )
        assert rec.can_graduate is False


class TestGraduationProposal:
    def test_default_status_pending(self):
        p = GraduationProposal()
        assert p.status == "pending"

    def test_id_is_uuid(self):
        p = GraduationProposal()
        assert len(p.id) == 36  # UUID format


class TestLoadConfig:
    def test_loads_real_yaml(self):
        config = load_trust_config("config/trust_graduation.yaml")
        assert isinstance(config, TrustGraduationConfig)
        assert config.version == "1.0"

    def test_thresholds_correct(self):
        config = load_trust_config("config/trust_graduation.yaml")
        assert config.thresholds.T3_to_T2 == 50
        assert config.thresholds.T2_to_T1 == 100
        assert config.thresholds.T1_to_T0 == 200

    def test_missing_file_returns_defaults(self):
        config = load_trust_config("/nonexistent/path.yaml")
        assert isinstance(config, TrustGraduationConfig)
        assert config.thresholds.T3_to_T2 == 50
