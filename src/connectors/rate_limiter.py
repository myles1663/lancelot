"""
Rate Limiter — Token bucket rate limiting for connectors.

Each connector gets its own rate limiter with configurable
max requests per minute and burst limit. Thread-safe.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict


class RateLimiter:
    """Token bucket rate limiter.

    Tokens refill at a constant rate (max_requests_per_minute / 60 per second).
    Burst limit caps the maximum number of tokens that can accumulate.
    """

    def __init__(self, max_requests_per_minute: int = 60, burst_limit: int = 10) -> None:
        self._tokens: float = float(burst_limit)
        self._max_tokens: float = float(burst_limit)
        self._refill_rate: float = max_requests_per_minute / 60.0
        self._last_refill: float = time.time()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._max_tokens,
            self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now

    def acquire(self) -> bool:
        """Try to consume one token. Returns True if successful."""
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def wait_and_acquire(self, timeout: float = 30.0) -> bool:
        """Block until a token is available or timeout expires.

        Returns True if a token was acquired, False on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.acquire():
                return True
            time.sleep(0.01)
        return False

    @property
    def available_tokens(self) -> float:
        """Current number of available tokens."""
        with self._lock:
            self._refill()
            return self._tokens

    @property
    def is_limited(self) -> bool:
        """True if no tokens are currently available."""
        with self._lock:
            self._refill()
            return self._tokens < 1.0


class RateLimiterRegistry:
    """Registry that manages per-connector rate limiters.

    Creates limiters on first access using per-connector config
    from connectors.yaml, falling back to default limits.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._default = config.get("default", {})
        self._per_connector = config.get("per_connector", {})
        self._limiters: Dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def get_limiter(self, connector_id: str) -> RateLimiter:
        """Get or create a rate limiter for a connector."""
        with self._lock:
            if connector_id not in self._limiters:
                # Use per-connector config if available, else default
                cfg = self._per_connector.get(connector_id, self._default)
                self._limiters[connector_id] = RateLimiter(
                    max_requests_per_minute=cfg.get(
                        "max_requests_per_minute",
                        self._default.get("max_requests_per_minute", 60),
                    ),
                    burst_limit=cfg.get(
                        "burst_limit",
                        self._default.get("burst_limit", 10),
                    ),
                )
            return self._limiters[connector_id]

    def check(self, connector_id: str) -> bool:
        """Try to acquire a token for a connector. Returns True if allowed."""
        return self.get_limiter(connector_id).acquire()

    def wait(self, connector_id: str, timeout: float = 30.0) -> bool:
        """Wait for a token to become available. Returns True if acquired."""
        return self.get_limiter(connector_id).wait_and_acquire(timeout)
