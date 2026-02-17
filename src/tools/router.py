"""
Tool Fabric Router — Provider Routing and Failover
===================================================

This module provides intelligent provider selection for Tool Fabric:
- Capability-based routing
- Priority-based provider selection
- Health-aware failover
- Policy integration
- Route decision logging

Router selects providers based on:
- Requested capability
- Provider health status
- Configured priorities
- Task risk level
- Feature flags
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.tools.contracts import (
    Capability,
    RiskLevel,
    ProviderHealth,
    ProviderState,
    ToolIntent,
    BaseProvider,
)
from src.tools.health import HealthMonitor, get_health_monitor
from src.tools.policies import PolicyEngine, PolicyDecision
from src.core.feature_flags import FEATURE_TOOLS_HOST_EXECUTION

logger = logging.getLogger(__name__)


# =============================================================================
# Router Configuration
# =============================================================================


def _default_provider_preferences() -> Dict[str, List[str]]:
    """Build provider preferences based on feature flags."""
    # When host execution is enabled, prefer it over sandbox for core capabilities
    if FEATURE_TOOLS_HOST_EXECUTION:
        return {
            "shell_exec": ["host_execution", "local_sandbox"],
            "repo_ops": ["host_execution", "local_sandbox"],
            "file_ops": ["host_execution", "local_sandbox"],
            "web_ops": ["local_sandbox"],
            "ui_builder": ["ui_templates", "ui_antigravity"],
            "deploy_ops": ["host_execution", "local_sandbox"],
            "vision_control": ["vision_antigravity"],
        }
    return {
        "shell_exec": ["local_sandbox"],
        "repo_ops": ["local_sandbox"],
        "file_ops": ["local_sandbox"],
        "web_ops": ["local_sandbox"],
        "ui_builder": ["ui_templates", "ui_antigravity"],
        "deploy_ops": ["local_sandbox"],
        "vision_control": ["vision_antigravity"],
    }


@dataclass
class RouterConfig:
    """Configuration for the provider router."""

    # Default provider preferences per capability
    # Order matters: first healthy provider is selected
    provider_preferences: Dict[str, List[str]] = field(
        default_factory=_default_provider_preferences
    )

    # Fallback provider (used if all preferred providers offline)
    fallback_provider: Optional[str] = "local_sandbox"

    # Whether to require healthy provider (vs degraded)
    require_healthy: bool = False

    # Whether to fail fast if no provider available
    fail_fast: bool = True

    # Whether to probe health before selection
    probe_before_select: bool = True


# =============================================================================
# Route Decision
# =============================================================================


@dataclass
class RouteDecision:
    """Result of a routing decision."""

    provider_id: Optional[str]
    capability: Capability
    success: bool
    reason: str
    alternatives_tried: List[str] = field(default_factory=list)
    health_state: Optional[ProviderState] = None
    policy_decision: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "provider_id": self.provider_id,
            "capability": self.capability.value,
            "success": self.success,
            "reason": self.reason,
            "alternatives_tried": self.alternatives_tried,
            "health_state": self.health_state.value if self.health_state else None,
            "policy_decision": self.policy_decision,
            "timestamp": self.timestamp,
        }


# =============================================================================
# Provider Router
# =============================================================================


class ProviderRouter:
    """
    Route tool requests to appropriate providers.

    Responsibilities:
    - Select provider based on capability and health
    - Apply priority-based selection
    - Handle failover when providers unavailable
    - Integrate with policy engine
    """

    def __init__(
        self,
        config: Optional[RouterConfig] = None,
        health_monitor: Optional[HealthMonitor] = None,
        policy_engine: Optional[PolicyEngine] = None,
    ):
        """
        Initialize the router.

        Args:
            config: Optional RouterConfig (uses defaults if not provided)
            health_monitor: Optional HealthMonitor (uses global if not provided)
            policy_engine: Optional PolicyEngine
        """
        self.config = config or RouterConfig()
        self._health_monitor = health_monitor
        self._policy_engine = policy_engine

    @property
    def health_monitor(self) -> HealthMonitor:
        """Get health monitor (lazy initialization)."""
        if self._health_monitor is None:
            self._health_monitor = get_health_monitor()
        return self._health_monitor

    @property
    def policy_engine(self) -> Optional[PolicyEngine]:
        """Get policy engine."""
        return self._policy_engine

    # =========================================================================
    # Provider Selection
    # =========================================================================

    def select_provider(
        self,
        capability: Capability,
        risk_level: RiskLevel = RiskLevel.LOW,
        hint: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> RouteDecision:
        """
        Select a provider for a capability.

        Args:
            capability: Required capability
            risk_level: Risk level of the operation
            hint: Optional preferred provider hint
            workspace: Optional workspace for policy evaluation

        Returns:
            RouteDecision with selected provider
        """
        alternatives_tried = []

        # Get provider preferences for this capability
        cap_key = capability.value
        preferences = self.config.provider_preferences.get(cap_key, [])

        # If hint provided, try it first
        if hint and hint not in preferences:
            preferences = [hint] + preferences

        # Try providers in order
        for provider_id in preferences:
            # Check if provider is registered
            provider = self.health_monitor.get_provider(provider_id)
            if not provider:
                alternatives_tried.append(f"{provider_id}:not_registered")
                continue

            # Check if provider supports capability
            if not provider.supports(capability):
                alternatives_tried.append(f"{provider_id}:no_capability")
                continue

            # Probe health if configured
            if self.config.probe_before_select:
                health = self.health_monitor.probe(provider_id)
            else:
                health = self.health_monitor.get_health(provider_id)
                if health is None:
                    health = self.health_monitor.probe(provider_id)

            # Check health state
            if health.state == ProviderState.OFFLINE:
                alternatives_tried.append(f"{provider_id}:offline")
                continue

            if self.config.require_healthy and health.state != ProviderState.HEALTHY:
                alternatives_tried.append(f"{provider_id}:degraded")
                continue

            # Provider is acceptable
            logger.debug(
                "Selected provider %s for %s (health: %s)",
                provider_id, capability.value, health.state.value,
            )

            return RouteDecision(
                provider_id=provider_id,
                capability=capability,
                success=True,
                reason=f"Selected healthy provider",
                alternatives_tried=alternatives_tried,
                health_state=health.state,
            )

        # Try fallback provider
        if self.config.fallback_provider:
            fallback_id = self.config.fallback_provider
            provider = self.health_monitor.get_provider(fallback_id)

            if provider and provider.supports(capability):
                health = self.health_monitor.probe(fallback_id)
                if health.state != ProviderState.OFFLINE:
                    logger.warning(
                        "Using fallback provider %s for %s",
                        fallback_id, capability.value,
                    )
                    return RouteDecision(
                        provider_id=fallback_id,
                        capability=capability,
                        success=True,
                        reason="Using fallback provider",
                        alternatives_tried=alternatives_tried,
                        health_state=health.state,
                    )
                alternatives_tried.append(f"{fallback_id}:offline")

        # No provider available
        logger.error(
            "No provider available for %s. Tried: %s",
            capability.value, alternatives_tried,
        )

        return RouteDecision(
            provider_id=None,
            capability=capability,
            success=False,
            reason=f"No provider available for {capability.value}",
            alternatives_tried=alternatives_tried,
        )

    def select_for_intent(self, intent: ToolIntent) -> RouteDecision:
        """
        Select a provider for a tool intent.

        Args:
            intent: ToolIntent to route

        Returns:
            RouteDecision with policy integration
        """
        # Get policy decision if policy engine available
        policy_decision = None
        if self._policy_engine:
            decision = self._policy_engine.evaluate_intent(intent, intent.workspace)
            policy_decision = decision.to_dict()

            if not decision.allowed:
                return RouteDecision(
                    provider_id=None,
                    capability=intent.capability,
                    success=False,
                    reason=f"Blocked by policy: {'; '.join(decision.reasons)}",
                    policy_decision=policy_decision,
                )

        # Select provider
        route = self.select_provider(
            capability=intent.capability,
            risk_level=intent.risk,
            hint=intent.provider_hint,
            workspace=intent.workspace,
        )

        route.policy_decision = policy_decision
        return route

    # =========================================================================
    # Route Queries
    # =========================================================================

    def get_provider_for_capability(
        self,
        capability: Capability,
    ) -> Optional[BaseProvider]:
        """
        Get the best provider for a capability.

        Args:
            capability: Required capability

        Returns:
            Provider instance or None
        """
        decision = self.select_provider(capability)
        if decision.success and decision.provider_id:
            return self.health_monitor.get_provider(decision.provider_id)
        return None

    def list_providers_for_capability(
        self,
        capability: Capability,
        include_offline: bool = False,
    ) -> List[Tuple[str, ProviderState]]:
        """
        List all providers for a capability with health states.

        Args:
            capability: Capability to check
            include_offline: Whether to include offline providers

        Returns:
            List of (provider_id, state) tuples
        """
        result = []
        cap_key = capability.value
        preferences = self.config.provider_preferences.get(cap_key, [])

        for provider_id in preferences:
            provider = self.health_monitor.get_provider(provider_id)
            if not provider:
                continue

            if not provider.supports(capability):
                continue

            health = self.health_monitor.get_health(provider_id)
            state = health.state if health else ProviderState.OFFLINE

            if include_offline or state != ProviderState.OFFLINE:
                result.append((provider_id, state))

        return result

    def get_routing_summary(self) -> Dict[str, Any]:
        """
        Get a summary of routing configuration.

        Returns:
            Summary dict
        """
        summary = {
            "capabilities": {},
            "fallback": self.config.fallback_provider,
            "require_healthy": self.config.require_healthy,
        }

        for cap_key, providers in self.config.provider_preferences.items():
            cap_providers = []
            for pid in providers:
                health = self.health_monitor.get_health(pid)
                state = health.state.value if health else "unknown"
                cap_providers.append({"id": pid, "state": state})
            summary["capabilities"][cap_key] = cap_providers

        return summary

    # =========================================================================
    # Configuration Updates
    # =========================================================================

    def set_preferences(
        self,
        capability: Capability,
        providers: List[str],
    ) -> None:
        """
        Update provider preferences for a capability.

        Args:
            capability: Capability to configure
            providers: Ordered list of provider IDs
        """
        self.config.provider_preferences[capability.value] = providers
        logger.info(
            "Updated preferences for %s: %s",
            capability.value, providers,
        )

    def set_fallback(self, provider_id: Optional[str]) -> None:
        """
        Set the fallback provider.

        Args:
            provider_id: Provider ID or None to disable fallback
        """
        self.config.fallback_provider = provider_id
        logger.info("Set fallback provider: %s", provider_id)


# =============================================================================
# Global Router
# =============================================================================


_router: Optional[ProviderRouter] = None
_router_lock = __import__("threading").Lock()


def get_router(
    config: Optional[RouterConfig] = None,
    health_monitor: Optional[HealthMonitor] = None,
    policy_engine: Optional[PolicyEngine] = None,
) -> ProviderRouter:
    """
    Get the global router instance.

    Args:
        config: Optional config (only used on first call)
        health_monitor: Optional health monitor
        policy_engine: Optional policy engine

    Returns:
        Global ProviderRouter instance
    """
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                _router = ProviderRouter(config, health_monitor, policy_engine)
    return _router


def reset_router() -> None:
    """Reset the global router (for testing)."""
    global _router
    with _router_lock:
        _router = None
