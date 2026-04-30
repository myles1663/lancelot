"""
Canonical kill switch contract for feature-flag-backed kill gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class KillSwitchScope(str, Enum):
    SUBSYSTEM = "subsystem"
    MCP_MASTER = "mcp_master"
    MCP_SERVER = "mcp_server"
    FEDERATION = "federation"
    RUNTIME_PAUSE = "runtime_pause"


@dataclass(slots=True)
class KillSwitchDecision:
    allowed: bool
    switch_id: str = ""
    scope: KillSwitchScope = KillSwitchScope.SUBSYSTEM
    source: str = "feature_flag"
    reason: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "allowed": self.allowed,
            "switch_id": self.switch_id,
            "scope": self.scope.value,
            "source": self.source,
            "reason": self.reason,
        }


def _read_feature_flag_state(flag_name: str, *, missing_default: bool) -> bool:
    try:
        import feature_flags

        enabled = getattr(feature_flags, flag_name, None)
        if enabled is None:
            enabled = feature_flags.get_persisted_flag_state(flag_name, missing_default)
        return bool(enabled)
    except ImportError:
        return missing_default


def evaluate_feature_flag_kill_switch(
    *,
    flag_name: str,
    switch_id: str,
    scope: KillSwitchScope,
    missing_default: bool,
    blocked_reason: str,
) -> KillSwitchDecision:
    enabled = _read_feature_flag_state(flag_name, missing_default=missing_default)
    if not enabled:
        return KillSwitchDecision(
            allowed=False,
            switch_id=switch_id,
            scope=scope,
            reason=blocked_reason,
        )
    return KillSwitchDecision(
        allowed=True,
        switch_id=switch_id,
        scope=scope,
    )
