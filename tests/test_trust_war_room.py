"""
Tests for Prompt 46: Trust War Room Panel.
"""

import pytest
from src.core.governance.models import RiskTier
from src.core.governance.trust_models import (
    GraduationProposal,
    TrustGraduationConfig,
    TrustGraduationThresholds,
    TrustRevocationConfig,
)
from src.core.governance.trust_ledger import TrustLedger
from src.core.governance.war_room_panel import (
    render_trust_panel,
    format_graduation_proposal,
)


@pytest.fixture
def config():
    return TrustGraduationConfig(
        thresholds=TrustGraduationThresholds(T3_to_T2=50, T2_to_T1=100, T1_to_T0=200),
        revocation=TrustRevocationConfig(),
    )


@pytest.fixture
def ledger(config):
    return TrustLedger(config)


class TestRenderTrustPanel:
    def test_empty_ledger_zero_counts(self):
        result = render_trust_panel(None)
        assert result["summary"]["total_records"] == 0
        assert result["summary"]["graduated_records"] == 0
        assert result["summary"]["pending_proposals"] == 0
        assert result["summary"]["avg_success_rate"] == 0.0

    def test_with_records_correct_summary(self, ledger):
        ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T3_IRREVERSIBLE)
        ledger.get_or_create_record("connector.slack.read", "s", RiskTier.T0_INERT)
        # Add some successes
        for _ in range(10):
            ledger.record_success("connector.slack.post", "s")
            ledger.record_success("connector.slack.read", "s")
        result = render_trust_panel(ledger)
        assert result["summary"]["total_records"] == 2
        assert result["summary"]["avg_success_rate"] == 1.0

    def test_graduated_records_counted(self, ledger):
        rec = ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T3_IRREVERSIBLE)
        rec.current_tier = RiskTier.T2_CONTROLLED  # Manually graduate
        result = render_trust_panel(ledger)
        assert result["summary"]["graduated_records"] == 1

    def test_proposals_included(self, ledger):
        ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T3_IRREVERSIBLE)
        for _ in range(50):
            ledger.record_success("connector.slack.post", "s")
        result = render_trust_panel(ledger)
        assert result["summary"]["pending_proposals"] == 1
        assert len(result["proposals"]) == 1
        assert result["proposals"][0]["capability"] == "connector.slack.post"

    def test_per_connector_breakdown(self, ledger):
        ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T2_CONTROLLED)
        ledger.get_or_create_record("connector.email.send", "s", RiskTier.T3_IRREVERSIBLE)
        result = render_trust_panel(ledger)
        connector_ids = {c["connector_id"] for c in result["per_connector"]}
        assert "slack" in connector_ids
        assert "email" in connector_ids


class TestFormatGraduationProposal:
    def test_includes_capability_and_transition(self):
        proposal = GraduationProposal(
            capability="connector.slack.post_message",
            scope="channel:#general",
            current_tier=RiskTier.T2_CONTROLLED,
            proposed_tier=RiskTier.T1_REVERSIBLE,
            consecutive_successes=100,
        )
        text = format_graduation_proposal(proposal)
        assert "connector.slack.post_message" in text
        assert "100" in text
        assert "T2_CONTROLLED" in text
        assert "T1_REVERSIBLE" in text
        assert "Approve?" in text
