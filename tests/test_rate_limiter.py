"""
Tests for Prompt 30: RateLimiter.

Token bucket rate limiter with per-connector registry.
"""

import threading
import time

import pytest

from src.connectors.rate_limiter import RateLimiter, RateLimiterRegistry


# ── RateLimiter ───────────────────────────────────────────────────

class TestRateLimiter:
    def test_initializes_with_correct_tokens(self):
        rl = RateLimiter(max_requests_per_minute=60, burst_limit=5)
        assert rl.available_tokens == pytest.approx(5.0, abs=0.1)

    def test_acquire_succeeds_when_tokens_available(self):
        rl = RateLimiter(burst_limit=5)
        assert rl.acquire() is True

    def test_acquire_fails_when_exhausted(self):
        rl = RateLimiter(max_requests_per_minute=60, burst_limit=3)
        # Drain all tokens
        assert rl.acquire() is True
        assert rl.acquire() is True
        assert rl.acquire() is True
        # Now exhausted
        assert rl.acquire() is False

    def test_tokens_refill_over_time(self):
        rl = RateLimiter(max_requests_per_minute=600, burst_limit=2)
        # Drain tokens
        rl.acquire()
        rl.acquire()
        assert rl.acquire() is False
        # Wait for refill (600/min = 10/sec, so 0.15s ≈ 1.5 tokens)
        time.sleep(0.15)
        assert rl.acquire() is True

    def test_burst_limit_caps_max_tokens(self):
        rl = RateLimiter(max_requests_per_minute=6000, burst_limit=3)
        # Even after generous refill time, tokens capped at burst_limit
        time.sleep(0.1)
        assert rl.available_tokens <= 3.0

    def test_thread_safety(self):
        rl = RateLimiter(max_requests_per_minute=60, burst_limit=5)
        results = []
        barrier = threading.Barrier(10)

        def worker():
            barrier.wait()
            results.append(rl.acquire())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At most burst_limit (5) should succeed
        assert sum(results) <= 5

    def test_wait_and_acquire_succeeds(self):
        rl = RateLimiter(max_requests_per_minute=600, burst_limit=1)
        rl.acquire()  # drain
        # 600/min = 10/sec, so within 0.5s we should get a token
        assert rl.wait_and_acquire(timeout=0.5) is True

    def test_wait_and_acquire_timeout(self):
        rl = RateLimiter(max_requests_per_minute=1, burst_limit=1)
        rl.acquire()  # drain
        # 1/min = very slow refill, timeout quickly
        assert rl.wait_and_acquire(timeout=0.05) is False

    def test_is_limited_true(self):
        rl = RateLimiter(max_requests_per_minute=60, burst_limit=1)
        rl.acquire()
        assert rl.is_limited is True

    def test_is_limited_false(self):
        rl = RateLimiter(max_requests_per_minute=60, burst_limit=5)
        assert rl.is_limited is False


# ── RateLimiterRegistry ───────────────────────────────────────────

class TestRateLimiterRegistry:
    def test_creates_limiter_per_connector(self):
        config = {
            "default": {"max_requests_per_minute": 60, "burst_limit": 10},
            "per_connector": {},
        }
        reg = RateLimiterRegistry(config)
        l1 = reg.get_limiter("slack")
        l2 = reg.get_limiter("email")
        assert l1 is not l2
        # Same connector returns same limiter
        assert reg.get_limiter("slack") is l1

    def test_uses_per_connector_config(self):
        config = {
            "default": {"max_requests_per_minute": 60, "burst_limit": 10},
            "per_connector": {
                "slack": {"max_requests_per_minute": 50, "burst_limit": 5},
            },
        }
        reg = RateLimiterRegistry(config)
        limiter = reg.get_limiter("slack")
        # Burst limit should be 5, not default 10
        assert limiter.available_tokens == pytest.approx(5.0, abs=0.1)

    def test_falls_back_to_default(self):
        config = {
            "default": {"max_requests_per_minute": 60, "burst_limit": 8},
            "per_connector": {},
        }
        reg = RateLimiterRegistry(config)
        limiter = reg.get_limiter("unknown_connector")
        assert limiter.available_tokens == pytest.approx(8.0, abs=0.1)

    def test_check_acquires(self):
        config = {
            "default": {"max_requests_per_minute": 60, "burst_limit": 2},
        }
        reg = RateLimiterRegistry(config)
        assert reg.check("slack") is True
        assert reg.check("slack") is True
        assert reg.check("slack") is False

    def test_wait_acquires(self):
        config = {
            "default": {"max_requests_per_minute": 600, "burst_limit": 1},
        }
        reg = RateLimiterRegistry(config)
        reg.check("slack")  # drain
        assert reg.wait("slack", timeout=0.5) is True
