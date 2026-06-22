"""Local file intake watcher for Lancelot memory.

The librarian keeps runtime files out of the way, stages newly-created local
files for operator review, and provides a 24-hour trash path. It deliberately
does not call external models; local file organization should not create data
egress or provider-key requirements.
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
                
            logger.info("Soft deleted local data file: %s -> %s", filename, trash_name)
            return True
        except Exception as e:
            logger.error("Failed to soft delete local data file %s: %s", filename, e)
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
                        target_file = meta_path.replace(".metadata", "")
                        if os.path.exists(target_file):
                            os.remove(target_file)
                        os.remove(meta_path)
                        logger.info("Removed expired trash item: %s", target_file)
                except Exception as e:
                    logger.error("Trash cleanup failed for metadata file %s: %s", f, e)


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

    def start(self):
        """Starts the filesystem watcher."""
        loop = asyncio.get_running_loop()
        handler = LibrarianHandler(self.queue, loop)
        self.observer.schedule(handler, self.data_dir, recursive=False)
        self.observer.start()
        logger.info("Librarian watching: %s", self.data_dir)
        
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
                logger.error("Librarian processing failed for %s: %s", file_path, e)
            finally:
                self.queue.task_done()

    # System files that the Librarian must never move
    PROTECTED_FILES = {
        "USER.md", "onboarding_snapshot.json", "usage_stats.json",
        "vault.key", "auth_state.json", "auth_state.key",
        "receipts.db", "receipts.db-shm", "receipts.db-wal",
        "actioncards.db", "actioncards.db-shm", "actioncards.db-wal",
        "mcp_pending_requests.json",
        "librarian.log",
    }

    async def _organize_file(self, file_path):
        filename = os.path.basename(file_path)

        # Ignore system files and protected files
        if filename.startswith(".") or filename.endswith(".tmp"):
            return
        if filename in self.PROTECTED_FILES:
            return

        logger.info("Filing local data file for operator review: %s", filename)

        category = "Unsorted"
        summary = "Model classification disabled; queued for operator review."

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
            logger.info("Filed local data file: %s -> %s/", filename, category)

            self._log_filing(filename, category, summary)

        except Exception as e:
            logger.error("Failed to file local data file %s into %s: %s", filename, category, e)

    def _log_filing(self, filename, category, summary):
        log_path = os.path.join(self.data_dir, "librarian.log")
        entry = f"[{datetime.utcnow().isoformat()}] Filed {filename} into {category}. Summary: {summary}\n"
        with open(log_path, "a") as f:
            f.write(entry)

    def stop(self):
        self.observer.stop()
        logger.info("Librarian stopped.")
