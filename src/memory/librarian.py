import os
import shutil
import time
import datetime
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from receipts import create_receipt, get_receipt_service, ActionType, CognitionTier


logger = logging.getLogger(__name__)

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
            logger.warning("Error writing to librarian log: %s", e)

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
            logger.warning("Error moving file %s: %s", src, e)
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

        # Ignored paths to prevent loops
        self.ignored_dirs = [".trash", "logs", "chroma_db", "artifacts"]

    def start_watching(self):
        """Starts the watchdog observer."""
        event_handler = LibrarianHandler(self)
        self.observer.schedule(event_handler, self.data_dir, recursive=False) # Not recursive to avoid loops in subfolders
        self.observer.start()
        logger.info("Librarian is watching %s", self.data_dir)

    def _is_ignored(self, path: str) -> bool:
        """Checks if path is in an ignored directory."""
        for ignored in self.ignored_dirs:
            if ignored in path.split(os.sep):
                return True
        return False

    def process_file(self, file_path):
        """Stages a local file for operator review."""
        if self._is_ignored(file_path):
            return

        filename = os.path.basename(file_path)
        if filename.startswith(".") or filename in ["USER.md", "RULES.md", "MEMORY_SUMMARY.md", "audit.log", "librarian.log"]:
            return

        logger.info("Librarian processing: %s", filename)
        
        # Record deterministic local intake without sending file content to a provider.
        start_time = __import__("time").time()
        receipt = create_receipt(
            ActionType.FILE_OP,
            "stage_file_for_review",
            {"filename": filename, "path": file_path},
            tier=CognitionTier.DETERMINISTIC
        )
        self.receipt_service.create(receipt)

        try:
            with open(file_path, "r", errors='ignore') as f:
                content = f.read(2000)

            duration = int((__import__("time").time() - start_time) * 1000)
            self.receipt_service.update(receipt.complete(
                {"status": "staged", "preview_len": len(content)},
                duration
            ))

        except Exception as e:
            duration = int((__import__("time").time() - start_time) * 1000)
            self.receipt_service.update(receipt.fail(str(e), duration))
            logger.warning("Error reading file %s: %s", filename, e)
            return

        self._update_memory_summary(filename, "Queued for operator review.", "Unsorted")
        dst_folder = os.path.join(self.data_dir, "Unsorted")
        self.action_handler.safe_move(file_path, dst_folder, "Staged for operator review without model classification.")

    def _update_memory_summary(self, filename, summary, category):
        summary_path = os.path.join(self.data_dir, "MEMORY_SUMMARY.md")
        try:
            with open(summary_path, "a") as f:
                f.write(f"\n- **{filename}** ([{category}]): {summary}")
        except Exception as exc:
            logger.warning("Failed to update memory summary for %s: %s", filename, exc)

    def check_queue(self):
        """Manual trigger to process queue (simplifies threading model for this script)."""
        while self.process_queue:
            path = self.process_queue.pop(0)
            # Verify it still exists and is not in ignored
            if os.path.exists(path):
                self.process_file(path)
