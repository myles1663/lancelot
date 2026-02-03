import unittest
import os
import shutil
from fastapi.testclient import TestClient
from gateway import app, receipt_svc

class TestMFAAndReceipts(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
    def test_mfa_flow(self):
        """Verify MFA submission endpoint."""
        payload = {"code": "888888", "task_id": "MFA-TEST-001"}
        response = self.client.post("/mfa_submit", json=payload)
        
        self.assertEqual(response.status_code, 200)
        self.assertIn("Code Accepted", response.json()["status"])
        
    def test_receipt_generation(self):
        """Verify receipt file is created."""
        task_id = "RECEIPT-TEST-999"
        
        # Test Direct Service Call
        path = receipt_svc.generate_receipt(task_id)
        self.assertTrue(os.path.exists(path))
        with open(path, "r") as f:
            content = f.read()
            self.assertIn(task_id, content)
            self.assertIn("Visual Proof Placeholder", content)
            
        # Test via Gateway
        response = self.client.get(f"/receipt/{task_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["receipt_path"], path)

if __name__ == "__main__":
    unittest.main()
