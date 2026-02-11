"""Tests for vNext4 governance feature flags (Prompt 1)."""

import os
import pytest


# Flags under test
GOV_FLAGS = [
    "FEATURE_RISK_TIERED_GOVERNANCE",
    "FEATURE_POLICY_CACHE",
    "FEATURE_ASYNC_VERIFICATION",
    "FEATURE_INTENT_TEMPLATES",
    "FEATURE_BATCH_RECEIPTS",
]

ENV_KEYS = [f for f in GOV_FLAGS]  # env var names match flag names


@pytest.fixture(autouse=True)
def clean_env():
    """Remove governance env vars before/after each test."""
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    import feature_flags
    feature_flags.reload_flags()
    yield
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    feature_flags.reload_flags()


def test_all_flags_default_false():
    """All five governance flags default to False when env vars are not set."""
    import feature_flags
    feature_flags.reload_flags()
    for flag_name in GOV_FLAGS:
        assert getattr(feature_flags, flag_name) is False, f"{flag_name} should default to False"


@pytest.mark.parametrize("value", ["true", "1", "yes", "TRUE", "Yes", "True"])
def test_flags_truthy_values(value):
    """Each flag correctly reads truthy values from env."""
    import feature_flags
    for key in ENV_KEYS:
        os.environ[key] = value
    feature_flags.reload_flags()
    for flag_name in GOV_FLAGS:
        assert getattr(feature_flags, flag_name) is True, f"{flag_name} should be True for '{value}'"


@pytest.mark.parametrize("value", ["false", "maybe", "2", "", "no", "0"])
def test_flags_falsy_values(value):
    """Invalid/falsy values default to False."""
    import feature_flags
    for key in ENV_KEYS:
        os.environ[key] = value
    feature_flags.reload_flags()
    for flag_name in GOV_FLAGS:
        assert getattr(feature_flags, flag_name) is False, f"{flag_name} should be False for '{value}'"


def test_reload_updates_flags():
    """reload_flags() correctly updates all five flags when env changes."""
    import feature_flags

    # Start False
    feature_flags.reload_flags()
    for flag_name in GOV_FLAGS:
        assert getattr(feature_flags, flag_name) is False

    # Set to True
    for key in ENV_KEYS:
        os.environ[key] = "true"
    feature_flags.reload_flags()
    for flag_name in GOV_FLAGS:
        assert getattr(feature_flags, flag_name) is True

    # Set back to False
    for key in ENV_KEYS:
        os.environ[key] = "false"
    feature_flags.reload_flags()
    for flag_name in GOV_FLAGS:
        assert getattr(feature_flags, flag_name) is False


def test_log_feature_flags_no_raise():
    """log_feature_flags() does not raise."""
    import feature_flags
    feature_flags.log_feature_flags()
