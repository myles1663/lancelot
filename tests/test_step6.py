import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from gateway import app
from orchestrator import LancelotOrchestrator

class TestGatewayAndCalendar(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
    def test_webhook_schedule_flow(self):
        """Simulate a Google Chat webhook triggering a calendar event."""
        
        # We need to mock the Orchestrator's model within the gateway's instance
        # Since 'gateway.orchestrator' is global, we patch it
        with patch("gateway.main_orchestrator.model") as mock_model:
            # Mock LLM response to be High Confidence Schedule Action
            mock_model.generate_content.return_value.text = (
                "Confidence: 95\n"
                "Action: Schedule a launch meeting for Project Excalibur."
            )
            
            payload = {"text": "Book a meeting for the launch", "user": "Arthur"}
            
            print(f"\nSending Webhook Payload: {payload}")
            response = self.client.post("/chat", json=payload)
            
            print(f"Gateway Response: {response.json()}")
            
            self.assertEqual(response.status_code, 200)
            json_resp = response.json()
            
            # Verify Orchestrator Logic
            self.assertIn("response", json_resp)
            text = json_resp["response"]
            
            # Check for MOCK calendar event creation
            self.assertIn("ACTION EXECUTED", text)
            self.assertIn("[MOCK] Event", text)
            self.assertIn("Project Excalibur", text)

if __name__ == "__main__":
    unittest.main()
