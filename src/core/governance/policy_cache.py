"""
Lancelot vNext4: Precomputed Policy Cache

Compiles allow/deny decisions at boot time for T0/T1 actions.
Provides O(1) lookup at runtime, with cache-miss fallback to
full PolicyEngine evaluation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .config import PolicyCacheConfig
from .models import RiskTier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedPolicyDecision:
    """A precomputed policy decision for a (capability, scope, pattern) tuple."""
    capability: str
    scope: str
    pattern: Optional[str]
    tier: RiskTier
    decision: str  # "allow" | "deny"
    reason: str
    soul_version: str
    compiled_at: str


@dataclass
class PolicyCacheStats:
    """Runtime statistics for the policy cache."""
    total_entries: int
    hits: int
    misses: int
    hit_rate: float
    soul_version: str
    compiled_at: str


class PolicyCache:
    """Precomputed policy decisions for T0 and T1 actions.

    Only caches decisions for actions with tier <= T1.
    T2 and T3 always go through full PolicyEngine evaluation.

    Cache invalidation and soul-version validation ensure safety.
    """

    def __init__(
        self,
        config: PolicyCacheConfig,
        risk_classifier=None,
        policy_engine=None,
        soul_version: str = "unknown",
    ):
        self._cache: dict[tuple[str, str, Optional[str]], CachedPolicyDecision] = {}
        self._config = config
        self._soul_version = soul_version
        self._compiled_at = datetime.now(timezone.utc).isoformat()
        self._hits = 0
        self._misses = 0

        if risk_classifier is not None:
            self._compile(risk_classifier, policy_engine)

    def _compile(self, risk_classifier, policy_engine=None) -> None:
        """Build the cache from known capabilities.

        For each known capability at workspace scope:
        - Classify the action
        - If tier <= T1, evaluate policy and cache the decision
        - Never cache T2 or T3 decisions
        """
        for capability in risk_classifier.known_capabilities:
            for scope in ["workspace"]:
                profile = risk_classifier.classify(capability, scope)
                if profile.tier <= RiskTier.T1_REVERSIBLE:
                    decision = "allow"  # Default for T0/T1 workspace actions
                    reason = f"Cached at boot: {profile.tier.name}"

                    if policy_engine is not None:
                        try:
                            snapshot = policy_engine.evaluate_path(capability)
                            decision = "allow" if snapshot.allowed else "deny"
                            reason = f"PolicyEngine: {snapshot.reasons}" if hasattr(snapshot, "reasons") else reason
                        except Exception:
                            decision = "allow"  # Safe default for low-risk

                    self._cache[(capability, scope, None)] = CachedPolicyDecision(
                        capability=capability,
                        scope=scope,
                        pattern=None,
                        tier=profile.tier,
                        decision=decision,
                        reason=reason,
                        soul_version=self._soul_version,
                        compiled_at=self._compiled_at,
                    )

        logger.info(
            "PolicyCache compiled: %d entries (soul_version=%s)",
            len(self._cache), self._soul_version,
        )

    def lookup(
        self,
        capability: str,
        scope: str = "workspace",
        pattern: Optional[str] = None,
    ) -> Optional[CachedPolicyDecision]:
        """O(1) lookup in the precomputed cache.

        Returns CachedPolicyDecision if found, None on cache miss.
        Validates soul_version if configured.
        """
        key = (capability, scope, pattern)
        decision = self._cache.get(key)

        if decision is None:
            self._misses += 1
            return None

        # Soul version validation
        if self._config.validate_soul_version:
            if decision.soul_version != self._soul_version:
                self._misses += 1
                return None

        self._hits += 1
        return decision

    def invalidate(self) -> None:
        """Clear all cached decisions. Called when Soul is amended."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        logger.info("PolicyCache invalidated")

    def recompile(
        self,
        risk_classifier,
        policy_engine=None,
        soul_version: str = "unknown",
    ) -> None:
        """Invalidate and rebuild the cache with a new Soul version."""
        self.invalidate()
        self._soul_version = soul_version
        self._compiled_at = datetime.now(timezone.utc).isoformat()
        self._compile(risk_classifier, policy_engine)

    @property
    def stats(self) -> PolicyCacheStats:
        """Current cache statistics."""
        total = self._hits + self._misses
        return PolicyCacheStats(
            total_entries=len(self._cache),
            hits=self._hits,
            misses=self._misses,
            hit_rate=self._hits / total if total > 0 else 0.0,
            soul_version=self._soul_version,
            compiled_at=self._compiled_at,
        )
