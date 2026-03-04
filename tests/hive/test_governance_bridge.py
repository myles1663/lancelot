"""Tests for HIVE Governance Bridge."""

import pytest

from src.hive.integration.governance_bridge import GovernanceBridge, GovernanceResult


class MockRiskClassifier:
    """Mock risk classifier for testing."""

    def __init__(self, default_tier=0):
        self._default_tier = default_tier

    def classify(self, capability, scope="workspace", target=None):
        from unittest.mock import MagicMock
        profile = MagicMock()
        profile.tier = MagicMock()
        profile.tier.value = self._default_tier
        return profile


class MockTrustLedger:
    """Mock trust ledger for testing."""

    def __init__(self, effective_tier=None):
        self._effective = effective_tier
        self.successes = []
        self.failures = []

    def get_effective_tier(self, capability, scope):
        return self._effective

    def record_success(self, capability, scope):
        self.successes.append((capability, scope))

    def record_failure(self, capability, scope):
        self.failures.append((capability, scope))


class MockMCPSentry:
    """Mock MCP Sentry for testing."""

    def __init__(self, allow_all=True):
        self._allow_all = allow_all

    def check_permission(self, capability):
        return self._allow_all


class TestValidateAction:
    def test_t0_action_approved(self):
        bridge = GovernanceBridge(
            risk_classifier=MockRiskClassifier(default_tier=0),
        )
        result = bridge.validate_action("classify_intent")
        assert result.approved is True
        assert result.tier == "T0"

    def test_t1_action_approved(self):
        bridge = GovernanceBridge(
            risk_classifier=MockRiskClassifier(default_tier=1),
        )
        result = bridge.validate_action("summarize")
        assert result.approved is True
        assert result.tier == "T1"

    def test_t2_action_requires_approval(self):
        bridge = GovernanceBridge(
            risk_classifier=MockRiskClassifier(default_tier=2),
        )
        result = bridge.validate_action("shell_exec")
        assert result.approved is False
        assert result.requires_operator_approval is True
        assert result.tier == "T2"

    def test_t3_action_requires_approval(self):
        bridge = GovernanceBridge(
            risk_classifier=MockRiskClassifier(default_tier=3),
        )
        result = bridge.validate_action("deploy")
        assert result.approved is False
        assert result.requires_operator_approval is True
        assert result.tier == "T3"

    def test_no_classifier_defaults_conservative(self):
        bridge = GovernanceBridge()
        result = bridge.validate_action("unknown_action")
        assert result.approved is False
        assert result.tier == "T2"

    def test_mcp_sentry_denial(self):
        bridge = GovernanceBridge(
            risk_classifier=MockRiskClassifier(default_tier=0),
            mcp_sentry=MockMCPSentry(allow_all=False),
        )
        result = bridge.validate_action("blocked_capability")
        assert result.approved is False
        assert "MCP Sentry denied" in result.reason

    def test_mcp_sentry_allows(self):
        bridge = GovernanceBridge(
            risk_classifier=MockRiskClassifier(default_tier=0),
            mcp_sentry=MockMCPSentry(allow_all=True),
        )
        result = bridge.validate_action("allowed_capability")
        assert result.approved is True


class TestKillSwitches:
    def test_kill_switch_reads_feature_flag(self, monkeypatch):
        bridge = GovernanceBridge()
        # When flag module isn't available, returns False
        result = bridge.check_kill_switches()
        assert isinstance(result, bool)


class TestUpdateTrust:
    def test_record_success(self):
        ledger = MockTrustLedger()
        bridge = GovernanceBridge(trust_ledger=ledger)
        bridge.update_trust("classify", "workspace", success=True)
        assert len(ledger.successes) == 1

    def test_record_failure(self):
        ledger = MockTrustLedger()
        bridge = GovernanceBridge(trust_ledger=ledger)
        bridge.update_trust("classify", "workspace", success=False)
        assert len(ledger.failures) == 1

    def test_no_ledger_is_noop(self):
        bridge = GovernanceBridge()
        # Should not raise
        bridge.update_trust("classify", "workspace", success=True)


class TestRequestApproval:
    def test_returns_false_by_default(self):
        bridge = GovernanceBridge()
        result = bridge.request_approval("deploy", "agent-1")
        assert result is False
