"""
Tests for Prompts 54-55: CapabilityEnforcer Core + Wiring.
"""

import pytest
from unittest.mock import MagicMock
from src.skills.security.capability_enforcer import (
    CapabilityEnforcer,
    EnforcementResult,
)
from src.skills.security.manifest import validate_manifest


def _make_manifest():
    """Create a valid manifest for testing."""
    return validate_manifest({
        "id": "slack-reader",
        "name": "Slack Reader",
        "version": "1.0.0",
        "author": "test",
        "source": "first-party",
        "capabilities_required": [
            {"capability": "connector.read", "description": "Read Slack messages"},
        ],
        "capabilities_optional": [
            {"capability": "memory.read", "description": "Read memory"},
        ],
        "credentials": [
            {"vault_key": "slack.bot_token", "type": "bearer", "purpose": "API"},
        ],
        "target_domains": ["slack.com"],
        "does_not_access": ["DMs"],
    })


@pytest.fixture
def enforcer():
    return CapabilityEnforcer()


@pytest.fixture
def registered_enforcer(enforcer):
    manifest = _make_manifest()
    enforcer.register_skill("slack-reader", manifest)
    return enforcer


# ── Prompt 54: Core ──────────────────────────────────────────────

class TestEnforceApproved:
    def test_approved_capability_allowed(self, registered_enforcer):
        result = registered_enforcer.enforce("slack-reader", "connector.read")
        assert result.allowed is True

    def test_unapproved_capability_blocked(self, registered_enforcer):
        result = registered_enforcer.enforce("slack-reader", "connector.write")
        assert result.allowed is False
        assert result.violation_type == "capability"

    def test_undeclared_domain_blocked(self, registered_enforcer):
        result = registered_enforcer.enforce(
            "slack-reader", "connector.read", target_domain="evil.com"
        )
        assert result.allowed is False
        assert result.violation_type == "domain"

    def test_undeclared_vault_key_blocked(self, registered_enforcer):
        result = registered_enforcer.enforce(
            "slack-reader", "connector.read", vault_key="secret.key"
        )
        assert result.allowed is False
        assert result.violation_type == "credential"


class TestUnregister:
    def test_unregister_removes_approvals(self, registered_enforcer):
        registered_enforcer.unregister_skill("slack-reader")
        result = registered_enforcer.enforce("slack-reader", "connector.read")
        assert result.allowed is False

    def test_enforce_after_unregister_blocked(self, registered_enforcer):
        registered_enforcer.unregister_skill("slack-reader")
        result = registered_enforcer.enforce("slack-reader", "connector.read")
        assert result.allowed is False
        assert result.violation_type == "capability"


class TestListApprovals:
    def test_returns_correct_sets(self, registered_enforcer):
        approvals = registered_enforcer.list_approvals("slack-reader")
        assert "connector.read" in approvals["capabilities"]
        assert "memory.read" in approvals["capabilities"]
        assert "slack.com" in approvals["domains"]
        assert "slack.bot_token" in approvals["vault_keys"]


class TestMultipleSkills:
    def test_independent_approval_sets(self, enforcer):
        m1 = _make_manifest()
        m2 = validate_manifest({
            "id": "email-sender",
            "name": "Email Sender",
            "version": "1.0.0",
            "author": "test",
            "source": "first-party",
            "capabilities_required": [
                {"capability": "connector.write", "description": "Send email"},
            ],
            "credentials": [
                {"vault_key": "email.token", "type": "bearer", "purpose": "API"},
            ],
            "target_domains": ["gmail.com"],
            "does_not_access": ["Contacts"],
        })
        enforcer.register_skill("slack-reader", m1)
        enforcer.register_skill("email-sender", m2)

        # slack-reader can read but not write
        assert enforcer.enforce("slack-reader", "connector.read").allowed is True
        assert enforcer.enforce("slack-reader", "connector.write").allowed is False

        # email-sender can write but not read
        assert enforcer.enforce("email-sender", "connector.write").allowed is True
        assert enforcer.enforce("email-sender", "connector.read").allowed is False


# ── Prompt 55: Wiring ────────────────────────────────────────────

class TestEnforcementHook:
    def test_raises_for_undeclared(self, registered_enforcer):
        hook = registered_enforcer.create_enforcement_hook()
        with pytest.raises(PermissionError, match="connector.write"):
            hook("slack-reader", "connector.write")

    def test_allows_declared(self, registered_enforcer):
        hook = registered_enforcer.create_enforcement_hook()
        result = hook("slack-reader", "connector.read")
        assert result.allowed is True

    def test_violation_emitted(self, registered_enforcer):
        hook = registered_enforcer.create_enforcement_hook()
        try:
            hook("slack-reader", "connector.delete")
        except PermissionError:
            pass
        assert len(registered_enforcer._violation_log) >= 1


class TestActiveSkill:
    def test_set_and_get(self, enforcer):
        enforcer.set_active_skill("test-skill")
        assert enforcer.get_active_skill() == "test-skill"

    def test_clear(self, enforcer):
        enforcer.set_active_skill("test-skill")
        enforcer.set_active_skill(None)
        assert enforcer.get_active_skill() is None


class TestFullFlow:
    def test_register_set_active_enforce_block(self, registered_enforcer):
        registered_enforcer.set_active_skill("slack-reader")
        hook = registered_enforcer.create_enforcement_hook()

        # Declared: allowed
        hook("slack-reader", "connector.read", target_domain="slack.com",
             vault_key="slack.bot_token")

        # Undeclared capability: blocked
        with pytest.raises(PermissionError):
            hook("slack-reader", "connector.delete")

        # Undeclared domain: blocked
        with pytest.raises(PermissionError):
            hook("slack-reader", "connector.read", target_domain="evil.com")
