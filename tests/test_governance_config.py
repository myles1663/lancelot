"""Tests for vNext4 governance config loader (Prompt 3)."""

import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.config import (
    load_governance_config,
    GovernanceConfig,
    ScopeEscalation,
    AsyncVerificationConfig,
    IntentTemplateConfig,
    BatchReceiptConfig,
)


# Path to the real config file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")


def test_load_real_config():
    """load_governance_config() loads the real config/governance.yaml successfully."""
    config = load_governance_config(CONFIG_PATH)
    assert isinstance(config, GovernanceConfig)
    assert config.version == "1.0"


def test_correct_number_of_defaults():
    """Parsed config has 14 default tier mappings."""
    config = load_governance_config(CONFIG_PATH)
    assert len(config.risk_classification.defaults) == 14


def test_correct_number_of_scope_escalations():
    """Parsed config has 3 scope escalation rules."""
    config = load_governance_config(CONFIG_PATH)
    assert len(config.risk_classification.scope_escalations) == 3


def test_default_tiers():
    """fs.read defaults to tier 0, net.post defaults to tier 3."""
    config = load_governance_config(CONFIG_PATH)
    assert config.risk_classification.defaults["fs.read"] == 0
    assert config.risk_classification.defaults["net.post"] == 3


def test_scope_escalation_rejects_invalid_tier():
    """ScopeEscalation validates escalate_to range (0-3), rejects 4."""
    with pytest.raises(Exception):
        ScopeEscalation(capability="fs.write", escalate_to=4)


def test_async_verification_rejects_zero_workers():
    """AsyncVerificationConfig validates max_workers range (1-10), rejects 0."""
    with pytest.raises(Exception):
        AsyncVerificationConfig(max_workers=0)


def test_intent_template_rejects_high_risk_tier():
    """IntentTemplateConfig validates max_template_risk_tier (0-1), rejects 2."""
    with pytest.raises(Exception):
        IntentTemplateConfig(max_template_risk_tier=2)


def test_batch_receipt_rejects_zero_buffer():
    """BatchReceiptConfig validates buffer_size (1-1000), rejects 0."""
    with pytest.raises(Exception):
        BatchReceiptConfig(buffer_size=0)


def test_missing_config_returns_defaults():
    """load_governance_config() with nonexistent path returns default config."""
    config = load_governance_config("/nonexistent/path/governance.yaml")
    assert isinstance(config, GovernanceConfig)
    assert config.version == "1.0"
    assert len(config.risk_classification.defaults) == 0


def test_config_round_trip():
    """GovernanceConfig round-trips through dict (model_dump + model_validate)."""
    config = load_governance_config(CONFIG_PATH)
    dumped = config.model_dump()
    restored = GovernanceConfig.model_validate(dumped)
    assert restored.version == config.version
    assert len(restored.risk_classification.defaults) == len(config.risk_classification.defaults)
    assert len(restored.risk_classification.scope_escalations) == len(config.risk_classification.scope_escalations)
