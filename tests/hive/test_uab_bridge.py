"""Tests for HIVE UAB Bridge."""

import pytest

from src.core.soul.store import AutonomyPosture, Soul
from src.hive.integration.uab_bridge import UABBridge
from src.hive.integration.governance_bridge import GovernanceBridge, GovernanceResult
from src.hive.errors import ScopedSoulViolationError, UABControlError


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


class MockRiskClassifier:
    def __init__(self, default_tier=0):
        self._default_tier = default_tier

    def classify(self, capability, scope="workspace", target=None):
        from unittest.mock import MagicMock
        profile = MagicMock()
        profile.tier = MagicMock()
        profile.tier.value = self._default_tier
        return profile


class MockTrustLedger:
    def __init__(self):
        self.successes = []
        self.failures = []

    def get_effective_tier(self, capability, scope):
        return None

    def record_success(self, capability, scope):
        self.successes.append((capability, scope))

    def record_failure(self, capability, scope):
        self.failures.append((capability, scope))


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

    async def test_enumerate_rejects_app_outside_allowed_apps(self):
        provider = MockUABProvider()
        bridge = UABBridge(uab_provider=provider)
        with pytest.raises(
            ScopedSoulViolationError,
            match="Scoped Soul forbids desktop app 'chrome'",
        ):
            await bridge.enumerate(
                "chrome",
                agent_id="a1",
                allowed_apps=["notepad"],
            )
        assert provider.calls == []

    async def test_query_allows_query_scoped_category(self):
        provider = MockUABProvider()
        bridge = UABBridge(uab_provider=provider)
        result = await bridge.query(
            "notepad",
            "title",
            agent_id="a1",
            allowed_categories=["query"],
        )
        assert result["result"] == "query_result"

    async def test_query_requires_approval_when_scoped_soul_demands_it(self):
        provider = MockUABProvider()
        bridge = UABBridge(uab_provider=provider)
        scoped_soul = Soul(
            version="v1",
            mission="Test",
            allegiance="Test",
            autonomy_posture=AutonomyPosture(
                level="scoped",
                description="Query requires approval",
                allowed_autonomous=["uab_state"],
                requires_approval=["uab_query"],
            ),
        )

        with pytest.raises(
            ScopedSoulViolationError,
            match="requires operator approval for UAB capability 'uab_query'",
        ):
            await bridge.query(
                "notepad",
                "title",
                agent_id="a1",
                scoped_soul=scoped_soul,
            )
        assert provider.calls == []

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

    async def test_act_updates_trust_on_success(self):
        provider = MockUABProvider()
        ledger = MockTrustLedger()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=GovernanceBridge(
                risk_classifier=MockRiskClassifier(default_tier=0),
                trust_ledger=ledger,
            ),
        )

        result = await bridge.act("notepad", "click", {"target": "ok"}, agent_id="a1")

        assert result["success"] is True
        assert ledger.successes == [("uab_click", "notepad")]
        assert ledger.failures == []

    async def test_act_updates_trust_on_failure(self):
        class FailingUABProvider(MockUABProvider):
            def act(self, app_name, action, params):
                self.calls.append(("act", app_name, action, params))
                return {"success": False, "error": "boom"}

        provider = FailingUABProvider()
        ledger = MockTrustLedger()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=GovernanceBridge(
                risk_classifier=MockRiskClassifier(default_tier=0),
                trust_ledger=ledger,
            ),
        )

        result = await bridge.act("notepad", "click", {"target": "ok"}, agent_id="a1")

        assert result["success"] is False
        assert ledger.successes == []
        assert ledger.failures == [("uab_click", "notepad")]

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

    async def test_act_rejects_app_outside_allowed_apps(self):
        provider = MockUABProvider()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=MockGovernanceBridgeApprove(),
        )
        with pytest.raises(
            ScopedSoulViolationError,
            match="Scoped Soul forbids desktop app 'chrome'",
        ):
            await bridge.act(
                "chrome",
                "click",
                {"target": "ok"},
                agent_id="a1",
                allowed_apps=["notepad"],
            )
        assert provider.calls == []

    async def test_act_rejects_uab_mutation_outside_query_only_scope(self):
        provider = MockUABProvider()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=MockGovernanceBridgeApprove(),
        )
        with pytest.raises(
            ScopedSoulViolationError,
            match="outside scoped categories",
        ):
            await bridge.act(
                "notepad",
                "click",
                {"target": "ok"},
                agent_id="a1",
                allowed_categories=["query"],
            )
        assert provider.calls == []

    async def test_act_honors_explicit_scoped_soul_capabilities(self):
        provider = MockUABProvider()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=MockGovernanceBridgeApprove(),
        )
        scoped_soul = Soul(
            version="v1",
            mission="Test",
            allegiance="Test",
            autonomy_posture=AutonomyPosture(
                level="scoped",
                description="Query only",
                allowed_autonomous=["uab_query", "uab_state"],
                requires_approval=[],
            ),
        )

        with pytest.raises(
            ScopedSoulViolationError,
            match="does not permit UAB capability 'uab_click'",
        ):
            await bridge.act(
                "notepad",
                "click",
                {"target": "ok"},
                agent_id="a1",
                scoped_soul=scoped_soul,
            )
        assert provider.calls == []

    async def test_act_requires_approval_when_scoped_soul_demands_it(self):
        provider = MockUABProvider()
        bridge = UABBridge(
            uab_provider=provider,
            governance_bridge=MockGovernanceBridgeApprove(),
        )
        scoped_soul = Soul(
            version="v1",
            mission="Test",
            allegiance="Test",
            autonomy_posture=AutonomyPosture(
                level="scoped",
                description="Click requires approval",
                allowed_autonomous=["uab_query", "uab_state"],
                requires_approval=["uab_click"],
            ),
        )

        with pytest.raises(
            ScopedSoulViolationError,
            match="requires operator approval for UAB capability 'uab_click'",
        ):
            await bridge.act(
                "notepad",
                "click",
                {"target": "ok"},
                agent_id="a1",
                scoped_soul=scoped_soul,
            )
        assert provider.calls == []
