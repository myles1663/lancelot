import os
import shutil
import time
import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from google import genai
from google.genai import types
from receipts import create_receipt, get_receipt_service, ActionType, ReceiptStatus, CognitionTier

class FileAction:
    def __init__(self, log_path="/home/lancelot/data/librarian.log", receipt_service=None):
        self.log_path = log_path
        self.receipt_service = receipt_service

    def log_action(self, action: str, file_name: str, justification: str):
        timestamp = datetime.datetime.utcnow().isoformat()
        entry = f"[{timestamp}] Action: {action} | File: {file_name} | Justification: {justification}\n"
        try:
            with open(self.log_path, "a") as f:
                f.write(entry)
        except Exception as e:
            print(f"Error writing to librarian log: {e}")

    def safe_move(self, src: str, dst_folder: str, justification: str):
        """Moves file to destination folder, creating it if needed."""
        # Create Receipt for File Op
        receipt = None
        if self.receipt_service:
            receipt = create_receipt(
                ActionType.FILE_OP,
                "move_file",
                {"src": src, "dst_folder": dst_folder, "reason": justification},
                tier=CognitionTier.DETERMINISTIC
            )
            self.receipt_service.create(receipt)
        
        start_time = __import__("time").time()

        try:
            if not os.path.exists(dst_folder):
                os.makedirs(dst_folder)
            
            filename = os.path.basename(src)
            dst = os.path.join(dst_folder, filename)
            
            # Handle collision
            if os.path.exists(dst):
                base, ext = os.path.splitext(filename)
                timestamp = int(time.time())
                dst = os.path.join(dst_folder, f"{base}_{timestamp}{ext}")

            shutil.move(src, dst)
            self.log_action("MOVE", filename, justification)
            
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.complete({"dst": dst}, duration))
                
            return dst
        except Exception as e:
            print(f"Error moving file: {e}")
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(str(e), duration))
            return None

    def safe_delete(self, src: str, justification: str):
        """Moves file to .trash folder for 24h retention."""
        trash_dir = "/home/lancelot/data/.trash"
        
        # Receipt for delete handled by safe_move, but we could add a parent receipt here?
        # Actually safe_move creates its own receipt.
        # But we want to capture the Intent "DELETE".
        
        receipt = None
        if self.receipt_service:
            receipt = create_receipt(
                ActionType.FILE_OP,
                "delete_file",
                {"src": src, "reason": justification},
                tier=CognitionTier.DETERMINISTIC
            )
            self.receipt_service.create(receipt)
            
        start_time = __import__("time").time()
        
        res = self.safe_move(src, trash_dir, f"DELETE (Recycle Bin): {justification}")
        
        if receipt:
            duration = int((__import__("time").time() - start_time) * 1000)
            if res:
                self.receipt_service.update(receipt.complete({"trash_path": res}, duration))
            else:
                self.receipt_service.update(receipt.fail("Move to trash failed", duration))
                
        return res

    def safe_copy(self, src: str, dst_folder: str, justification: str = "Client Request"):
        """Copies file to destination folder."""
        receipt = None
        if self.receipt_service:
            receipt = create_receipt(ActionType.FILE_OP, "copy_file", {"src": src, "dst": dst_folder}, tier=CognitionTier.DETERMINISTIC)
            self.receipt_service.create(receipt)
            
        start_time = __import__("time").time()
        try:
            if not os.path.exists(dst_folder):
                os.makedirs(dst_folder)
            
            filename = os.path.basename(src)
            dst = os.path.join(dst_folder, filename)
            
            # Handle collision
            if os.path.exists(dst):
                base, ext = os.path.splitext(filename)
                timestamp = int(time.time())
                dst = os.path.join(dst_folder, f"{base}_{timestamp}_copy{ext}")

            shutil.copy2(src, dst)
            self.log_action("COPY", filename, justification)
            
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.complete({"dst": dst}, duration))
            return dst
        except Exception as e:
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(str(e), duration))
            return None

    def safe_mkdir(self, path: str, justification: str = "Client Request"):
        """Creates a directory safely."""
        receipt = None
        if self.receipt_service:
            receipt = create_receipt(ActionType.FILE_OP, "mkdir", {"path": path}, tier=CognitionTier.DETERMINISTIC)
            self.receipt_service.create(receipt)
            
        start_time = __import__("time").time()
        try:
            os.makedirs(path, exist_ok=True)
            self.log_action("MKDIR", path, justification)
            if receipt:
                 duration = int((__import__("time").time() - start_time) * 1000)
                 self.receipt_service.update(receipt.complete({}, duration))
            return True
        except Exception as e:
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(str(e), duration))
            return False

    def touch(self, path: str, justification: str = "Client Request"):
        """Touches a file (creates empty or updates mtime)."""
        receipt = None
        if self.receipt_service:
            receipt = create_receipt(ActionType.FILE_OP, "touch", {"path": path}, tier=CognitionTier.DETERMINISTIC)
            self.receipt_service.create(receipt)
            
        start_time = __import__("time").time()
        try:
            # Ensure dir exists
            parent_dir = os.path.dirname(path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)
                
            with open(path, 'a'):
                os.utime(path, None)
                
            self.log_action("TOUCH", path, justification)
            
            if receipt:
                 duration = int((__import__("time").time() - start_time) * 1000)
                 self.receipt_service.update(receipt.complete({}, duration))
            return True
        except Exception as e:
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(str(e), duration))
            return False
            
    def write_file(self, path: str, content: str, justification: str = "Automated Write"):
        """Writes content to a file safely."""
        receipt = None
        if self.receipt_service:
            receipt = create_receipt(ActionType.FILE_OP, "write_file", {"path": path}, tier=CognitionTier.DETERMINISTIC)
            self.receipt_service.create(receipt)
            
        start_time = __import__("time").time()
        try:
             # Ensure dir exists
            parent_dir = os.path.dirname(path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)
                
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
                
            self.log_action("WRITE", path, justification)
            
            if receipt:
                 duration = int((__import__("time").time() - start_time) * 1000)
                 self.receipt_service.update(receipt.complete({"size": len(content)}, duration))
            return True
        except Exception as e:
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(str(e), duration))
            return False
            
    def write_file(self, path: str, content: str, justification: str = "Automated Write"):
        """Writes content to a file safely."""
        receipt = None
        if self.receipt_service:
            receipt = create_receipt(ActionType.FILE_OP, "write_file", {"path": path}, tier=CognitionTier.DETERMINISTIC)
            self.receipt_service.create(receipt)
            
        start_time = __import__("time").time()
        try:
             # Ensure dir exists
            parent_dir = os.path.dirname(path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)
                
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
                
            self.log_action("WRITE", path, justification)
            
            if receipt:
                 duration = int((__import__("time").time() - start_time) * 1000)
                 self.receipt_service.update(receipt.complete({"size": len(content)}, duration))
            return True
        except Exception as e:
            if receipt:
                duration = int((__import__("time").time() - start_time) * 1000)
                self.receipt_service.update(receipt.fail(str(e), duration))
            return False

