import unittest
import os
import shutil
import time
from unittest.mock import MagicMock
from librarian import Librarian

class TestLibrarian(unittest.TestCase):
    def setUp(self):
        self.data_dir = "/home/lancelot/data"
        self.librarian = Librarian(data_dir=self.data_dir)
        
        # Mock Gemini
        self.librarian.model = MagicMock()
        
        # Clean up folders if they exist
        for cat in ["Documents", "Code", "Images"]:
            path = os.path.join(self.data_dir, cat)
            if os.path.exists(path):
                shutil.rmtree(path)
        
        # Clean logs
        self.log_file = os.path.join(self.data_dir, "librarian.log")
        if os.path.exists(self.log_file):
            os.remove(self.log_file)

    def test_organize_code(self):
        """Test sorting a Python file."""
        # Setup Mock Response
        mock_response = MagicMock()
        mock_response.text = "Summary: A python script. | Category: Code"
        self.librarian.model.generate_content.return_value = mock_response
        
        # Create Dummy File
        filename = "test_script.py"
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, "w") as f:
            f.write("print('Hello World')")
            
        # Process
        self.librarian.process_file(filepath)
        
        # Verify Move
        dst = os.path.join(self.data_dir, "Code", filename)
        self.assertTrue(os.path.exists(dst))
        self.assertFalse(os.path.exists(filepath))
        
        # Verify Log
        with open(self.log_file, "r") as f:
            self.assertIn("Organized into Code", f.read())

    def test_organize_doc(self):
        """Test sorting a text document."""
        mock_response = MagicMock()
        mock_response.text = "Summary: Meeting notes. | Category: Documents"
        self.librarian.model.generate_content.return_value = mock_response
        
        filename = "notes.txt"
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, "w") as f:
            f.write("Meeting details...")
            
        self.librarian.process_file(filepath)
        
        dst = os.path.join(self.data_dir, "Documents", filename)
        self.assertTrue(os.path.exists(dst))

    def test_metadata_tagging(self):
        """Verify MEMORY_SUMMARY.md update."""
        mock_response = MagicMock()
        mock_response.text = "Summary: Important info. | Category: Documents"
        self.librarian.model.generate_content.return_value = mock_response
        
        filename = "info.txt"
        filepath = os.path.join(self.data_dir, filename)
        with open(filepath, "w") as f:
            f.write("Info")
            
        self.librarian.process_file(filepath)
        
        summary_path = os.path.join(self.data_dir, "MEMORY_SUMMARY.md")
        with open(summary_path, "r") as f:
            content = f.read()
            self.assertIn(f"**{filename}** ([Documents]): Important info.", content)

if __name__ == "__main__":
    unittest.main()
