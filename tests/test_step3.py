import unittest
import os
import time
import threading
import shutil
from indexer import MemoryIndexer
from orchestrator import LancelotOrchestrator

class TestMemorySystem(unittest.TestCase):
    def setUp(self):
        self.data_dir = "/home/lancelot/data"
        self.logs_dir = os.path.join(self.data_dir, "logs")
        
        # Clean up logs before test
        if os.path.exists(self.logs_dir):
            for f in os.listdir(self.logs_dir):
                if f.endswith(".txt"):
                    os.remove(os.path.join(self.logs_dir, f))
        
        self.indexer = MemoryIndexer(data_dir=self.data_dir)

    def test_log_indexing_and_retrieval(self):
        """Write a log, wait for index, query orchestrator."""
        
        # 1. Create a log file with unique info
        secret_info = "The launch code for Project Excalibur is 77-Alpha-Omega."
        log_file = os.path.join(self.logs_dir, "mission_log_001.txt")
        
        print(f"Writing log to {log_file}...")
        with open(log_file, "w") as f:
            f.write(f"Mission Update: {secret_info}")
            
        # 2. Start Indexer in a separate thread (It will scan existing files on init)
        print("Starting indexer...")
        self.indexer_thread = threading.Thread(target=self.indexer.start_watching, daemon=True)
        self.indexer_thread.start()
        
        # Wait for initialization and scanning
        print("Waiting 20 seconds for indexing (model download)...")
        time.sleep(20) 
        
        # 3. Query Orchestrator
        orchestrator = LancelotOrchestrator(data_dir=self.data_dir)
        query = "What is the launch code for Project Excalibur?"
        
        # Direct memory query check
        print(f"Querying memory for: '{query}'")
        retrieved_context = orchestrator.query_memory(query)
        print(f"Retrieved Context: {retrieved_context}")
        
        self.assertIn("77-Alpha-Omega", retrieved_context)
        
        # 4. (Optional) Full Chat Check if API Key exists
        if os.getenv("GEMINI_API_KEY"):
            print("Testing full LLM Chat with RAG...")
            response = orchestrator.chat(query)
            print(f"LLM Response: {response}")
            self.assertIn("77-Alpha-Omega", response)

if __name__ == "__main__":
    unittest.main()
