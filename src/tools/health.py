"""
Tool Fabric Health — Provider Discovery and Health Probes
==========================================================

This module provides health monitoring for Tool Fabric providers:
- Provider discovery and registration
- Health probe execution
- State tracking (HEALTHY/DEGRADED/OFFLINE)
- Cached health status with TTL

Health probes run:
- On startup (discovery sweep)
- On-demand (manual probe)
- Before provider selection (if stale)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.tools.contracts import (
    Capability,
    ProviderHealth,
    ProviderState,
    BaseProvider,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Health Configuration
# =============================================================================


@dataclass
class HealthConfig:
    """Configuration for health monitoring."""

    # Probe settings
    probe_timeout_s: int = 10
    cache_ttl_s: int = 60  # How long health status is considered fresh

    # Startup settings
    run_sweep_on_startup: bool = True
    parallel_probes: bool = True

    # Retry settings
    retry_offline_after_s: int = 300  # Retry offline providers after 5 min
    max_consecutive_failures: int = 3


# =============================================================================
# Health Monitor
# =============================================================================


class HealthMonitor:
    """
    Monitor and track health of Tool Fabric providers.

    Responsibilities:
    - Register providers
    - Execute health probes
    - Cache and track health status
    - Provide health queries
    """

    def __init__(self, config: Optional[HealthConfig] = None):
        """
        Initialize the health monitor.

        Args:
            config: Optional HealthConfig (uses defaults if not provided)
        """
        self.config = config or HealthConfig()
        self._providers: Dict[str, BaseProvider] = {}
        self._health_cache: Dict[str, ProviderHealth] = {}
        self._last_check: Dict[str, float] = {}
        self._failure_counts: Dict[str, int] = {}
        self._lock = threading.RLock()

    # =========================================================================
    # Provider Registration
    # =========================================================================

    def register(self, provider: BaseProvider) -> None:
        """
        Register a provider for health monitoring.

        Args:
            provider: Provider instance to register
        """
        with self._lock:
            provider_id = provider.provider_id
            self._providers[provider_id] = provider
            self._failure_counts[provider_id] = 0
            logger.info("Registered provider: %s", provider_id)

    def unregister(self, provider_id: str) -> None:
        """
        Unregister a provider.

        Args:
            provider_id: Provider ID to unregister
        """
        with self._lock:
            self._providers.pop(provider_id, None)
            self._health_cache.pop(provider_id, None)
            self._last_check.pop(provider_id, None)
            self._failure_counts.pop(provider_id, None)
            logger.info("Unregistered provider: %s", provider_id)

    def get_provider(self, provider_id: str) -> Optional[BaseProvider]:
        """
        Get a registered provider by ID.

        Args:
            provider_id: Provider ID

        Returns:
            Provider instance or None
        """
        with self._lock:
            return self._providers.get(provider_id)

    def list_providers(self) -> List[str]:
        """List all registered provider IDs."""
        with self._lock:
            return list(self._providers.keys())

    # =========================================================================
    # Health Probes
    # =========================================================================

    def probe(self, provider_id: str, force: bool = False) -> ProviderHealth:
        """
        Probe a specific provider's health.

        Args:
            provider_id: Provider to probe
            force: If True, ignore cache and probe now

        Returns:
            ProviderHealth for the provider
        """
        with self._lock:
            # Check cache
            if not force and self._is_cache_valid(provider_id):
                return self._health_cache[provider_id]

            # Get provider
            provider = self._providers.get(provider_id)
            if not provider:
                return ProviderHealth(
                    provider_id=provider_id,
                    state=ProviderState.OFFLINE,
                    error_message="Provider not registered",
                )

            # Check if we should skip offline provider
            if not force and self._should_skip_offline(provider_id):
                return self._health_cache.get(provider_id, ProviderHealth(
                    provider_id=provider_id,
                    state=ProviderState.OFFLINE,
                    error_message="Provider offline, waiting for retry interval",
                ))

        # Execute probe (outside lock)
        try:
            health = provider.health_check()
            self._update_health(provider_id, health)
            return health
        except Exception as e:
            logger.warning("Health probe failed for %s: %s", provider_id, e)
            health = ProviderHealth(
                provider_id=provider_id,
                state=ProviderState.OFFLINE,
                error_message=str(e)[:200],
            )
            self._update_health(provider_id, health)
            return health

    def probe_all(self, force: bool = False) -> Dict[str, ProviderHealth]:
        """
        Probe all registered providers.

        Args:
            force: If True, ignore cache for all providers

        Returns:
            Dict mapping provider_id to ProviderHealth
        """
        results = {}
        provider_ids = self.list_providers()

        if self.config.parallel_probes:
            # Parallel probing (for faster startup)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(self.probe, pid, force): pid
                    for pid in provider_ids
                }
                for future in concurrent.futures.as_completed(futures):
                    pid = futures[future]
                    try:
                        results[pid] = future.result()
                    except Exception as e:
                        results[pid] = ProviderHealth(
                            provider_id=pid,
                            state=ProviderState.OFFLINE,
                            error_message=str(e)[:200],
                        )
        else:
            # Sequential probing
            for pid in provider_ids:
                results[pid] = self.probe(pid, force)

        return results

    def sweep(self) -> Dict[str, ProviderHealth]:
        """
        Run a full health sweep (all providers, force refresh).

        Returns:
            Dict mapping provider_id to ProviderHealth
        """
        logger.info("Running health sweep for %d providers", len(self._providers))
        return self.probe_all(force=True)

    # =========================================================================
    # Health Queries
    # =========================================================================

    def get_health(self, provider_id: str) -> Optional[ProviderHealth]:
        """
        Get cached health for a provider.

        Args:
            provider_id: Provider ID

        Returns:
            Cached ProviderHealth or None
        """
        with self._lock:
            return self._health_cache.get(provider_id)

    def get_all_health(self) -> Dict[str, ProviderHealth]:
        """Get cached health for all providers."""
        with self._lock:
            return dict(self._health_cache)

    def is_healthy(self, provider_id: str) -> bool:
        """
        Check if a provider is healthy.

        Args:
            provider_id: Provider ID

        Returns:
            True if provider is HEALTHY
        """
        health = self.get_health(provider_id)
        return health is not None and health.state == ProviderState.HEALTHY

    def is_available(self, provider_id: str) -> bool:
        """
        Check if a provider is available (HEALTHY or DEGRADED).

        Args:
            provider_id: Provider ID

        Returns:
            True if provider is available
        """
        health = self.get_health(provider_id)
        return health is not None and health.state != ProviderState.OFFLINE

    def get_available_providers(self) -> List[str]:
        """Get list of available provider IDs."""
        with self._lock:
            return [
                pid for pid, health in self._health_cache.items()
                if health.state != ProviderState.OFFLINE
            ]

    def get_healthy_providers(self) -> List[str]:
        """Get list of healthy provider IDs."""
        with self._lock:
            return [
                pid for pid, health in self._health_cache.items()
                if health.state == ProviderState.HEALTHY
            ]

    def get_providers_for_capability(self, capability: Capability) -> List[str]:
        """
        Get providers that support a capability.

        Args:
            capability: Capability to check

        Returns:
            List of provider IDs that support the capability
        """
        with self._lock:
            result = []
            for pid, provider in self._providers.items():
                if provider.supports(capability):
                    result.append(pid)
            return result

    def get_healthy_providers_for_capability(
        self,
        capability: Capability,
    ) -> List[str]:
        """
        Get healthy providers that support a capability.

        Args:
            capability: Capability to check

        Returns:
            List of healthy provider IDs that support the capability
        """
        providers = self.get_providers_for_capability(capability)
        return [pid for pid in providers if self.is_healthy(pid)]

    # =========================================================================
    # Summary
    # =========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of provider health.

        Returns:
            Summary dict with counts and states
        """
        with self._lock:
            states = {"healthy": 0, "degraded": 0, "offline": 0}
            for health in self._health_cache.values():
                if health.state == ProviderState.HEALTHY:
                    states["healthy"] += 1
                elif health.state == ProviderState.DEGRADED:
                    states["degraded"] += 1
                else:
                    states["offline"] += 1

            return {
                "total_providers": len(self._providers),
                "registered": list(self._providers.keys()),
                "states": states,
                "last_sweep": max(self._last_check.values()) if self._last_check else None,
            }

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _is_cache_valid(self, provider_id: str) -> bool:
        """Check if cached health is still valid."""
        last = self._last_check.get(provider_id, 0)
        age = time.time() - last
        return age < self.config.cache_ttl_s

    def _should_skip_offline(self, provider_id: str) -> bool:
        """Check if we should skip probing an offline provider."""
        health = self._health_cache.get(provider_id)
        if not health or health.state != ProviderState.OFFLINE:
            return False

        # Check retry interval
        last = self._last_check.get(provider_id, 0)
        age = time.time() - last
        return age < self.config.retry_offline_after_s

    def _update_health(self, provider_id: str, health: ProviderHealth) -> None:
        """Update health cache and failure counts."""
        with self._lock:
            self._health_cache[provider_id] = health
            self._last_check[provider_id] = time.time()

            # Update failure count
            if health.state == ProviderState.OFFLINE:
                self._failure_counts[provider_id] = self._failure_counts.get(provider_id, 0) + 1
            else:
                self._failure_counts[provider_id] = 0


# =============================================================================
# Global Health Monitor
# =============================================================================


_health_monitor: Optional[HealthMonitor] = None
_monitor_lock = threading.Lock()


def get_health_monitor(config: Optional[HealthConfig] = None) -> HealthMonitor:
    """
    Get the global health monitor instance.

    Args:
        config: Optional config (only used on first call)

    Returns:
        Global HealthMonitor instance
    """
    global _health_monitor
    if _health_monitor is None:
        with _monitor_lock:
            if _health_monitor is None:
                _health_monitor = HealthMonitor(config)
    return _health_monitor


def reset_health_monitor() -> None:
    """Reset the global health monitor (for testing)."""
    global _health_monitor
    with _monitor_lock:
        _health_monitor = None
