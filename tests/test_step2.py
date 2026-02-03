import sys
import unittest
from orchestrator import LancelotOrchestrator
import os

class TestLancelotOrchestrator(unittest.TestCase):
    def setUp(self):
        # Ensure we are checking the correct path (in container it is /home/lancelot/data)
        self.orchestrator = LancelotOrchestrator()

    def test_memory_load(self):
        """Verify that memory files are loaded."""
        self.assertIn("Name: Arthur", self.orchestrator.user_context)
        self.assertIn("Lancelot", self.orchestrator.rules_context)

    def test_hello_world_command(self):
        """Verify simple terminal command execution."""
        # 'echo "Hello World"' usually prints Hello World (quotes stripped by shell)
        output = self.orchestrator.execute_command("echo Hello World")
        self.assertEqual(output.strip(), "Hello World")
        
        output_quoted = self.orchestrator.execute_command("echo 'Hello World'")
        # shlex.split() properly handles shell quoting, stripping quotes before passing to subprocess.
        self.assertEqual(output_quoted.strip(), "Hello World")

    def test_identity_acknowledgement(self):
        """Verify Lancelot acknowledges identity from USER.md."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "MySecretKey_12345":
            print("SKIPPING LLM TEST: No valid GEMINI_API_KEY provided.")
            return

        response = self.orchestrator.chat("Who are you and who is your user?")
        print(f"\nLLM Response: {response}\n")

        # Check for keywords from RULES.md or USER.md
        self.assertTrue("Lancelot" in response or "lancelot" in response.lower())
        self.assertTrue("Arthur" in response or "Commander" in response)

if __name__ == '__main__':
    unittest.main()
