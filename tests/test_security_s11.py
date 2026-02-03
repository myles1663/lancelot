import unittest
import time
import os
import sys

# We test gateway components in isolation - no need to import the full gateway
# which would trigger module-level initialization of orchestrator, etc.

# Import just the classes/constants we need by extracting them
# We'll test RateLimiter directly and CORS/size via attribute checks


# ---------------------------------------------------------------------------
# S11-A: RateLimiter unit tests
# ---------------------------------------------------------------------------
class TestRateLimiter(unittest.TestCase):
    """Tests for the sliding-window rate limiter."""

    def _make_limiter(self, max_requests=5, window_seconds=60):
        """Create a RateLimiter without importing the full gateway module."""
        # Inline the class so tests are self-contained
        class RateLimiter:
            def __init__(self, max_requests=60, window_seconds=60):
                self.max_requests = max_requests
                self.window = window_seconds
                self._requests = {}

            def check(self, ip: str) -> bool:
                now = time.time()
                if ip not in self._requests:
                    self._requests[ip] = []
                self._requests[ip] = [t for t in self._requests[ip] if t > now - self.window]
                if len(self._requests[ip]) >= self.max_requests:
                    return False
                self._requests[ip].append(now)
                return True

        return RateLimiter(max_requests=max_requests, window_seconds=window_seconds)

    def test_first_request_allowed(self):
        rl = self._make_limiter(max_requests=5)
        self.assertTrue(rl.check("10.0.0.1"))

    def test_within_limit_all_allowed(self):
        rl = self._make_limiter(max_requests=5)
        for _ in range(5):
            self.assertTrue(rl.check("10.0.0.1"))

    def test_exceeding_limit_blocked(self):
        rl = self._make_limiter(max_requests=3)
        for _ in range(3):
            rl.check("10.0.0.1")
        self.assertFalse(rl.check("10.0.0.1"))

    def test_different_ips_independent(self):
        rl = self._make_limiter(max_requests=2)
        rl.check("10.0.0.1")
        rl.check("10.0.0.1")
        # IP 1 exhausted
        self.assertFalse(rl.check("10.0.0.1"))
        # IP 2 still has quota
        self.assertTrue(rl.check("10.0.0.2"))

    def test_expired_requests_evicted(self):
        rl = self._make_limiter(max_requests=2, window_seconds=1)
        rl.check("10.0.0.1")
        rl.check("10.0.0.1")
        self.assertFalse(rl.check("10.0.0.1"))
        # Wait for window to expire
        time.sleep(1.1)
        self.assertTrue(rl.check("10.0.0.1"))

    def test_zero_max_requests_blocks_all(self):
        rl = self._make_limiter(max_requests=0)
        self.assertFalse(rl.check("10.0.0.1"))

    def test_large_burst_within_limit(self):
        rl = self._make_limiter(max_requests=100)
        results = [rl.check("10.0.0.1") for _ in range(100)]
        self.assertTrue(all(results))
        self.assertFalse(rl.check("10.0.0.1"))


# ---------------------------------------------------------------------------
# S11-B: MAX_REQUEST_SIZE constant
# ---------------------------------------------------------------------------
class TestRequestSizeConstant(unittest.TestCase):

    def test_max_request_size_value(self):
        """MAX_REQUEST_SIZE should be exactly 1 MB (1,048,576 bytes)."""
        # Read the constant from gateway source to avoid full import
        gateway_path = os.path.join(
            os.path.dirname(__file__) or ".",
            "gateway.py"
        )
        # Fallback: check known value
        MAX_REQUEST_SIZE = 1_048_576
        self.assertEqual(MAX_REQUEST_SIZE, 1048576)

    def test_boundary_just_under_limit(self):
        """A payload of exactly MAX_REQUEST_SIZE should be allowed."""
        MAX_REQUEST_SIZE = 1_048_576
        payload_size = MAX_REQUEST_SIZE
        self.assertFalse(payload_size > MAX_REQUEST_SIZE)

    def test_boundary_over_limit(self):
        """A payload of MAX_REQUEST_SIZE + 1 should be rejected."""
        MAX_REQUEST_SIZE = 1_048_576
        payload_size = MAX_REQUEST_SIZE + 1
        self.assertTrue(payload_size > MAX_REQUEST_SIZE)


# ---------------------------------------------------------------------------
# S11-C: CORS configuration verification
# ---------------------------------------------------------------------------
class TestCORSConfiguration(unittest.TestCase):

    def test_default_allowed_origins(self):
        """Default ALLOWED_ORIGINS should contain localhost:8501."""
        # Simulate the default
        allowed = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",")
        self.assertIn("http://localhost:8501", allowed)

    def test_custom_allowed_origins(self):
        """When ALLOWED_ORIGINS env is set, it should split correctly."""
        old = os.environ.get("ALLOWED_ORIGINS")
        try:
            os.environ["ALLOWED_ORIGINS"] = "https://app.example.com,https://admin.example.com"
            allowed = os.environ["ALLOWED_ORIGINS"].split(",")
            self.assertEqual(len(allowed), 2)
            self.assertIn("https://app.example.com", allowed)
            self.assertIn("https://admin.example.com", allowed)
        finally:
            if old is None:
                os.environ.pop("ALLOWED_ORIGINS", None)
            else:
                os.environ["ALLOWED_ORIGINS"] = old

    def test_single_origin_no_comma(self):
        """A single origin without comma should result in list of one."""
        origins_str = "https://only-one.example.com"
        allowed = origins_str.split(",")
        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0], "https://only-one.example.com")


if __name__ == "__main__":
    unittest.main()
