# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for Agent Card generation."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock

from src.a2a import agent_card
from src.a2a.agent_card import generate_agent_card, get_cached_card, invalidate_card


def _make_mock_soul(
    version="1.0.0",
    mission="Test mission",
    allow_inbound=True,
    skill_filter=None,
):
    """Build a mock Soul with inbound_a2a_permissions."""
    soul = MagicMock()
    soul.version = version
    soul.mission = mission

    inbound = MagicMock()
    inbound.allow_inbound = allow_inbound
    inbound.skill_filter = skill_filter or []
    soul.inbound_a2a_permissions = inbound

    return soul


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset module-level cache before each test."""
    invalidate_card()
    yield
    invalidate_card()


# ── Basic generation ────────────────────────────────────────

class TestGenerateAgentCard:
    def test_returns_valid_agent_card(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul, base_url="http://localhost:8000")
        assert card.name == "Lancelot Governed Agent"
        assert card.url == "http://localhost:8000"

    def test_includes_soul_mission_as_description(self):
        soul = _make_mock_soul(mission="Defend the realm")
        card = generate_agent_card(soul)
        assert card.description == "Defend the realm"

    def test_includes_soul_skills(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul)
        assert len(card.skills) > 0
        skill_ids = [s.id for s in card.skills]
        assert "chat" in skill_ids
        assert "data_analysis" in skill_ids

    def test_applies_skill_filter(self):
        soul = _make_mock_soul(skill_filter=["chat", "research"])
        card = generate_agent_card(soul)
        skill_ids = [s.id for s in card.skills]
        assert "chat" in skill_ids
        assert "research" in skill_ids
        assert "data_analysis" not in skill_ids
        assert "code_review" not in skill_ids

    def test_empty_skill_filter_advertises_all(self):
        soul = _make_mock_soul(skill_filter=[])
        card = generate_agent_card(soul)
        assert len(card.skills) == 5  # all default skills

    def test_a2a_protocol_version(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul)
        assert card.a2a_protocol_version == "0.2"

    def test_authentication_config_default(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul)
        assert card.authentication == {"type": "bearer_token"}

    def test_authentication_config_custom(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul, auth_scheme={"type": "api_key"})
        assert card.authentication == {"type": "api_key"}


# ── Governance declaration ──────────────────────────────────

class TestGovernanceDeclaration:
    def test_includes_governance_declaration(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul)
        assert card.governance_declaration is not None
        assert card.governance_declaration["governance_framework"] == "lancelot"

    def test_governance_includes_receipt_system(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul)
        assert card.governance_declaration["receipt_system"] is True

    def test_governance_includes_soul_version_hash(self):
        soul = _make_mock_soul(version="2.0.0")
        card = generate_agent_card(soul)
        assert "soul_version_hash" in card.governance_declaration
        assert len(card.governance_declaration["soul_version_hash"]) == 16

    def test_no_governance_when_disabled(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul, include_governance_declaration=False)
        assert card.governance_declaration is None


# ── Caching ─────────────────────────────────────────────────

class TestCaching:
    def test_caches_by_soul_hash(self):
        soul = _make_mock_soul()
        card1 = generate_agent_card(soul)
        card2 = generate_agent_card(soul)
        assert card1 is card2  # same object from cache

    def test_cache_invalidation_on_soul_change(self):
        soul1 = _make_mock_soul(version="1.0")
        card1 = generate_agent_card(soul1)

        soul2 = _make_mock_soul(version="2.0")
        card2 = generate_agent_card(soul2)
        assert card1 is not card2

    def test_invalidate_card_forces_regeneration(self):
        soul = _make_mock_soul()
        card1 = generate_agent_card(soul)
        invalidate_card()
        card2 = generate_agent_card(soul)
        assert card1 is not card2

    def test_get_cached_card_returns_none_initially(self):
        assert get_cached_card() is None

    def test_get_cached_card_returns_after_generation(self):
        soul = _make_mock_soul()
        card = generate_agent_card(soul)
        assert get_cached_card() is card


# ── Edge: no inbound permissions ────────────────────────────

class TestNoInboundPermissions:
    def test_soul_without_inbound_permissions(self):
        soul = MagicMock()
        soul.version = "1.0"
        soul.mission = "Minimal"
        soul.inbound_a2a_permissions = None
        card = generate_agent_card(soul)
        # Should still generate a card with default/fallback skills
        assert card.name == "Lancelot Governed Agent"
        assert len(card.skills) == 5  # all defaults when no filter
