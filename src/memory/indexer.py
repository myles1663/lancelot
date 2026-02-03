import os
import time
import chromadb
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class LogHandler(FileSystemEventHandler):
    def __init__(self, collection):
        self.collection = collection

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.txt'):
            self.process_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('.txt'):
            self.process_file(event.src_path)

    def process_file(self, filepath):
        print(f"Indexing file: {filepath}")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Simple chunking by paragraphs for now
            chunks = [c.strip() for c in content.split('\n\n') if c.strip()]
            
            if not chunks:
                return

            ids = [f"{os.path.basename(filepath)}_{i}_{int(time.time())}" for i in range(len(chunks))]
            metadatas = [{"source": os.path.basename(filepath)} for _ in chunks]

            self.collection.add(
                documents=chunks,
                metadatas=metadatas,
                ids=ids
            )
            print(f"Indexed {len(chunks)} chunks from {filepath}")
        except Exception as e:
            print(f"Error processing {filepath}: {e}")

class MemoryIndexer:
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.logs_dir = os.path.join(data_dir, "logs")
        self.chroma_path = os.path.join(data_dir, "chroma_db")
        
        # Ensure directories exist
        os.makedirs(self.logs_dir, exist_ok=True)
        os.makedirs(self.chroma_path, exist_ok=True)

        # Initialize ChromaDB
        self.client = chromadb.PersistentClient(path=self.chroma_path)
        self.collection = self.client.get_or_create_collection(name="lancelot_memory")
        print(f"ChromaDB initialized at {self.chroma_path}")

    def start_watching(self):
        # Process existing files first
        print("Scanning for existing logs...")
        for filename in os.listdir(self.logs_dir):
            if filename.endswith(".txt"):
                self.process_file_init(os.path.join(self.logs_dir, filename))

        event_handler = LogHandler(self.collection)
        observer = Observer()
        observer.schedule(event_handler, self.logs_dir, recursive=False)
        observer.start()
        print(f"Watching directory: {self.logs_dir}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

    def process_file_init(self, filepath):
        # Re-use logic, could be refactored but keeping simple for now
        # We need a temporary handler or move method to class
        handler = LogHandler(self.collection)
        handler.process_file(filepath)

if __name__ == "__main__":
    indexer = MemoryIndexer()
    indexer.start_watching()
