import unittest
import tempfile
import os
import hmac
import hashlib
import sys

# Stub external dependencies before importing orchestrator
sys.modules.setdefault("google.generativeai", type(sys)("google.generativeai"))
sys.modules.setdefault("chromadb", type(sys)("chromadb"))

from security import InputSanitizer
from orchestrator import LancelotOrchestrator


def _make_orchestrator(data_dir):
    """Create an orchestrator with stubbed-out external services."""
    # Write required memory files
    for fname in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
        with open(os.path.join(data_dir, fname), "w") as f:
            f.write("")

    # Monkey-patch expensive init methods
    orig_gemini = LancelotOrchestrator._init_gemini
    orig_memory = LancelotOrchestrator._init_memory_db
    LancelotOrchestrator._init_gemini = lambda self: None
    LancelotOrchestrator._init_memory_db = lambda self: None

    orch = LancelotOrchestrator(data_dir=data_dir)

    # Restore
    LancelotOrchestrator._init_gemini = orig_gemini
    LancelotOrchestrator._init_memory_db = orig_memory
    return orch


# ---------------------------------------------------------------------------
# S9-A: Rule content validation
# ---------------------------------------------------------------------------
class TestValidateRuleContent(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_valid_short_rule_accepted(self):
        valid, reason = self.orch._validate_rule_content("Always greet the user.")
        self.assertTrue(valid)
        self.assertEqual(reason, "")

    def test_rule_exceeding_500_chars_rejected(self):
        long_rule = "A" * 501
        valid, reason = self.orch._validate_rule_content(long_rule)
        self.assertFalse(valid)
        self.assertIn("500", reason)

    def test_rule_exactly_500_chars_accepted(self):
        rule = "B" * 500
        valid, reason = self.orch._validate_rule_content(rule)
        self.assertTrue(valid)

    def test_subprocess_blocked(self):
        valid, reason = self.orch._validate_rule_content("Run subprocess to check status")
        self.assertFalse(valid)
        self.assertIn("subprocess", reason)

    def test_os_system_blocked(self):
        valid, reason = self.orch._validate_rule_content("Call os.system for cleanup")
        self.assertFalse(valid)
        self.assertIn("os.system", reason)

    def test_exec_blocked(self):
        valid, reason = self.orch._validate_rule_content("Use exec( code ) here")
        self.assertFalse(valid)
        self.assertIn("exec(", reason)

    def test_eval_blocked(self):
        valid, reason = self.orch._validate_rule_content("Run eval( expression )")
        self.assertFalse(valid)
        self.assertIn("eval(", reason)

    def test_import_blocked(self):
        valid, reason = self.orch._validate_rule_content("import os then delete")
        self.assertFalse(valid)
        self.assertIn("import ", reason)

    def test_http_url_blocked(self):
        valid, reason = self.orch._validate_rule_content("Fetch from http://evil.com")
        self.assertFalse(valid)
        self.assertIn("URL", reason)

    def test_https_url_blocked(self):
        valid, reason = self.orch._validate_rule_content("Download https://evil.com/payload")
        self.assertFalse(valid)
        self.assertIn("URL", reason)


# ---------------------------------------------------------------------------
# S9-B: Confidence clamping
# ---------------------------------------------------------------------------
class TestConfidenceClamping(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_confidence_above_100_clamped(self):
        """A confidence of 150 should be treated as 100 (>90 branch)."""
        response = self.orch._parse_response("Confidence: 150 Action: do something")
        # 100 > 90 -> high confidence route (no DRAFT prefix)
        self.assertNotIn("DRAFT", response)
        self.assertNotIn("PERMISSION REQUIRED", response)

    def test_confidence_negative_clamped_to_zero(self):
        """Negative-ish: the regex only matches digits so this tests 0 floor."""
        response = self.orch._parse_response("Confidence: 0 Some low message")
        self.assertIn("PERMISSION REQUIRED", response)

    def test_normal_confidence_not_altered(self):
        response = self.orch._parse_response("Confidence: 75 Draft idea here")
        self.assertIn("DRAFT", response)


# ---------------------------------------------------------------------------
# S9-C: Rule candidates logged (not auto-written to RULES.md)
# ---------------------------------------------------------------------------
class TestRuleCandidateLogging(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_high_confidence_action_writes_candidate(self):
        """When confidence >90 and response starts with Action:, candidate is logged."""
        self.orch._parse_response("Confidence: 95 Action: Enable dark mode")

        candidate_path = os.path.join(self.data_dir, "RULE_CANDIDATES.md")
        self.assertTrue(os.path.exists(candidate_path))
        with open(candidate_path, "r") as f:
            content = f.read()
        self.assertIn("Enable dark mode", content)

    def test_high_confidence_action_does_not_write_rules(self):
        """RULES.md should NOT be modified by _parse_response for >90 actions."""
        rules_path = os.path.join(self.data_dir, "RULES.md")
        with open(rules_path, "r") as f:
            before = f.read()

        self.orch._parse_response("Confidence: 95 Action: Enable dark mode")

        with open(rules_path, "r") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_log_rule_candidate_appends(self):
        self.orch._log_rule_candidate("- Rule one")
        self.orch._log_rule_candidate("- Rule two")

        candidate_path = os.path.join(self.data_dir, "RULE_CANDIDATES.md")
        with open(candidate_path, "r") as f:
            content = f.read()
        self.assertIn("Rule one", content)
        self.assertIn("Rule two", content)


# ---------------------------------------------------------------------------
# S9-D: HMAC integrity for RULES.md
# ---------------------------------------------------------------------------
class TestHMACIntegrity(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_update_rules_creates_sig_file(self):
        """After a valid _update_rules call, RULES.md.sig should exist."""
        self.orch._update_rules("- Safe rule content")
        sig_path = os.path.join(self.data_dir, "RULES.md.sig")
        self.assertTrue(os.path.exists(sig_path))

    def test_sig_matches_rules_content(self):
        """The written signature should match HMAC-SHA256 of RULES.md."""
        self.orch._update_rules("- Another safe rule")
        rules_path = os.path.join(self.data_dir, "RULES.md")
        sig_path = os.path.join(self.data_dir, "RULES.md.sig")

        hmac_key = os.getenv("LANCELOT_HMAC_KEY", "default-dev-key")
        with open(rules_path, "rb") as f:
            expected = hmac.new(hmac_key.encode(), f.read(), hashlib.sha256).hexdigest()
        with open(sig_path, "r") as f:
            stored = f.read().strip()
        self.assertEqual(expected, stored)

    def test_tampered_rules_detected_on_load(self, ):
        """If RULES.md is modified after signing, _load_memory should warn."""
        self.orch._update_rules("- Signed rule")
        # Tamper with RULES.md
        rules_path = os.path.join(self.data_dir, "RULES.md")
        with open(rules_path, "a") as f:
            f.write("\n- INJECTED EVIL RULE")

        # Capture printed output
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.orch._load_memory()

        output = buf.getvalue()
        self.assertIn("HMAC signature mismatch", output)

    def test_valid_sig_no_warning(self):
        """A correctly signed RULES.md should not produce a warning."""
        self.orch._update_rules("- Good rule")

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.orch._load_memory()

        output = buf.getvalue()
        self.assertNotIn("mismatch", output)


# ---------------------------------------------------------------------------
# S9-E: _update_rules rejects invalid content
# ---------------------------------------------------------------------------
class TestUpdateRulesValidation(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_update_rules_rejects_dangerous_content(self):
        """_update_rules should refuse content with subprocess."""
        rules_path = os.path.join(self.data_dir, "RULES.md")
        with open(rules_path, "r") as f:
            before = f.read()

        self.orch._update_rules("Run subprocess.call(['rm', '-rf', '/'])")

        with open(rules_path, "r") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_update_rules_rejects_long_content(self):
        rules_path = os.path.join(self.data_dir, "RULES.md")
        with open(rules_path, "r") as f:
            before = f.read()

        self.orch._update_rules("X" * 501)

        with open(rules_path, "r") as f:
            after = f.read()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
