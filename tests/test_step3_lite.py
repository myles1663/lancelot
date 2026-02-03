import unittest
import os
import time
import threading
from indexer import MemoryIndexer
from orchestrator import LancelotOrchestrator

class TestMemorySystemLite(unittest.TestCase):
    def setUp(self):
        self.data_dir = "/home/lancelot/data"
        self.logs_dir = os.path.join(self.data_dir, "logs")
        
        # Clean up logs before test
        if os.path.exists(self.logs_dir):
            for f in os.listdir(self.logs_dir):
                if f.endswith(".txt"):
                    os.remove(os.path.join(self.logs_dir, f))
        
        self.indexer = MemoryIndexer(data_dir=self.data_dir)

    def test_rag_retrieval(self):
        """Write a log, wait for index, query orchestrator for context (No LLM)."""
        
        secret_info = "Code Name: Project Excalibur - Status: Go"
        log_file = os.path.join(self.logs_dir, "status_log.txt")
        
        print(f"Writing log to {log_file}...")
        with open(log_file, "w") as f:
            f.write(secret_info)
            
        print("Starting indexer...")
        self.indexer_thread = threading.Thread(target=self.indexer.start_watching, daemon=True)
        self.indexer_thread.start()
        
        print("Waiting 10 seconds for indexing...")
        time.sleep(10) # 10s should be enough if model is cached, else 20
        
        orchestrator = LancelotOrchestrator(data_dir=self.data_dir)
        query = "What is the status of Project Excalibur?"
        
        print(f"Querying memory for: '{query}'")
        retrieved_context = orchestrator.query_memory(query)
        print(f"Retrieved Context: {retrieved_context}")
        
        self.assertIn("Code Name: Project Excalibur", retrieved_context)
        self.assertIn("Status: Go", retrieved_context)

if __name__ == "__main__":
    unittest.main()
