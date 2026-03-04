"""Tests for HIVE UAB Bridge."""

import pytest

from src.hive.integration.uab_bridge import UABBridge
from src.hive.integration.governance_bridge import GovernanceBridge, GovernanceResult
from src.hive.errors import UABControlError


class MockUABProvider:
    """Mock UAB provider for testing."""

    def __init__(self):
        self.calls = []

    def detect(self):
        self.calls.append("detect")
        return [{"name": "notepad", "pid": 1234}]

    def enumerate(self, app_name):
        self.calls.append(("enumerate", app_name))
        return {"elements": ["button1", "input1"]}

    def query(self, app_name, query):
        self.calls.append(("query", app_name, query))
        return {"result": "query_result"}

    def act(self, app_name, action, params):
        self.calls.append(("act", app_name, action, params))
        return {"success": True}

    def state(self, app_name):
        self.calls.append(("state", app_name))
        return {"state": "active"}


class MockGovernanceBridgeApprove(GovernanceBridge):
    def validate_action(self, **kwargs):
        return GovernanceResult(approved=True, tier="T0")


class MockGovernanceBridgeDeny(GovernanceBridge):
    def validate_action(self, **kwargs):
        return GovernanceResult(
            approved=False, tier="T3", reason="Denied by test",
        )


class TestUABBridgeAvailability:
    def test_available_with_provider(self):
        bridge = UABBridge(uab_provider=MockUABProvider())
        assert bridge.available is True

    def test_not_available_without_provider(self):
        bridge = UABBridge()
        assert bridge.available is False


@pytest.mark.asyncio
class TestUABBridgeReadOps:
    async def test_get_available_apps(self):
        provider = MockUABProvider()
        bridge = UABBridge(uab_provider=provider)
        apps = await bridge.get_available_apps()
        assert len(apps) == 1
        assert apps[0]["name"] == "notepad"

    async def test_enumerate(self):
        provider = MockUABProvider()
        bridge = UABBridge(uab_provider=provider)
        result = await bridge.enumerate("notepad", agent_id="a1")
        assert "elements" in result

    async def test_query(self):
        provider = MockUABProvider()
        bridge = UABBridge(uab_provider=provider)
        result = await bridge.query("notepad", "title", agent_id="a1")
        assert "result" in result

    async def test_state(self):
        provider = MockUABProvider()
        bridge = UABBridge(uab_provider=provider)
        result = await bridge.state("notepad", agent_id="a1")
        assert "state" in result

    async def test_no_provider_returns_empty(self):
        bridge = UABBridge()
        apps = await bridge.get_available_apps()
        assert apps == []


@pytest.mark.asyncio
class TestUABBridgeMutatingOps:
    async def test_act_with_governance_approval(self):
        provider = MockUABProvider()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=MockGovernanceBridgeApprove(),
        )
        result = await bridge.act("notepad", "click", {"target": "ok"}, agent_id="a1")
        assert result["success"] is True

    async def test_act_governance_denied(self):
        provider = MockUABProvider()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=MockGovernanceBridgeDeny(),
        )
        with pytest.raises(UABControlError, match="Governance denied"):
            await bridge.act("notepad", "click", {"target": "ok"}, agent_id="a1")

    async def test_act_no_provider_raises(self):
        bridge = UABBridge(
            governance_bridge=MockGovernanceBridgeApprove(),
        )
        with pytest.raises(UABControlError, match="not available"):
            await bridge.act("notepad", "click", agent_id="a1")
