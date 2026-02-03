import unittest
import tempfile
import time
import os
from mcp_sentry import MCPSentry, APPROVAL_TTL, MAX_REQUESTS_PER_MINUTE


class TestUnknownToolDenied(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.data_dir, "mcp_configs"), exist_ok=True)
        with open(os.path.join(self.data_dir, "MEMORY_SUMMARY.md"), "w") as f:
            f.write("")
        self.sentry = MCPSentry(data_dir=self.data_dir)

    def test_unknown_tool_returns_pending(self):
        result = self.sentry.check_permission("totally_unknown_tool", {"x": 1})
        self.assertEqual(result["status"], "PENDING")
        self.assertIn("High-Risk", result["message"])

    def test_known_low_risk_tool_approved(self):
        self.sentry.tools["safe_tool"] = {"name": "safe_tool", "risk": "low"}
        result = self.sentry.check_permission("safe_tool", {"x": 1})
        self.assertEqual(result["status"], "APPROVED")


class TestApprovalExpiry(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.data_dir, "mcp_configs"), exist_ok=True)
        with open(os.path.join(self.data_dir, "MEMORY_SUMMARY.md"), "w") as f:
            f.write("")
        self.sentry = MCPSentry(data_dir=self.data_dir)

    def test_approved_request_within_ttl_works(self):
        result = self.sentry.check_permission("cli_shell", {"cmd": "ls"})
        req_id = result["request_id"]
        self.sentry.approve_request(req_id)
        # Same params should find the approval
        result2 = self.sentry.check_permission("cli_shell", {"cmd": "ls"})
        self.assertEqual(result2["status"], "APPROVED")

    def test_expired_approval_creates_new_pending(self):
        result = self.sentry.check_permission("cli_shell", {"cmd": "ls"})
        req_id = result["request_id"]
        self.sentry.approve_request(req_id)
        # Simulate expiry by backdating
        self.sentry.pending_requests[req_id]["_created_at"] = time.time() - APPROVAL_TTL - 10
        result2 = self.sentry.check_permission("cli_shell", {"cmd": "ls"})
        self.assertEqual(result2["status"], "PENDING")
        self.assertNotEqual(result2["request_id"], req_id)


class TestRateLimit(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.data_dir, "mcp_configs"), exist_ok=True)
        with open(os.path.join(self.data_dir, "MEMORY_SUMMARY.md"), "w") as f:
            f.write("")
        self.sentry = MCPSentry(data_dir=self.data_dir)
        self.sentry.tools["safe_tool"] = {"name": "safe_tool", "risk": "low"}

    def test_within_rate_limit_allowed(self):
        for i in range(5):
            result = self.sentry.check_permission("safe_tool", {"i": i})
            self.assertEqual(result["status"], "APPROVED")

    def test_exceeding_rate_limit_denied(self):
        for i in range(MAX_REQUESTS_PER_MINUTE):
            self.sentry.check_permission("safe_tool", {"i": i})
        result = self.sentry.check_permission("safe_tool", {"i": 999})
        self.assertEqual(result["status"], "DENIED")
        self.assertIn("Rate limit", result["message"])


class TestExplicitDeny(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.data_dir, "mcp_configs"), exist_ok=True)
        with open(os.path.join(self.data_dir, "MEMORY_SUMMARY.md"), "w") as f:
            f.write("")
        self.sentry = MCPSentry(data_dir=self.data_dir)

    def test_deny_request_sets_denied(self):
        result = self.sentry.check_permission("unknown_tool", {"x": 1})
        req_id = result["request_id"]
        self.assertTrue(self.sentry.deny_request(req_id))
        self.assertEqual(self.sentry.pending_requests[req_id]["status"], "DENIED")

    def test_deny_nonexistent_returns_false(self):
        self.assertFalse(self.sentry.deny_request("nonexistent-id"))


class TestCleanup(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.data_dir, "mcp_configs"), exist_ok=True)
        with open(os.path.join(self.data_dir, "MEMORY_SUMMARY.md"), "w") as f:
            f.write("")
        self.sentry = MCPSentry(data_dir=self.data_dir)

    def test_expired_entries_cleaned_up(self):
        # Create an expired entry manually
        self.sentry.pending_requests["old-id"] = {
            "tool": "test",
            "params": {},
            "status": "PENDING",
            "_created_at": time.time() - APPROVAL_TTL - 100,
        }
        self.sentry._cleanup_expired()
        self.assertNotIn("old-id", self.sentry.pending_requests)

    def test_fresh_entries_preserved(self):
        self.sentry.pending_requests["new-id"] = {
            "tool": "test",
            "params": {},
            "status": "PENDING",
            "_created_at": time.time(),
        }
        self.sentry._cleanup_expired()
        self.assertIn("new-id", self.sentry.pending_requests)


if __name__ == "__main__":
    unittest.main()
