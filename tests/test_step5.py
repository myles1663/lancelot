import unittest
from unittest.mock import MagicMock, patch
from streamlit.testing.v1 import AppTest
import os

class TestWarRoom(unittest.TestCase):
    def setUp(self):
        # We need to ensure we are in the right directory for relative imports if any
        os.chdir("/home/lancelot/app")

    def test_app_loads(self):
        """Test that the app starts without error."""
        at = AppTest.from_file("war_room.py")
        at.run()
        self.assertFalse(at.exception, f"App failed to load: {at.exception}")

    def test_draft_alert(self):
        """Test that a DRAFT response triggers a warning."""
        # We need to patch the Orchestrator used in war_room.py
        # Since AppTest runs in a separate context, patching is tricky.
        # However, we can mock the secrets or session state if logic allows.
        # Alternatively, for this check, we verify the Orchestrator returns DRAFT
        # and assume the UI handles it (verified by code inspection), 
        # OR we try to inject a mock into session_state before run.
        
        at = AppTest.from_file("war_room.py")
        
        # Mock the orchestrator in session state
        mock_orch = MagicMock()
        mock_orch.chat.return_value = "DRAFT: 85 Action: Test Draft"
        
        at.run() # First run to init
        at.session_state["orchestrator"] = mock_orch
        
        # Simulate Input
        at.chat_input[0].set_value("Trigger Draft").run()
        
        # Verify Warning appears
        # Streamlit testing: warning elements are usually in .warning
        self.assertTrue(len(at.warning) > 0, "No warning displayed for DRAFT response")
        self.assertIn("Review Required", at.warning[0].value)
        
        # Verify Chat output
        # Last message is "pending approval", second to last is the response content
        # We search in recent elements
        values = [m.value for m in at.markdown]
        self.assertTrue(any("DRAFT: 85" in v for v in values), "DRAFT content not found in markdown elements")

if __name__ == "__main__":
    unittest.main()
