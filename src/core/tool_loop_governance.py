from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("src.core.orchestrator")


def governance_scope_from_inputs(inputs: dict[str, Any] | None) -> str:
    inputs = inputs or {}
    return str(inputs.get("url", inputs.get("command", inputs.get("path", "default"))))


def governance_tier_for_tool(skill_name: str) -> Any:
    from governance.models import RiskTier

    tier_map = {
        "network_client": RiskTier.T2_CONTROLLED,
        "command_runner": RiskTier.T2_CONTROLLED,
        "repo_writer": RiskTier.T1_REVERSIBLE,
        "service_runner": RiskTier.T2_CONTROLLED,
    }
    return tier_map.get(skill_name, RiskTier.T0_INERT)


def record_tool_governance_event(
    runtime: Any,
    skill_name: str,
    inputs: dict[str, Any] | None,
    success: bool,
    *,
    source: str,
) -> None:
    try:
        tier = governance_tier_for_tool(skill_name)
        scope = governance_scope_from_inputs(inputs)
        runtime._record_governance_event(skill_name, scope, tier, success)
    except Exception as exc:
        _logger.warning(
            "Failed to record governance event for %s agentic tool %s: %s",
            source,
            skill_name,
            exc,
        )
