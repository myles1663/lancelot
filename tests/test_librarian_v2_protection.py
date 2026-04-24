import os
import sys
import asyncio

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "memory"))


def test_librarian_protects_governance_runtime_state_files():
    from librarian_v2 import LibrarianV2

    protected = LibrarianV2.PROTECTED_FILES

    assert "mcp_pending_requests.json" in protected
    assert "actioncards.db" in protected
    assert "actioncards.db-shm" in protected
    assert "actioncards.db-wal" in protected


class _Observer:
    def __init__(self):
        self.scheduled = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


@pytest.mark.asyncio
async def test_librarian_stop_cancels_background_tasks(monkeypatch, tmp_path):
    import librarian_v2

    monkeypatch.setattr(librarian_v2, "Observer", _Observer)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    librarian = librarian_v2.LibrarianV2(data_dir=str(tmp_path))
    librarian.start()

    assert librarian._running is True
    assert len(librarian._tasks) == 2
    assert librarian.observer.started is True

    librarian.stop()
    await asyncio.sleep(0)

    assert librarian._running is False
    assert librarian.observer.stopped is True
    assert librarian.observer.joined is True
    assert all(task.done() for task in librarian._tasks)


def test_librarian_stop_before_start_is_safe(monkeypatch, tmp_path):
    import librarian_v2

    monkeypatch.setattr(librarian_v2, "Observer", _Observer)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    librarian = librarian_v2.LibrarianV2(data_dir=str(tmp_path))
    librarian.stop()

    assert librarian._running is False
    assert librarian.observer.stopped is False
    assert librarian.observer.joined is False
