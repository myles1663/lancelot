"""Tests for vNext4 governance data models (Prompt 2)."""

import pytest
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.models import (
    RiskTier,
    VerificationStrategy,
    VerificationStatus,
    RiskClassification,
    ActionRiskProfile,
    RECEIPT_TYPE_BATCH,
    RECEIPT_TYPE_VERIFICATION,
    RECEIPT_TYPE_VERIFICATION_FAILED,
    RECEIPT_TYPE_ROLLBACK,
    RECEIPT_TYPE_TEMPLATE_MATCH,
    RECEIPT_TYPE_POLICY_CACHE_HIT,
)


# ── RiskTier Tests ───────────────────────────────────────────────

def test_risk_tier_ordering():
    """T0 < T1 < T2 < T3."""
    assert RiskTier.T0_INERT < RiskTier.T1_REVERSIBLE
    assert RiskTier.T1_REVERSIBLE < RiskTier.T2_CONTROLLED
    assert RiskTier.T2_CONTROLLED < RiskTier.T3_IRREVERSIBLE


def test_risk_tier_integer_values():
    """Tier values are 0, 1, 2, 3."""
    assert int(RiskTier.T0_INERT) == 0
    assert int(RiskTier.T1_REVERSIBLE) == 1
    assert int(RiskTier.T2_CONTROLLED) == 2
    assert int(RiskTier.T3_IRREVERSIBLE) == 3


# ── RiskClassification Tests ────────────────────────────────────

def test_from_tier_t0():
    rc = RiskClassification.from_tier(RiskTier.T0_INERT)
    assert rc.requires_sync_verify is False
    assert rc.requires_approval is False
    assert rc.batchable_receipt is True
    assert rc.label == "inert"


def test_from_tier_t1():
    rc = RiskClassification.from_tier(RiskTier.T1_REVERSIBLE)
    assert rc.requires_sync_verify is False
    assert rc.requires_approval is False
    assert rc.batchable_receipt is True
    assert rc.label == "reversible"


def test_from_tier_t2():
    rc = RiskClassification.from_tier(RiskTier.T2_CONTROLLED)
    assert rc.requires_sync_verify is True
    assert rc.requires_approval is False
    assert rc.batchable_receipt is False
    assert rc.label == "controlled"


def test_from_tier_t3():
    rc = RiskClassification.from_tier(RiskTier.T3_IRREVERSIBLE)
    assert rc.requires_sync_verify is True
    assert rc.requires_approval is True
    assert rc.batchable_receipt is False
    assert rc.label == "irreversible"


def test_risk_classification_frozen():
    """RiskClassification is immutable."""
    rc = RiskClassification.from_tier(RiskTier.T0_INERT)
    with pytest.raises(AttributeError):
        rc.tier = RiskTier.T3_IRREVERSIBLE


# ── ActionRiskProfile Tests ─────────────────────────────────────

def test_action_risk_profile_auto_timestamp():
    """classified_at is auto-set to a valid ISO 8601 string."""
    profile = ActionRiskProfile(tier=RiskTier.T0_INERT, capability="fs.read")
    assert profile.classified_at
    # Should parse without error
    datetime.fromisoformat(profile.classified_at.replace("Z", "+00:00") if profile.classified_at.endswith("Z") else profile.classified_at)


def test_action_risk_profile_frozen():
    """ActionRiskProfile is immutable."""
    profile = ActionRiskProfile(tier=RiskTier.T0_INERT, capability="fs.read")
    with pytest.raises(AttributeError):
        profile.tier = RiskTier.T3_IRREVERSIBLE


# ── VerificationStatus/Strategy Tests ───────────────────────────

def test_verification_status_distinct():
    """All six VerificationStatus values are distinct strings."""
    values = [s.value for s in VerificationStatus]
    assert len(values) == 6
    assert len(set(values)) == 6


def test_verification_strategy_distinct():
    """All three VerificationStrategy values are distinct strings."""
    values = [s.value for s in VerificationStrategy]
    assert len(values) == 3
    assert len(set(values)) == 3


# ── Receipt Type Constants ──────────────────────────────────────

def test_receipt_type_constants_non_empty():
    """All receipt type constants are non-empty strings."""
    for const in [
        RECEIPT_TYPE_BATCH,
        RECEIPT_TYPE_VERIFICATION,
        RECEIPT_TYPE_VERIFICATION_FAILED,
        RECEIPT_TYPE_ROLLBACK,
        RECEIPT_TYPE_TEMPLATE_MATCH,
        RECEIPT_TYPE_POLICY_CACHE_HIT,
    ]:
        assert isinstance(const, str)
        assert len(const) > 0
