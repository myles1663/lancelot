"""
Tests for Prompts 42-43: TrustLedger Core + Graduation + Revocation.
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


@pytest.fixture
def config():
    return TrustGraduationConfig(
        thresholds=TrustGraduationThresholds(T3_to_T2=50, T2_to_T1=100, T1_to_T0=200),
        revocation=TrustRevocationConfig(
            on_failure="reset_to_default",
            on_rollback="reset_above_default",
            cooldown_after_denial=50,
            cooldown_after_revocation=25,
        ),
    )


@pytest.fixture
def ledger(config):
    return TrustLedger(config)


# ── Prompt 42: Core ──────────────────────────────────────────────

class TestLedgerBasics:
    def test_empty_ledger(self, ledger):
        assert ledger.list_records() == []

    def test_get_or_create_creates(self, ledger):
        rec = ledger.get_or_create_record("cap.test", "scope", RiskTier.T2_CONTROLLED)
        assert rec.capability == "cap.test"
        assert rec.current_tier == RiskTier.T2_CONTROLLED
        assert rec.default_tier == RiskTier.T2_CONTROLLED

    def test_get_or_create_returns_existing(self, ledger):
        r1 = ledger.get_or_create_record("cap.test", "scope", RiskTier.T2_CONTROLLED)
        r1.total_successes = 5
        r2 = ledger.get_or_create_record("cap.test", "scope", RiskTier.T2_CONTROLLED)
        assert r2.total_successes == 5
        assert r1 is r2


class TestRecordSuccess:
    def test_increments_counts(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        rec = ledger.record_success("cap.test", "s")
        assert rec.consecutive_successes == 1
        assert rec.total_successes == 1
        assert rec.last_success != ""

    def test_50_successes_triggers_graduation(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        for _ in range(50):
            ledger.record_success("cap.test", "s")
        assert len(ledger.pending_proposals()) == 1
        proposal = ledger.pending_proposals()[0]
        assert proposal.current_tier == RiskTier.T3_IRREVERSIBLE
        assert proposal.proposed_tier == RiskTier.T2_CONTROLLED


class TestCheckGraduation:
    def test_returns_proposal_at_threshold(self, ledger):
        rec = ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        rec.consecutive_successes = 50
        rec.total_successes = 50
        proposal = ledger.check_graduation(rec)
        assert proposal is not None
        assert proposal.proposed_tier == RiskTier.T2_CONTROLLED

    def test_returns_none_below_threshold(self, ledger):
        rec = ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        rec.consecutive_successes = 49
        rec.total_successes = 49
        proposal = ledger.check_graduation(rec)
        assert proposal is None

    def test_returns_none_at_soul_minimum(self, ledger):
        rec = ledger.get_or_create_record(
            "cap.test", "s", RiskTier.T2_CONTROLLED,
            soul_minimum_tier=RiskTier.T2_CONTROLLED,
        )
        rec.consecutive_successes = 200
        proposal = ledger.check_graduation(rec)
        assert proposal is None

    def test_t1_to_t0_at_200(self, ledger):
        rec = ledger.get_or_create_record("cap.test", "s", RiskTier.T1_REVERSIBLE)
        rec.consecutive_successes = 200
        rec.total_successes = 200
        proposal = ledger.check_graduation(rec)
        assert proposal is not None
        assert proposal.proposed_tier == RiskTier.T0_INERT


class TestRecordFailure:
    def test_resets_consecutive(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        for _ in range(10):
            ledger.record_success("cap.test", "s")
        rec = ledger.record_failure("cap.test", "s")
        assert rec.consecutive_successes == 0

    def test_increments_total_failures(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        ledger.record_failure("cap.test", "s")
        rec = ledger.get_record("cap.test", "s")
        assert rec.total_failures == 1

    def test_rollback_increments_rollbacks(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        ledger.record_failure("cap.test", "s", is_rollback=True)
        rec = ledger.get_record("cap.test", "s")
        assert rec.total_rollbacks == 1


class TestGetEffectiveTier:
    def test_none_for_unknown(self, ledger):
        assert ledger.get_effective_tier("unknown", "s") is None

    def test_returns_current_tier(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T2_CONTROLLED)
        assert ledger.get_effective_tier("cap.test", "s") == RiskTier.T2_CONTROLLED


class TestPendingProposals:
    def test_returns_all_pending(self, ledger):
        ledger.get_or_create_record("cap.a", "s", RiskTier.T3_IRREVERSIBLE)
        ledger.get_or_create_record("cap.b", "s", RiskTier.T3_IRREVERSIBLE)
        for _ in range(50):
            ledger.record_success("cap.a", "s")
            ledger.record_success("cap.b", "s")
        assert len(ledger.pending_proposals()) == 2


class TestCooldown:
    def test_cooldown_blocks_graduation(self, ledger):
        rec = ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        rec.cooldown_remaining = 5
        rec.consecutive_successes = 50
        proposal = ledger.check_graduation(rec)
        assert proposal is None

    def test_cooldown_expires_after_successes(self, ledger):
        rec = ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        rec.cooldown_remaining = 3
        for _ in range(3):
            ledger.record_success("cap.test", "s")
        assert rec.cooldown_remaining == 0


# ── Prompt 43: Graduation + Revocation ───────────────────────────

class TestApplyGraduation:
    def test_approved_lowers_tier(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        for _ in range(50):
            ledger.record_success("cap.test", "s")
        proposal = ledger.pending_proposals()[0]
        rec = ledger.apply_graduation(proposal.id, approved=True)
        assert rec.current_tier == RiskTier.T2_CONTROLLED

    def test_cannot_lower_below_soul_minimum(self, ledger):
        rec = ledger.get_or_create_record(
            "cap.test", "s", RiskTier.T1_REVERSIBLE,
            soul_minimum_tier=RiskTier.T1_REVERSIBLE,
        )
        # Manually force a proposal (T1→T0)
        proposal = GraduationProposal(
            capability="cap.test", scope="s",
            current_tier=RiskTier.T1_REVERSIBLE,
            proposed_tier=RiskTier.T0_INERT,
        )
        rec.pending_proposal = proposal
        ledger._proposals.append(proposal)
        result = ledger.apply_graduation(proposal.id, approved=True)
        assert result.current_tier == RiskTier.T1_REVERSIBLE  # Clamped at soul minimum

    def test_denied_sets_cooldown(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        for _ in range(50):
            ledger.record_success("cap.test", "s")
        proposal = ledger.pending_proposals()[0]
        rec = ledger.apply_graduation(proposal.id, approved=False)
        assert rec.cooldown_remaining == 50
        assert rec.pending_proposal is None
        assert proposal.status == "denied"


class TestRevokeTrust:
    def test_resets_to_default_on_failure(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        # Graduate: 50 successes + approve
        for _ in range(50):
            ledger.record_success("cap.test", "s")
        proposal = ledger.pending_proposals()[0]
        ledger.apply_graduation(proposal.id, approved=True)
        rec = ledger.get_record("cap.test", "s")
        assert rec.current_tier == RiskTier.T2_CONTROLLED

        # Failure revokes
        ledger.record_failure("cap.test", "s")
        assert rec.current_tier == RiskTier.T3_IRREVERSIBLE

    def test_rollback_resets_above_default(self, ledger):
        # Default T2, graduate to T1
        rec = ledger.get_or_create_record("cap.test", "s", RiskTier.T2_CONTROLLED)
        rec.consecutive_successes = 100
        rec.total_successes = 100
        ledger.check_graduation(rec)
        proposal = ledger.pending_proposals()[0]
        ledger.apply_graduation(proposal.id, approved=True)
        assert rec.current_tier == RiskTier.T1_REVERSIBLE

        # Rollback: reset to default+1 = T3
        ledger.revoke_trust("cap.test", "s", is_rollback=True)
        assert rec.current_tier == RiskTier.T3_IRREVERSIBLE

    def test_revocation_sets_cooldown(self, ledger):
        rec = ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        # Graduate
        for _ in range(50):
            ledger.record_success("cap.test", "s")
        proposal = ledger.pending_proposals()[0]
        ledger.apply_graduation(proposal.id, approved=True)
        # Revoke
        ledger.revoke_trust("cap.test", "s")
        assert rec.cooldown_remaining == 25


class TestFullLifecycle:
    def test_graduate_fail_regraduate(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)

        # 50 successes → propose
        for _ in range(50):
            ledger.record_success("cap.test", "s")
        p1 = ledger.pending_proposals()[0]
        ledger.apply_graduation(p1.id, approved=True)
        rec = ledger.get_record("cap.test", "s")
        assert rec.current_tier == RiskTier.T2_CONTROLLED

        # Failure → revoke
        ledger.record_failure("cap.test", "s")
        assert rec.current_tier == RiskTier.T3_IRREVERSIBLE
        assert rec.cooldown_remaining == 25

        # Burn off cooldown + 50 more successes
        for _ in range(75):
            ledger.record_success("cap.test", "s")

        # Should have new proposal
        pending = ledger.pending_proposals()
        assert len(pending) == 1
        assert pending[0].capability == "cap.test"


class TestSimulateTimeline:
    def test_shows_correct_transitions(self, ledger):
        events = ledger.simulate_timeline(
            "cap.test", "s", RiskTier.T3_IRREVERSIBLE, RiskTier.T0_INERT, 400
        )
        # Should see T3→T2 at 50, T2→T1 at 150, T1→T0 at 350
        assert len(events) > 0
        assert any(e["event"] == "graduation_proposed" for e in events)

    def test_does_not_modify_real_state(self, ledger):
        ledger.get_or_create_record("cap.test", "s", RiskTier.T3_IRREVERSIBLE)
        ledger.simulate_timeline("cap.test", "s", RiskTier.T3_IRREVERSIBLE, RiskTier.T0_INERT, 100)
        rec = ledger.get_record("cap.test", "s")
        assert rec.consecutive_successes == 0
        assert rec.total_successes == 0


class TestSoulMinimumLifecycle:
    def test_enforced_throughout(self, ledger):
        rec = ledger.get_or_create_record(
            "cap.test", "s", RiskTier.T3_IRREVERSIBLE,
            soul_minimum_tier=RiskTier.T2_CONTROLLED,
        )
        # Graduate T3→T2
        for _ in range(50):
            ledger.record_success("cap.test", "s")
        p = ledger.pending_proposals()[0]
        ledger.apply_graduation(p.id, approved=True)
        assert rec.current_tier == RiskTier.T2_CONTROLLED

        # Further successes should NOT create new proposal (at soul minimum)
        for _ in range(200):
            ledger.record_success("cap.test", "s")
        # Only the original approved proposal; no new pending ones
        assert len(ledger.pending_proposals()) == 0
