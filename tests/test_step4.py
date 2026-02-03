import unittest
from unittest.mock import MagicMock, patch
import os
import shutil
from orchestrator import LancelotOrchestrator

class TestConfidenceEngine(unittest.TestCase):
    def setUp(self):
        self.data_dir = "/home/lancelot/data"
        self.rules_path = os.path.join(self.data_dir, "RULES.md")
        
        # Backup RULES.md
        if os.path.exists(self.rules_path):
            shutil.copy(self.rules_path, self.rules_path + ".bak")
            
        self.orchestrator = LancelotOrchestrator(data_dir=self.data_dir)
        
        # Mock the model
        self.orchestrator.model = MagicMock()

    def tearDown(self):
        # Restore RULES.md
        bak = self.rules_path + ".bak"
        if os.path.exists(bak):
            shutil.move(bak, self.rules_path)

    def test_low_confidence_gate(self):
        """Test that low confidence triggers a permission block."""
        
        # Mock response
        mock_response = MagicMock()
        mock_response.text = "Confidence: 60\nAction: Delete system32"
        self.orchestrator.model.generate_content.return_value = mock_response
        
        response = self.orchestrator.chat("Delete everything")
        print(f"\nLow Confidence Response: {response}")
        
        self.assertIn("PERMISSION REQUIRED", response)
        self.assertIn("60", response)

    def test_high_confidence_learning(self):
        """Test that high confidence updates RULES.md."""
        
        # Mock response
        mock_response = MagicMock()
        mock_response.text = "Confidence: 95\nAction: Always backup data before deletion."
        self.orchestrator.model.generate_content.return_value = mock_response
        
        # Read rules before
        with open(self.rules_path, "r") as f:
            rules_before = f.read()
            
        print("\nSending High Confidence Action...")
        response = self.orchestrator.chat("What is the safety protocol?")
        
        # Read rules after
        with open(self.rules_path, "r") as f:
            rules_after = f.read()
            
        print(f"Rules appended: \n{rules_after[len(rules_before):]}")
        
        self.assertIn("Always backup data", rules_after)
        self.assertIn("[Learned Rule]", rules_after)

    def test_normal_confidence(self):
        """Test that normal confidence passes through without side effects."""
        
        mock_response = MagicMock()
        mock_response.text = "Confidence: 80\nAction: Listing directory contents."
        self.orchestrator.model.generate_content.return_value = mock_response
        
        # Read rules before
        with open(self.rules_path, "r") as f:
            rules_before = f.read()
            
        response = self.orchestrator.chat("ls")
        
        # Read rules after
        with open(self.rules_path, "r") as f:
            rules_after = f.read()
            
        self.assertEqual(rules_before, rules_after)
        self.assertNotIn("PERMISSION REQUIRED", response)
        self.assertIn("Listing directory", response)

if __name__ == "__main__":
    unittest.main()
