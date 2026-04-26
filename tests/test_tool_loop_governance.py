import logging

from governance.models import RiskTier
from tool_loop_governance import (
    governance_scope_from_inputs,
    governance_tier_for_tool,
    record_tool_governance_event,
)


def test_governance_scope_prefers_url_command_path_then_default():
    assert governance_scope_from_inputs({"url": "https://example.com", "command": "echo ok"}) == "https://example.com"
    assert governance_scope_from_inputs({"command": "pytest -q", "path": "README.md"}) == "pytest -q"
    assert governance_scope_from_inputs({"path": "README.md"}) == "README.md"
    assert governance_scope_from_inputs({}) == "default"
    assert governance_scope_from_inputs(None) == "default"


def test_governance_tier_for_tool_maps_agentic_tools_to_expected_risk_tiers():
    assert governance_tier_for_tool("network_client") == RiskTier.T2_CONTROLLED
    assert governance_tier_for_tool("command_runner") == RiskTier.T2_CONTROLLED
    assert governance_tier_for_tool("service_runner") == RiskTier.T2_CONTROLLED
    assert governance_tier_for_tool("repo_writer") == RiskTier.T1_REVERSIBLE
    assert governance_tier_for_tool("unknown_tool") == RiskTier.T0_INERT


def test_record_tool_governance_event_uses_normalized_scope_and_tier():
    class Runtime:
        def __init__(self):
            self.events = []

        def _record_governance_event(self, capability, scope, tier, success):
            self.events.append((capability, scope, tier, success))

    runtime = Runtime()

    record_tool_governance_event(
        runtime,
        "repo_writer",
        {"path": "src/core/tool_loop.py"},
        True,
        source="local",
    )

    assert runtime.events == [
        ("repo_writer", "src/core/tool_loop.py", RiskTier.T1_REVERSIBLE, True)
    ]


def test_record_tool_governance_event_does_not_fail_tool_execution(caplog):
    class Runtime:
        def _record_governance_event(self, capability, scope, tier, success):
            raise RuntimeError("ledger unavailable")

    with caplog.at_level(logging.WARNING, logger="src.core.orchestrator"):
        record_tool_governance_event(
            Runtime(),
            "network_client",
            {"url": "https://example.com"},
            False,
            source="flagship",
        )

    assert "Failed to record governance event for flagship agentic tool network_client" in caplog.text