class LibrarianHandler(FileSystemEventHandler):
    def __init__(self, librarian):
        self.librarian = librarian

    def on_created(self, event):
        if event.is_directory:
            return
        self.librarian.process_queue.append(event.src_path)

class Librarian:
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.receipt_service = get_receipt_service(data_dir)
        self.action_handler = FileAction(receipt_service=self.receipt_service)
        self.process_queue = []
        self.observer = Observer()
        self.client = None
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        self._init_gemini()
        
        # Ignored paths to prevent loops
        self.ignored_dirs = [".trash", "logs", "chroma_db", "artifacts"]

    def _init_gemini(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            self.client = genai.Client(api_key=api_key)
            self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    def start_watching(self):
        """Starts the watchdog observer."""
        event_handler = LibrarianHandler(self)
        self.observer.schedule(event_handler, self.data_dir, recursive=False) # Not recursive to avoid loops in subfolders
        self.observer.start()
        print("Librarian is watching...")

    def _is_ignored(self, path: str) -> bool:
        """Checks if path is in an ignored directory."""
        for ignored in self.ignored_dirs:
            if ignored in path.split(os.sep):
                return True
        return False

    def process_file(self, file_path):
        """Analyzes and organizes a single file."""
        if self._is_ignored(file_path):
            return

        filename = os.path.basename(file_path)
        if filename.startswith(".") or filename in ["USER.md", "RULES.md", "MEMORY_SUMMARY.md", "audit.log", "librarian.log"]:
            return

        print(f"Librarian processing: {filename}")
        
        # Create Receipt for Analysis
        start_time = __import__("time").time()
        receipt = create_receipt(
            ActionType.LLM_CALL, # It involves LLM usually
            "analyze_file",
            {"filename": filename, "path": file_path},
            tier=CognitionTier.CLASSIFICATION
        )
        self.receipt_service.create(receipt)
        
        # 1. Analyze Content
        try:
            with open(file_path, "r", errors='ignore') as f:
                content = f.read(2000) # Read first 2k chars
            
            # Simple simulation of analysis success for receipt
            # In vNext this would call an LLM
            
            duration = int((__import__("time").time() - start_time) * 1000)
            self.receipt_service.update(receipt.complete(
                {"status": "analyzed", "preview_len": len(content)}, 
                duration
            ))
                
        except Exception as e:
            # Not raising here to keep process alive, but failing receipt
            duration = int((__import__("time").time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(str(e), duration))
            print(f"Error reading file {filename}: {e}")
            return
            print(f"Could not read file {filename}: {e}")
            return

        if not self.client:
            print("Gemini not ready, skipping tag.")
            return

        try:
            prompt = (
                f"Analyze this file content and provide: 1. A 1-sentence summary. "
                f"2. A Category (one of: Documents, Images, Code, Data, Other). "
                f"Format: Summary: ... | Category: ...\n\nContent:\n{content}"
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            text = response.text.strip()
            
            # Parse
            summary = text.split("|")[0].replace("Summary:", "").strip()
            category = "Other"
            if "Category:" in text:
                category = text.split("Category:")[1].strip()
                # Clean up category
                for valid in ["Documents", "Images", "Code", "Data"]:
                    if valid in category:
                        category = valid
                        break

            # 2. Update Memory
            self._update_memory_summary(filename, summary, category)
            
            # 3. Organize
            dst_folder = os.path.join(self.data_dir, category)
            self.action_handler.safe_move(file_path, dst_folder, f"Organized into {category} based on content analysis.")
            
        except Exception as e:
            print(f"Error processing file with Gemini: {e}")

    def _update_memory_summary(self, filename, summary, category):
        summary_path = os.path.join(self.data_dir, "MEMORY_SUMMARY.md")
        try:
            with open(summary_path, "a") as f:
                f.write(f"\n- **{filename}** ([{category}]): {summary}")
        except Exception:
            pass

    def check_queue(self):
        """Manual trigger to process queue (simplifies threading model for this script)."""
        while self.process_queue:
            path = self.process_queue.pop(0)
            # Verify it still exists and is not in ignored
            if os.path.exists(path):
                self.process_file(path)
