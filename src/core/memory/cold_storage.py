"""Cold-storage writer for evicted memory items."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import MEMORY_DIR
from .schemas import MemoryItem, MemoryTier


class ColdStorageWriter:
    """Append evicted memory items to JSONL files for audit recovery."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.storage_dir = self.data_dir / MEMORY_DIR / "cold_storage"

    def write_items(
        self,
        *,
        tier: MemoryTier,
        items: Iterable[MemoryItem],
        reason: str,
    ) -> Path | None:
        """Write evicted items to a daily JSONL file and return its path."""
        batch = list(items)
        if not batch:
            return None

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.utcnow()
        path = self.storage_dir / f"{tier.value}-{now.date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for item in batch:
                handle.write(json.dumps({
                    "evicted_at": now.isoformat(),
                    "reason": reason,
                    "item": item.model_dump(mode="json"),
                }, sort_keys=True))
                handle.write("\n")
        return path
