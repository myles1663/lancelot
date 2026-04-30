from src.core.kill_switches import (
    KillSwitchDecision,
    KillSwitchScope,
    evaluate_feature_flag_kill_switch,
)


def test_kill_switch_decision_serializes_scope_and_reason():
    decision = KillSwitchDecision(
        allowed=False,
        switch_id="FEATURE_MCP",
        scope=KillSwitchScope.MCP_MASTER,
        reason="blocked",
    )

    assert decision.to_dict() == {
        "allowed": False,
        "switch_id": "FEATURE_MCP",
        "scope": "mcp_master",
        "source": "feature_flag",
        "reason": "blocked",
    }


def test_feature_flag_kill_switch_fails_closed_when_master_disabled(monkeypatch):
    monkeypatch.setattr(
        "src.core.kill_switches._read_feature_flag_state",
        lambda flag_name, missing_default: False,
    )

    decision = evaluate_feature_flag_kill_switch(
        flag_name="FEATURE_MCP",
        switch_id="FEATURE_MCP",
        scope=KillSwitchScope.MCP_MASTER,
        missing_default=False,
        blocked_reason="blocked",
    )

    assert decision.allowed is False
    assert decision.scope == KillSwitchScope.MCP_MASTER
    assert decision.reason == "blocked"


def test_feature_flag_kill_switch_can_fail_open_for_dynamic_server_flags(monkeypatch):
    monkeypatch.setattr(
        "src.core.kill_switches._read_feature_flag_state",
        lambda flag_name, missing_default: True,
    )

    decision = evaluate_feature_flag_kill_switch(
        flag_name="FEATURE_MCP_SERVER_GITHUB",
        switch_id="MCP_SERVER_GITHUB",
        scope=KillSwitchScope.MCP_SERVER,
        missing_default=True,
        blocked_reason="blocked",
    )

    assert decision.allowed is True
    assert decision.scope == KillSwitchScope.MCP_SERVER
