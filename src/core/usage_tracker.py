"""
UsageTracker — per-lane usage and cost telemetry (Prompt 17).

Single-owner module that tracks API usage per lane, estimates costs,
and calculates local-utility savings.  Data is derived from
RouterDecision records produced by the ModelRouter.

Cost estimates use approximate $/1K-token rates.  Real billing
depends on the provider's actual pricing, but these figures are
close enough for the War Room cost panel.

Public API:
    UsageTracker(model_router)
    tracker.record(decision)        — record a single routing decision
    tracker.summary()               → dict   (full usage summary)
    tracker.lane_breakdown()        → dict   (per-lane counts & cost)
    tracker.estimated_savings()     → dict   (local-vs-flagship delta)
    tracker.reset()                 — clear all counters
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Approximate cost per 1K tokens (output) by model family.
# Input tokens are typically cheaper; we use a blended estimate.
# ---------------------------------------------------------------------------

_COST_PER_1K: dict[str, float] = {
    # Gemini
    "gemini-2.0-flash": 0.0004,
    "gemini-2.0-pro": 0.007,
    # OpenAI
    "gpt-4o-mini": 0.0006,
    "gpt-4o": 0.01,
    # Anthropic
    "claude-3-5-haiku-latest": 0.001,
    "claude-sonnet-4-20250514": 0.015,
    # Local (free)
    "local-llm": 0.0,
}

# Average tokens per request by lane (rough heuristic).
_AVG_TOKENS: dict[str, int] = {
    "local_redaction": 80,
    "local_utility": 120,
    "flagship_fast": 500,
    "flagship_deep": 1500,
}

# What a local task *would* cost if sent to the cheapest flagship.
_FLAGSHIP_FLOOR_COST_PER_1K = 0.0004


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LaneUsage:
    """Accumulated usage for a single lane."""
    requests: int = 0
    successes: int = 0
    failures: int = 0
    total_tokens_est: int = 0
    total_cost_est: float = 0.0
    total_elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "successes": self.successes,
            "failures": self.failures,
            "total_tokens_est": self.total_tokens_est,
            "total_cost_est": round(self.total_cost_est, 6),
            "total_elapsed_ms": round(self.total_elapsed_ms, 2),
            "avg_elapsed_ms": round(
                self.total_elapsed_ms / self.requests, 2
            ) if self.requests else 0.0,
        }


# ---------------------------------------------------------------------------
# UsageTracker
# ---------------------------------------------------------------------------

class UsageTracker:
    """Tracks per-lane usage, estimates costs, calculates savings."""

    def __init__(self) -> None:
        self._lanes: dict[str, LaneUsage] = defaultdict(LaneUsage)
        self._started_at: str = datetime.now(timezone.utc).isoformat()
        self._total_requests: int = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, decision) -> None:
        """Record a RouterDecision into the usage ledger.

        Args:
            decision: A RouterDecision (or any object with lane, model,
                      success, elapsed_ms attributes).
        """
        lane = getattr(decision, "lane", "unknown")
        model = getattr(decision, "model", "unknown")
        success = getattr(decision, "success", False)
        elapsed_ms = getattr(decision, "elapsed_ms", 0.0)

        usage = self._lanes[lane]
        usage.requests += 1
        if success:
            usage.successes += 1
        else:
            usage.failures += 1
        usage.total_elapsed_ms += elapsed_ms

        # Estimate tokens and cost
        est_tokens = _AVG_TOKENS.get(lane, 200)
        usage.total_tokens_est += est_tokens

        cost_rate = _COST_PER_1K.get(model, 0.001)
        est_cost = (est_tokens / 1000) * cost_rate
        usage.total_cost_est += est_cost

        self._total_requests += 1

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def lane_breakdown(self) -> dict[str, dict]:
        """Per-lane usage breakdown."""
        return {lane: usage.to_dict() for lane, usage in self._lanes.items()}

    def estimated_savings(self) -> dict:
        """Calculate how much was saved by routing to local models.

        Compares the actual cost of local lanes ($0) against what it
        would have cost to send those same requests to the cheapest
        flagship lane.
        """
        local_requests = 0
        local_tokens = 0
        for lane_name in ("local_redaction", "local_utility"):
            usage = self._lanes.get(lane_name)
            if usage:
                local_requests += usage.requests
                local_tokens += usage.total_tokens_est

        # What those local tokens would have cost on flagship
        hypothetical_cost = (local_tokens / 1000) * _FLAGSHIP_FLOOR_COST_PER_1K

        flagship_cost = 0.0
        for lane_name, usage in self._lanes.items():
            if lane_name not in ("local_redaction", "local_utility"):
                flagship_cost += usage.total_cost_est

        return {
            "local_requests": local_requests,
            "local_tokens_est": local_tokens,
            "hypothetical_flagship_cost": round(hypothetical_cost, 6),
            "actual_flagship_cost": round(flagship_cost, 6),
            "estimated_savings": round(hypothetical_cost, 6),
            "savings_description": (
                f"{local_requests} requests handled locally at $0, "
                f"saving ~${hypothetical_cost:.4f} vs cheapest flagship"
            ),
        }

    def summary(self) -> dict:
        """Full usage summary for the War Room cost panel."""
        total_tokens = sum(u.total_tokens_est for u in self._lanes.values())
        total_cost = sum(u.total_cost_est for u in self._lanes.values())
        total_ms = sum(u.total_elapsed_ms for u in self._lanes.values())
        successes = sum(u.successes for u in self._lanes.values())

        return {
            "period_start": self._started_at,
            "total_requests": self._total_requests,
            "total_tokens_est": total_tokens,
            "total_cost_est": round(total_cost, 6),
            "success_rate": round(
                successes / self._total_requests, 4
            ) if self._total_requests else 0.0,
            "avg_elapsed_ms": round(
                total_ms / self._total_requests, 2
            ) if self._total_requests else 0.0,
            "by_lane": self.lane_breakdown(),
            "savings": self.estimated_savings(),
        }

    def reset(self) -> None:
        """Clear all counters and start a new tracking period."""
        self._lanes.clear()
        self._total_requests = 0
        self._started_at = datetime.now(timezone.utc).isoformat()
        logger.info("UsageTracker reset")
