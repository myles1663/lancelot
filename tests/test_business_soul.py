"""
Tests for Prompt 63: Business Soul Configuration.
"""

import pytest
from src.business.soul_config import (
    BUSINESS_SOUL_CONFIG,
    create_business_soul,
    validate_business_soul,
)


class TestBusinessSoulConfig:
    def test_has_identity_and_governance(self):
        assert "identity" in BUSINESS_SOUL_CONFIG
        assert "governance" in BUSINESS_SOUL_CONFIG

    def test_stripe_minimum_tier_3(self):
        overrides = BUSINESS_SOUL_CONFIG["governance"]["risk_overrides"]
        stripe = [o for o in overrides if "stripe" in o["capability"]]
        assert len(stripe) == 1
        assert stripe[0]["minimum_tier"] == 3

    def test_email_non_verified_tier_3(self):
        overrides = BUSINESS_SOUL_CONFIG["governance"]["risk_overrides"]
        email = [o for o in overrides if "email" in o["capability"]]
        assert len(email) == 1
        assert email[0]["minimum_tier"] == 3


class TestCreateBusinessSoul:
    def test_with_recipients(self):
        soul = create_business_soul(verified_recipients=["alice@example.com"])
        recipients = soul["governance"]["connector_policies"]["email"]["verified_recipients"]
        assert "alice@example.com" in recipients

    def test_with_slack_channels(self):
        soul = create_business_soul(slack_channels=["#general"])
        channels = soul["governance"]["connector_policies"]["slack"]["allowed_channels"]
        assert "#general" in channels


class TestValidateBusinessSoul:
    def test_passes_for_valid(self):
        valid, issues = validate_business_soul(BUSINESS_SOUL_CONFIG)
        assert valid is True
        assert len(issues) == 0

    def test_fails_for_missing_governance(self):
        valid, issues = validate_business_soul({"identity": "test"})
        assert valid is False
        assert any("governance" in i for i in issues)
