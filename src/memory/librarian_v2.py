"""
Librarian V2 - Intelligent File Clerk
-------------------------------------
High-concurrency, AI-driven file organization service.

Feature Set:
1. **Thread-Safe Ingestion**: Uses asyncio.Queue to decouple Watchdog threads from processing.
2. **AI Classification**: Inspects file content via Gemini to tag Intent (Financial, Technical, etc.).
3. **Safety Protocol**: Implements '24h Trash' rule - deletions are soft-moves to .trash with metadata.
"""

import os
import time
import shutil
import asyncio
import json
import logging
from datetime import datetime, timedelta
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from google import genai

# Configure Logging
logger = logging.getLogger("lancelot.librarian")

class TrashService:
    """Manages the 24h Recycle Bin."""
    def __init__(self, data_dir):
        self.trash_dir = os.path.join(data_dir, ".trash")
        if not os.path.exists(self.trash_dir):
            os.makedirs(self.trash_dir)

    def soft_delete(self, file_path, justification):
        """Moves file to trash with metadata."""
        filename = os.path.basename(file_path)
        timestamp = int(time.time())
        trash_name = f"{filename}_{timestamp}"
        dest_path = os.path.join(self.trash_dir, trash_name)
        
        try:
            shutil.move(file_path, dest_path)
            
            # Write metadata
            meta = {
                "original_path": file_path,
                "deleted_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat(),
                "reason": justification
            }
            with open(dest_path + ".metadata", "w") as f:
                json.dump(meta, f)
                
            logger.info(f"Soft deleted: {filename} -> {trash_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to soft delete {filename}: {e}")
            return False

    def cleanup(self):
        """Removes expired items."""
        now = datetime.utcnow()
        for f in os.listdir(self.trash_dir):
            if f.endswith(".metadata"):
                try:
                    meta_path = os.path.join(self.trash_dir, f)
                    with open(meta_path, "r") as mf:
                        meta = json.load(mf)
                    
                    expires = datetime.fromisoformat(meta["expires_at"])
                    if now > expires:
                        # Delete file and metadata
                        target_file = meta_path.replace(".metadata", "")
                        if os.path.exists(target_file):
                            os.remove(target_file)
                        os.remove(meta_path)
                        logger.info(f"Cleanup: Removed expired {target_file}")
                except Exception as e:
                    logger.error(f"Cleanup error for {f}: {e}")


class LibrarianHandler(FileSystemEventHandler):
    """Bridges Watchdog threads to Asyncio Queue."""
    def __init__(self, queue, loop):
        self.queue = queue
        self.loop = loop

    def on_created(self, event):
        if not event.is_directory:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, event.src_path)

class LibrarianV2:
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.queue = asyncio.Queue()
        self.trash_svc = TrashService(data_dir)
        self.observer = Observer()
        self.client = None
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        
        # Classification Categories
        self.categories = {
            "Financial": ["invoices", "receipts", "billing"],
            "Technical": ["logs", "code", "configs"],
            "Personal": ["photos", "letters"],
            "Data": ["csv", "json", "datasets"]
        }
        
        self._init_gemini()

    def _init_gemini(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            self.client = genai.Client(api_key=api_key)
            logger.info("Librarian AI: Online")
        else:
            logger.warning("Librarian AI: Offline (No Key)")

    def start(self):
        """Starts the filesystem watcher."""
        loop = asyncio.get_running_loop()
        handler = LibrarianHandler(self.queue, loop)
        self.observer.schedule(handler, self.data_dir, recursive=False)
        self.observer.start()
        logger.info(f"Librarian V2 watching: {self.data_dir}")
        
        # Start background worker
        asyncio.create_task(self._process_queue())
        asyncio.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        """Runs trash cleanup every hour."""
        while True:
            await asyncio.sleep(3600)
            self.trash_svc.cleanup()

    async def _process_queue(self):
        """Consumes files from the queue."""
        while True:
            file_path = await self.queue.get()
            try:
                # Debounce fast writes
                await asyncio.sleep(1)
                
                if os.path.exists(file_path):
                    await self._organize_file(file_path)
            except Exception as e:
                logger.error(f"Processing error: {e}")
            finally:
                self.queue.task_done()

    # System files that the Librarian must never move
    PROTECTED_FILES = {
        "USER.md", "onboarding_snapshot.json", "usage_stats.json",
        "vault.key", "receipts.db", "receipts.db-shm", "receipts.db-wal",
        "librarian.log",
    }

    async def _organize_file(self, file_path):
        filename = os.path.basename(file_path)

        # Ignore system files and protected files
        if filename.startswith(".") or filename.endswith(".tmp"):
            return
        if filename in self.PROTECTED_FILES:
            return

        logger.info(f"Analyzing: {filename}")
        
        category = "Unsorted"
        summary = "No analysis performed."
        
        # AI Classification
        if self.client:
            try:
                with open(file_path, "r", errors='ignore') as f:
                    content = f.read(1500)
                
                # Offload to thread to avoid blocking loop
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(None, self._query_gemini, content)
                
                parsed = self._parse_ai_decision(response)
                category = parsed.get("category", "Unsorted")
                summary = parsed.get("summary", "")
                
            except Exception as e:
                logger.warning(f"AI Check failed: {e}")

        # Move to Category Folder
        target_dir = os.path.join(self.data_dir, category)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
            
        dest_path = os.path.join(target_dir, filename)
        
        # Handle Collision
        if os.path.exists(dest_path):
            name, ext = os.path.splitext(filename)
            dest_path = os.path.join(target_dir, f"{name}_{int(time.time())}{ext}")

        try:
            shutil.move(file_path, dest_path)
            logger.info(f"Filed: {filename} -> {category}/")
            
            # Log to Memory
            self._log_filing(filename, category, summary)
            
        except Exception as e:
            logger.error(f"Refiling failed: {e}")

    def _query_gemini(self, content):
        """Sync wrapper for Gemini call."""
        prompt = (
            f"Classify this file content into one tag: [Financial, Technical, Personal, Data, Other]. "
            f"Also verify if it should be deleted (Trash). "
            f"Format: Tag: <Tag> | Action: <Keep/Delete> | Summary: <1 sent>\n\n"
            f"Content:\n{content}"
        )
        resp = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt
        )
        return resp.text

    def _parse_ai_decision(self, text):
        """Parses LLM output."""
        # Simple heuristic parsing
        lower = text.lower()
        category = "Other"
        
        if "financial" in lower: category = "Financial"
        elif "technical" in lower: category = "Technical"
        elif "personal" in lower: category = "Personal"
        elif "data" in lower: category = "Data"
        
        return {"category": category, "summary": text}

    def _log_filing(self, filename, category, summary):
        log_path = os.path.join(self.data_dir, "librarian.log")
        entry = f"[{datetime.utcnow().isoformat()}] Filed {filename} into {category}. Summary: {summary}\n"
        with open(log_path, "a") as f:
            f.write(entry)

    def stop(self):
        self.observer.stop()
        logger.info("Librarian V2 stopped.")
