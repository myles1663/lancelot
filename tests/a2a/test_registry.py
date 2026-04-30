# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""Unit tests for A2A Remote Agent Registry."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import threading
import pytest

from src.a2a.types import (
    RemoteAgent, RemoteAgentStatus, AgentDirection, AgentFramework,
)
from src.a2a.registry import A2ARegistry


@pytest.fixture
def registry(tmp_path):
    """Create an A2ARegistry backed by a temp directory."""
    reg = A2ARegistry(data_dir=str(tmp_path))
    yield reg
    reg.close()


def _make_agent(agent_id="agent-1", display_name="Agent 1", **kwargs):
    """Helper to create a RemoteAgent with defaults."""
    return RemoteAgent(agent_id=agent_id, display_name=display_name, **kwargs)


# ── register / get ──────────────────────────────────────────

class TestRegisterAndGet:
    def test_register_creates_agent(self, registry):
        agent = _make_agent()
        result = registry.register(agent)
        assert result.agent_id == "agent-1"

    def test_register_duplicate_raises(self, registry):
        registry.register(_make_agent())
        with pytest.raises(Exception):
            registry.register(_make_agent())

    def test_get_returns_registered_agent(self, registry):
        registry.register(_make_agent())
        found = registry.get("agent-1")
        assert found is not None
        assert found.agent_id == "agent-1"
        assert found.display_name == "Agent 1"

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("nonexistent") is None


# ── list_agents ─────────────────────────────────────────────

class TestListAgents:
    def test_list_all(self, registry):
        registry.register(_make_agent("a1", "A1"))
        registry.register(_make_agent("a2", "A2"))
        agents = registry.list_agents()
        assert len(agents) == 2

    def test_list_filter_status(self, registry):
        registry.register(_make_agent("a1", "A1"))
        registry.register(_make_agent("a2", "A2"))
        registry.revoke("a1")
        active = registry.list_agents(status="active")
        assert len(active) == 1
        assert active[0].agent_id == "a2"

    def test_list_filter_direction(self, registry):
        registry.register(_make_agent("a1", "A1", direction="inbound"))
        registry.register(_make_agent("a2", "A2", direction="outbound"))
        inbound = registry.list_agents(direction="inbound")
        assert len(inbound) == 1
        assert inbound[0].agent_id == "a1"

    def test_list_filter_direction_includes_both(self, registry):
        registry.register(_make_agent("a1", "A1", direction="both"))
        registry.register(_make_agent("a2", "A2", direction="outbound"))
        inbound = registry.list_agents(direction="inbound")
        # "both" direction should match inbound filter
        assert len(inbound) == 1
        assert inbound[0].agent_id == "a1"

    def test_list_filter_framework(self, registry):
        registry.register(_make_agent("a1", "A1", agent_framework="crewai"))
        registry.register(_make_agent("a2", "A2", agent_framework="langchain"))
        crewai = registry.list_agents(framework="crewai")
        assert len(crewai) == 1
        assert crewai[0].agent_id == "a1"


# ── update ──────────────────────────────────────────────────

class TestUpdate:
    def test_update_modifies_fields(self, registry):
        registry.register(_make_agent())
        agent = registry.get("agent-1")
        agent.display_name = "Updated Name"
        registry.update(agent)
        refreshed = registry.get("agent-1")
        assert refreshed.display_name == "Updated Name"

    def test_update_trust_tiers(self, registry):
        registry.register(_make_agent())
        agent = registry.get("agent-1")
        agent.inbound_trust_tier = 1
        agent.outbound_trust_tier = 3
        registry.update(agent)
        refreshed = registry.get("agent-1")
        assert refreshed.inbound_trust_tier == 1
        assert refreshed.outbound_trust_tier == 3


# ── revoke ──────────────────────────────────────────────────

class TestRevoke:
    def test_revoke_sets_status(self, registry):
        registry.register(_make_agent())
        registry.revoke("agent-1")
        agent = registry.get("agent-1")
        assert agent.status == RemoteAgentStatus.REVOKED.value


# ── auto_register ───────────────────────────────────────────

