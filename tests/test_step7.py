import unittest
import unittest.mock
import os
import shutil
from fastapi.testclient import TestClient
from gateway import app, onboarding_orch

class TestOnboardingFlow(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.data_dir = "/home/lancelot/data"
        self.user_file = os.path.join(self.data_dir, "USER.md")
        self.env_file = ".env"
        
        # Clean Start: Remove USER.md and unset API Key in internal state
        if os.path.exists(self.user_file):
            os.remove(self.user_file)
        if os.path.exists(self.env_file):
            os.remove(self.env_file)
        
        # We also need to ensure the orchestrator instance sees the clean state
        # Since it determines state in __init__, we force a re-check or re-init logic if exposed
        # But process() calls _determine_state() dynamically in our updated gateway logic? 
        # Let's check gateway.py content... 
        # Yes: onboarding_orch.state = onboarding_orch._determine_state()
        
        # Also need to ensure API key check fails initially
        # We can mock os.getenv or just ensure .env doesnt have it. 
        # But Docker env vars might be set. 
        # We will mock the _determine_state method's key check or the os.getenv call 
        # to ensure we can simulate the HANDSHAKE state even if env var exists.

    def test_full_onboarding(self):
        """Simulate the Squire's Journey from Welcome to Ready."""
        
        # 1. WELCOME -> BONDING
        # Force state to WELCOME by ensuring file missing (done in setUp)
        
        # Step 1: Initial Hello
        payload = {"text": "Hello", "user": "Arthur"}
        print("\n[User]: Hello")
        response = self.client.post("/chat", json=payload)
        text = response.json()["response"]
        print(f"[Lancelot]: {text}")
        
        self.assertIn("bonded to your account", text)
        self.assertTrue(os.path.exists(self.user_file))
            
        # 2. BONDING -> HANDSHAKE
        # We need to ensure the system thinks Key is MISSING.
        # Check current env
        real_key = os.environ.get("GEMINI_API_KEY")
        if real_key:
            del os.environ["GEMINI_API_KEY"]
            
        try:
            # Send next message (should trigger API key prompt or processing)
            # Actually, the previous response asked for the key. State was set to HANDSHAKE internally by _bond_identity
            # But gateway calls _determine_state again.
            # _determine_state checks file (exists) -> checks key (missing) -> HANDSHAKE.
            # So we are in HANDSHAKE.
            
            payload = {"text": "MySecretKey_12345", "user": "Arthur"}
            print(f"\n[User]: {payload['text']}")
            response = self.client.post("/chat", json=payload)
            text = response.json()["response"]
            print(f"[Lancelot]: {text}")
            
            self.assertIn("Fortress is secure", text)
            self.assertIn("Salesforce", text)
            
            # Verify Key Saved
            with open(".env", "r") as f:
                env_content = f.read()
            self.assertIn("GEMINI_API_KEY=MySecretKey_12345", env_content)
            
            # 3. QUEST SELECTION
            # State should be CALIBRATION -> QUEST_SELECTION
            # The previous return was from _calibrate called by _save_api_key.
            
            payload = {"text": "Organize downloads", "user": "Arthur"}
            print(f"\n[User]: {payload['text']}")
            response = self.client.post("/chat", json=payload)
            text = response.json()["response"]
            print(f"[Lancelot]: {text}")
            
            self.assertIn("downloads organization", text)
            self.assertIn("Task Started", text)
            
        finally:
            # Restore Env if needed
            if real_key:
                os.environ["GEMINI_API_KEY"] = real_key

if __name__ == "__main__":
    unittest.main()
