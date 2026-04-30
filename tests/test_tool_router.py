"""
Unit Tests for Tool Fabric Router and Health
=============================================

Tests for:
- HealthMonitor: provider registration, probing, caching
- ProviderRouter: provider selection, failover, policy integration
- Route decisions and health summaries

Prompt 4 — Router + Health
"""

import pytest
import time
import threading
from unittest.mock import MagicMock, patch

from src.tools.health import (
    HealthMonitor,
    HealthConfig,
    get_health_monitor,
    reset_health_monitor,
)
from src.tools.router import (
    ProviderRouter,
    RouterConfig,
    RouteDecision,
    get_router,
    reset_router,
)
from src.tools.contracts import (
    Capability,
    RiskLevel,
    ProviderHealth,
    ProviderState,
    ToolIntent,
    BaseProvider,
)
from src.tools.policies import PolicyEngine, PolicyConfig


# =============================================================================
# Mock Provider
# =============================================================================


class MockProvider(BaseProvider):
    """Mock provider for testing."""

    def __init__(
        self,
        provider_id: str,
        capabilities: list,
        health_state: ProviderState = ProviderState.HEALTHY,
        version: str = "1.0.0",
    ):
        self._provider_id = provider_id
        self._capabilities = capabilities
        self._health_state = health_state
        self._version = version
        self._probe_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> list:
        return self._capabilities

    def health_check(self) -> ProviderHealth:
        self._probe_count += 1
        return ProviderHealth(
            provider_id=self._provider_id,
            state=self._health_state,
            version=self._version,
            capabilities=[c.value for c in self._capabilities],
        )

    def set_health(self, state: ProviderState):
        """Change health state for testing."""
        self._health_state = state


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global instances before each test."""
    reset_health_monitor()
    reset_router()
    yield
    reset_health_monitor()
    reset_router()


@pytest.fixture
def health_config():
    """Create health config for testing."""
    return HealthConfig(
        probe_timeout_s=5,
        cache_ttl_s=1,  # Short TTL for testing
        retry_offline_after_s=10,
    )


@pytest.fixture
def health_monitor(health_config):
    """Create a HealthMonitor instance."""
    return HealthMonitor(health_config)


@pytest.fixture
def mock_sandbox():
    """Create a mock local sandbox provider."""
    return MockProvider(
        provider_id="local_sandbox",
        capabilities=[
            Capability.SHELL_EXEC,
            Capability.REPO_OPS,
            Capability.FILE_OPS,
            Capability.DEPLOY_OPS,
        ],
        health_state=ProviderState.HEALTHY,
    )


@pytest.fixture
def mock_ui_templates():
    """Create a mock UI templates provider."""
    return MockProvider(
        provider_id="ui_templates",
        capabilities=[Capability.UI_BUILDER],
        health_state=ProviderState.HEALTHY,
    )


@pytest.fixture
def mock_antigravity():
    """Create a mock Antigravity provider."""
    return MockProvider(
        provider_id="ui_antigravity",
        capabilities=[Capability.UI_BUILDER],
        health_state=ProviderState.OFFLINE,  # Offline by default
    )


@pytest.fixture
def mock_vision():
    """Create a mock Vision provider."""
    return MockProvider(
        provider_id="vision_antigravity",
        capabilities=[Capability.VISION_CONTROL],
        health_state=ProviderState.HEALTHY,
    )


# =============================================================================
# HealthConfig Tests
# =============================================================================


class TestHealthConfig:
    """Test HealthConfig dataclass."""

    def test_default_config(self):
        """Default config has sensible values."""
        config = HealthConfig()
        assert config.probe_timeout_s == 10
        assert config.cache_ttl_s == 60
        assert config.run_sweep_on_startup is True

    def test_custom_config(self):
        """Config accepts custom values."""
        config = HealthConfig(
            probe_timeout_s=5,
            cache_ttl_s=30,
            parallel_probes=False,
        )
        assert config.probe_timeout_s == 5
        assert config.cache_ttl_s == 30
        assert config.parallel_probes is False


# =============================================================================
# HealthMonitor Registration Tests
# =============================================================================


class TestHealthMonitorRegistration:
    """Test provider registration."""

    def test_register_provider(self, health_monitor, mock_sandbox):
        """Provider can be registered."""
        health_monitor.register(mock_sandbox)
        assert "local_sandbox" in health_monitor.list_providers()

    def test_unregister_provider(self, health_monitor, mock_sandbox):
        """Provider can be unregistered."""
        health_monitor.register(mock_sandbox)
        health_monitor.unregister("local_sandbox")
        assert "local_sandbox" not in health_monitor.list_providers()

    def test_get_provider(self, health_monitor, mock_sandbox):
        """Can retrieve registered provider."""
        health_monitor.register(mock_sandbox)
        provider = health_monitor.get_provider("local_sandbox")
        assert provider is mock_sandbox

    def test_get_nonexistent_provider(self, health_monitor):
        """Returns None for unregistered provider."""
        provider = health_monitor.get_provider("nonexistent")
        assert provider is None

    def test_list_providers(self, health_monitor, mock_sandbox, mock_ui_templates):
        """List all registered providers."""
        health_monitor.register(mock_sandbox)
        health_monitor.register(mock_ui_templates)

        providers = health_monitor.list_providers()
        assert len(providers) == 2
        assert "local_sandbox" in providers
        assert "ui_templates" in providers


# =============================================================================
# HealthMonitor Probe Tests
# =============================================================================


class TestHealthMonitorProbes:
    """Test health probing."""

    def test_probe_healthy_provider(self, health_monitor, mock_sandbox):
        """Probing healthy provider returns HEALTHY."""
        health_monitor.register(mock_sandbox)
        health = health_monitor.probe("local_sandbox")

        assert health.state == ProviderState.HEALTHY
        assert health.provider_id == "local_sandbox"
        assert health.version == "1.0.0"

    def test_probe_offline_provider(self, health_monitor, mock_antigravity):
        """Probing offline provider returns OFFLINE."""
        health_monitor.register(mock_antigravity)
        health = health_monitor.probe("ui_antigravity")

        assert health.state == ProviderState.OFFLINE

    def test_probe_unregistered_provider(self, health_monitor):
        """Probing unregistered provider returns OFFLINE."""
        health = health_monitor.probe("nonexistent")

        assert health.state == ProviderState.OFFLINE
        assert "not registered" in health.error_message.lower()

    def test_probe_caching(self, health_monitor, mock_sandbox):
        """Health is cached between probes."""
        health_monitor.register(mock_sandbox)

        # First probe
        health_monitor.probe("local_sandbox")
        probe_count_1 = mock_sandbox._probe_count

        # Second probe (should use cache)
        health_monitor.probe("local_sandbox")
        probe_count_2 = mock_sandbox._probe_count

        # Only one actual probe should have occurred
        assert probe_count_2 == probe_count_1

    def test_probe_force_refresh(self, health_monitor, mock_sandbox):
        """Force flag bypasses cache."""
        health_monitor.register(mock_sandbox)

        health_monitor.probe("local_sandbox")
        probe_count_1 = mock_sandbox._probe_count

        health_monitor.probe("local_sandbox", force=True)
        probe_count_2 = mock_sandbox._probe_count

        # Force should trigger new probe
        assert probe_count_2 == probe_count_1 + 1

    def test_probe_cache_expiry(self, health_config, mock_sandbox):
        """Stale cache triggers new probe."""
        # Use very short TTL
        health_config.cache_ttl_s = 0.1
        monitor = HealthMonitor(health_config)
        monitor.register(mock_sandbox)

        monitor.probe("local_sandbox")
        probe_count_1 = mock_sandbox._probe_count

        # Wait for cache to expire
        time.sleep(0.2)

        monitor.probe("local_sandbox")
        probe_count_2 = mock_sandbox._probe_count

        assert probe_count_2 > probe_count_1

    def test_probe_all(self, health_monitor, mock_sandbox, mock_ui_templates):
        """Probe all registered providers."""
        health_monitor.register(mock_sandbox)
        health_monitor.register(mock_ui_templates)

        results = health_monitor.probe_all(force=True)

        assert len(results) == 2
        assert results["local_sandbox"].state == ProviderState.HEALTHY
        assert results["ui_templates"].state == ProviderState.HEALTHY


# =============================================================================
# HealthMonitor Query Tests
# =============================================================================


class TestHealthMonitorQueries:
    """Test health queries."""

    def test_is_healthy(self, health_monitor, mock_sandbox):
        """is_healthy returns True for healthy provider."""
        health_monitor.register(mock_sandbox)
        health_monitor.probe("local_sandbox")

        assert health_monitor.is_healthy("local_sandbox") is True

    def test_is_healthy_offline(self, health_monitor, mock_antigravity):
        """is_healthy returns False for offline provider."""
        health_monitor.register(mock_antigravity)
        health_monitor.probe("ui_antigravity")

        assert health_monitor.is_healthy("ui_antigravity") is False

    def test_is_available(self, health_monitor, mock_sandbox):
        """is_available returns True for healthy/degraded providers."""
        health_monitor.register(mock_sandbox)
        health_monitor.probe("local_sandbox")

        assert health_monitor.is_available("local_sandbox") is True

    def test_is_available_degraded(self, health_monitor, mock_sandbox):
        """is_available returns True for degraded provider."""
        mock_sandbox.set_health(ProviderState.DEGRADED)
        health_monitor.register(mock_sandbox)
        health_monitor.probe("local_sandbox")

        assert health_monitor.is_available("local_sandbox") is True
        assert health_monitor.is_healthy("local_sandbox") is False

    def test_get_healthy_providers(self, health_monitor, mock_sandbox, mock_antigravity):
        """Get list of healthy providers."""
        health_monitor.register(mock_sandbox)
        health_monitor.register(mock_antigravity)
        health_monitor.probe_all(force=True)

        healthy = health_monitor.get_healthy_providers()
        assert "local_sandbox" in healthy
        assert "ui_antigravity" not in healthy

    def test_get_providers_for_capability(
        self, health_monitor, mock_sandbox, mock_ui_templates
    ):
        """Get providers supporting a capability."""
        health_monitor.register(mock_sandbox)
        health_monitor.register(mock_ui_templates)

        shell_providers = health_monitor.get_providers_for_capability(
            Capability.SHELL_EXEC
        )
        assert "local_sandbox" in shell_providers
        assert "ui_templates" not in shell_providers

        ui_providers = health_monitor.get_providers_for_capability(
            Capability.UI_BUILDER
        )
        assert "ui_templates" in ui_providers


# =============================================================================
# HealthMonitor Summary Tests
# =============================================================================


class TestHealthMonitorSummary:
    """Test health summary."""

    def test_get_summary(
        self, health_monitor, mock_sandbox, mock_ui_templates, mock_antigravity
    ):
        """Get health summary."""
        health_monitor.register(mock_sandbox)
        health_monitor.register(mock_ui_templates)
        health_monitor.register(mock_antigravity)
        health_monitor.probe_all(force=True)

        summary = health_monitor.get_summary()

        assert summary["total_providers"] == 3
        assert summary["states"]["healthy"] == 2
        assert summary["states"]["offline"] == 1


# =============================================================================
# RouterConfig Tests
# =============================================================================


class TestRouterConfig:
    """Test RouterConfig dataclass."""

    def test_default_config(self):
        """Default config has preferences for all capabilities."""
        config = RouterConfig()
        assert "shell_exec" in config.provider_preferences
        assert "ui_builder" in config.provider_preferences
        assert config.fallback_provider == "local_sandbox"

    def test_custom_config(self):
        """Config accepts custom values."""
        config = RouterConfig(
            fallback_provider=None,
            require_healthy=True,
        )
        assert config.fallback_provider is None
        assert config.require_healthy is True


# =============================================================================
# ProviderRouter Selection Tests
# =============================================================================


class TestProviderRouterSelection:
    """Test provider selection."""

    def test_select_healthy_provider(self, health_monitor, mock_sandbox):
        """Selects healthy provider for capability."""
        health_monitor.register(mock_sandbox)
        router = ProviderRouter(health_monitor=health_monitor)

        decision = router.select_provider(Capability.SHELL_EXEC)

        assert decision.success is True
        assert decision.provider_id == "local_sandbox"
        assert decision.health_state == ProviderState.HEALTHY

    def test_select_with_multiple_providers(
        self, health_monitor, mock_ui_templates, mock_antigravity
    ):
        """Selects first healthy provider from preferences."""
        health_monitor.register(mock_ui_templates)
        health_monitor.register(mock_antigravity)
        router = ProviderRouter(health_monitor=health_monitor)

        decision = router.select_provider(Capability.UI_BUILDER)

        # ui_templates is healthy, ui_antigravity is offline
        assert decision.success is True
        assert decision.provider_id == "ui_templates"

    def test_select_skips_offline_provider(
        self, health_monitor, mock_ui_templates, mock_antigravity
    ):
        """Skips offline providers in selection."""
        # Make ui_templates offline, antigravity healthy
        mock_ui_templates.set_health(ProviderState.OFFLINE)
        mock_antigravity.set_health(ProviderState.HEALTHY)

        health_monitor.register(mock_ui_templates)
        health_monitor.register(mock_antigravity)
        router = ProviderRouter(health_monitor=health_monitor)

        decision = router.select_provider(Capability.UI_BUILDER)

        assert decision.success is True
        assert decision.provider_id == "ui_antigravity"
        assert "ui_templates:offline" in decision.alternatives_tried

    def test_select_no_provider_available(self, health_monitor, mock_antigravity):
        """Returns failure when no provider available."""
        # Only register offline provider
        health_monitor.register(mock_antigravity)

        # Create router without fallback for UI_BUILDER
        config = RouterConfig(fallback_provider=None)
        router = ProviderRouter(config=config, health_monitor=health_monitor)

        decision = router.select_provider(Capability.UI_BUILDER)

        assert decision.success is False
        assert decision.provider_id is None
        assert "No provider available" in decision.reason

    def test_select_uses_fallback(self, health_monitor, mock_sandbox):
        """Uses fallback when preferred providers offline."""
        health_monitor.register(mock_sandbox)
        router = ProviderRouter(health_monitor=health_monitor)

        # Select for capability that sandbox supports but isn't preferred
        # Sandbox should be used as fallback
        decision = router.select_provider(Capability.SHELL_EXEC)

        assert decision.success is True
        assert decision.provider_id == "local_sandbox"

    def test_select_with_hint(self, health_monitor, mock_sandbox, mock_ui_templates):
        """Hint provider is tried first."""
        health_monitor.register(mock_sandbox)
        health_monitor.register(mock_ui_templates)
        router = ProviderRouter(health_monitor=health_monitor)

        # Hint for a provider that doesn't support the capability
        decision = router.select_provider(
            Capability.UI_BUILDER,
            hint="local_sandbox",
        )

        # Should fail because sandbox doesn't support UI_BUILDER
        # Then fall back to ui_templates
        assert decision.success is True
        assert decision.provider_id == "ui_templates"
        assert "local_sandbox:no_capability" in decision.alternatives_tried


# =============================================================================
# ProviderRouter Intent Tests
# =============================================================================


class TestProviderRouterIntent:
    """Test intent-based routing."""

    def test_route_safe_intent(self, health_monitor, mock_sandbox):
        """Routes safe intent successfully."""
        health_monitor.register(mock_sandbox)
        router = ProviderRouter(health_monitor=health_monitor)

        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/workspace",
            risk=RiskLevel.LOW,
            inputs={"command": "ls"},
        )

        decision = router.select_for_intent(intent)

        assert decision.success is True
        assert decision.provider_id == "local_sandbox"

    def test_route_with_policy_engine(self, health_monitor, mock_sandbox):
        """Policy engine is consulted for routing."""
        health_monitor.register(mock_sandbox)
        policy_engine = PolicyEngine()
        router = ProviderRouter(
            health_monitor=health_monitor,
            policy_engine=policy_engine,
        )

        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/workspace",
            risk=RiskLevel.LOW,
            inputs={"command": "git status"},
        )

        decision = router.select_for_intent(intent)

        assert decision.success is True
        assert decision.policy_decision is not None
        assert decision.policy_decision["allowed"] is True

    def test_route_blocked_by_policy(self, health_monitor, mock_sandbox):
        """Policy can block routing."""
        health_monitor.register(mock_sandbox)
        policy_engine = PolicyEngine()
        router = ProviderRouter(
            health_monitor=health_monitor,
            policy_engine=policy_engine,
        )

        intent = ToolIntent(
            capability=Capability.SHELL_EXEC,
            action="run",
            workspace="/workspace",
            risk=RiskLevel.HIGH,
            inputs={"command": "rm -rf /"},
        )

        decision = router.select_for_intent(intent)

        assert decision.success is False
        assert "policy" in decision.reason.lower()


# =============================================================================
# ProviderRouter Query Tests
# =============================================================================


class TestProviderRouterQueries:
    """Test router queries."""

    def test_get_provider_for_capability(self, health_monitor, mock_sandbox):
        """Get provider instance for capability."""
        health_monitor.register(mock_sandbox)
        router = ProviderRouter(health_monitor=health_monitor)

        provider = router.get_provider_for_capability(Capability.SHELL_EXEC)

        assert provider is mock_sandbox

    def test_list_providers_for_capability(
        self, health_monitor, mock_ui_templates, mock_antigravity
    ):
        """List providers for capability with states."""
        health_monitor.register(mock_ui_templates)
        health_monitor.register(mock_antigravity)
        health_monitor.probe_all(force=True)
        router = ProviderRouter(health_monitor=health_monitor)

        providers = router.list_providers_for_capability(
            Capability.UI_BUILDER,
            include_offline=True,
        )

        assert len(providers) == 2
        provider_ids = [p[0] for p in providers]
        assert "ui_templates" in provider_ids
        assert "ui_antigravity" in provider_ids

    def test_get_routing_summary(self, health_monitor, mock_sandbox):
        """Get routing summary."""
        health_monitor.register(mock_sandbox)
        health_monitor.probe("local_sandbox")
        router = ProviderRouter(health_monitor=health_monitor)

        summary = router.get_routing_summary()

        assert "capabilities" in summary
        assert "fallback" in summary
        assert summary["fallback"] == "local_sandbox"


# =============================================================================
# ProviderRouter Configuration Tests
# =============================================================================


class TestProviderRouterConfiguration:
    """Test router configuration updates."""

    def test_set_preferences(self, health_monitor):
        """Can update provider preferences."""
        router = ProviderRouter(health_monitor=health_monitor)

        router.set_preferences(
            Capability.SHELL_EXEC,
            ["custom_provider", "local_sandbox"],
        )

        assert router.config.provider_preferences["shell_exec"][0] == "custom_provider"

    def test_set_fallback(self, health_monitor):
        """Can update fallback provider."""
        router = ProviderRouter(health_monitor=health_monitor)

        router.set_fallback("custom_fallback")
        assert router.config.fallback_provider == "custom_fallback"

        router.set_fallback(None)
        assert router.config.fallback_provider is None


# =============================================================================
# RouteDecision Tests
# =============================================================================


class TestRouteDecision:
    """Test RouteDecision dataclass."""

    def test_to_dict(self):
        """RouteDecision serializes to dict."""
        decision = RouteDecision(
            provider_id="local_sandbox",
            capability=Capability.SHELL_EXEC,
            success=True,
            reason="Selected healthy provider",
            health_state=ProviderState.HEALTHY,
        )

        d = decision.to_dict()

        assert d["provider_id"] == "local_sandbox"
        assert d["capability"] == "shell_exec"
        assert d["success"] is True
        assert d["health_state"] == "healthy"

    def test_failed_decision(self):
        """Failed decision captures reason."""
        decision = RouteDecision(
            provider_id=None,
            capability=Capability.VISION_CONTROL,
            success=False,
            reason="No provider available",
            alternatives_tried=["vision_antigravity:offline"],
        )

        assert decision.success is False
        assert decision.provider_id is None
        assert len(decision.alternatives_tried) == 1


# =============================================================================
# Global Instance Tests
# =============================================================================


class TestGlobalInstances:
    """Test global singleton instances."""

    def test_get_health_monitor_singleton(self):
        """get_health_monitor returns same instance."""
        monitor1 = get_health_monitor()
        monitor2 = get_health_monitor()
        assert monitor1 is monitor2

    def test_get_router_singleton(self):
        """get_router returns same instance."""
        router1 = get_router()
        router2 = get_router()
        assert router1 is router2

    def test_reset_clears_instances(self):
        """Reset clears global instances."""
        monitor1 = get_health_monitor()
        reset_health_monitor()
        monitor2 = get_health_monitor()
        assert monitor1 is not monitor2


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Test thread safety of health monitor."""

    def test_concurrent_probes(self, health_config, mock_sandbox):
        """Concurrent probes don't cause issues."""
        monitor = HealthMonitor(health_config)
        monitor.register(mock_sandbox)

        results = []
        errors = []

        def probe_thread():
            try:
                health = monitor.probe("local_sandbox", force=True)
                results.append(health)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=probe_thread) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        for health in results:
            assert health.state == ProviderState.HEALTHY