class TestAutoRegister:
    def test_auto_register_creates_inbound_agent(self, registry):
        agent = registry.auto_register("new-agent", "New Agent")
        assert agent.agent_id == "new-agent"
        assert agent.direction == AgentDirection.INBOUND.value
        assert agent.auto_registered is True

    def test_auto_register_default_trust_tier(self, registry):
        agent = registry.auto_register("new", "New", default_tier=3)
        assert agent.inbound_trust_tier == 3
        assert agent.outbound_trust_tier == 3

    def test_auto_register_existing_returns_existing(self, registry):
        registry.register(_make_agent("existing", "Existing"))
        agent = registry.auto_register("existing", "Different Name")
        assert agent.display_name == "Existing"

    def test_auto_register_populates_network_allowlist(self, registry):
        agent = registry.auto_register(
            "web-agent", "Web Agent",
            card_url="https://api.example.com/.well-known/agent.json",
        )
        assert "api.example.com" in agent.network_allowlist_entries


# ── update_interaction / trust graduation ───────────────────

class TestUpdateInteraction:
    def test_increments_interaction_count(self, registry):
        registry.register(_make_agent())
        registry.update_interaction("agent-1", "completed")
        agent = registry.get("agent-1")
        assert agent.interaction_count == 1

    def test_success_increments_success_count(self, registry):
        registry.register(_make_agent())
        registry.update_interaction("agent-1", "completed")
        agent = registry.get("agent-1")
        assert agent.success_count == 1

    def test_success_records_last_outcome(self, registry):
        registry.register(_make_agent())
        registry.update_interaction("agent-1", "completed")
        agent = registry.get("agent-1")
        assert agent.last_outcome == "completed"
        assert agent.last_interaction != ""

    def test_trust_graduation_t2_to_t1(self, registry):
        """T2 agent graduates to T1 after 20 successes (10 * (3-2+1) = 20)."""
        registry.register(_make_agent(inbound_trust_tier=2))
        for _ in range(20):
            registry.update_interaction("agent-1", "completed", direction="inbound")
        agent = registry.get("agent-1")
        assert agent.inbound_trust_tier == 1

    def test_trust_graduation_t3_to_t2(self, registry):
        """T3 agent graduates to T2 after 10 successes."""
        registry.register(_make_agent(inbound_trust_tier=3))
        # For T3 (3 - 3 + 1 = 1), need 10 * 1 = 10 successes
        for _ in range(10):
            registry.update_interaction("agent-1", "completed", direction="inbound")
        agent = registry.get("agent-1")
        assert agent.inbound_trust_tier == 2

    def test_single_failure_resets_to_t3(self, registry):
        registry.register(_make_agent(inbound_trust_tier=1))
        registry.update_interaction("agent-1", "failed", direction="inbound")
        agent = registry.get("agent-1")
        assert agent.inbound_trust_tier == 3

    def test_failure_resets_outbound_trust(self, registry):
        registry.register(_make_agent(outbound_trust_tier=1))
        registry.update_interaction("agent-1", "failed", direction="outbound")
        agent = registry.get("agent-1")
        assert agent.outbound_trust_tier == 3

    def test_directional_trust_independent(self, registry):
        """Inbound and outbound trust are tracked independently."""
        registry.register(_make_agent(inbound_trust_tier=2, outbound_trust_tier=2))
        # Fail inbound, outbound should remain unchanged
        registry.update_interaction("agent-1", "failed", direction="inbound")
        agent = registry.get("agent-1")
        assert agent.inbound_trust_tier == 3
        assert agent.outbound_trust_tier == 2

    def test_update_interaction_unknown_agent_noop(self, registry):
        """Updating interaction for non-existent agent does nothing."""
        registry.update_interaction("ghost", "completed")
        # Should not raise


# ── Database behavior ───────────────────────────────────────

class TestDatabaseBehavior:
    def test_wal_mode_enabled(self, tmp_path):
        reg = A2ARegistry(data_dir=str(tmp_path))
        conn = reg._get_connection()
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"
        reg.close()

    def test_persistence_across_close_reopen(self, tmp_path):
        reg1 = A2ARegistry(data_dir=str(tmp_path))
        reg1.register(_make_agent("persist", "Persist"))
        reg1.close()

        reg2 = A2ARegistry(data_dir=str(tmp_path))
        agent = reg2.get("persist")
        assert agent is not None
        assert agent.display_name == "Persist"
        reg2.close()

    def test_thread_safety_concurrent_register(self, tmp_path):
        """Concurrent register calls should not corrupt the database."""
        reg = A2ARegistry(data_dir=str(tmp_path))
        errors = []

        def register_agent(idx):
            try:
                reg.register(_make_agent(f"thread-{idx}", f"Thread {idx}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_agent, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        reg.close()
        assert len(errors) == 0

        # Verify all agents registered
        reg2 = A2ARegistry(data_dir=str(tmp_path))
        agents = reg2.list_agents()
        assert len(agents) == 10
        reg2.close()
