import os
import sys
import importlib


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "memory"))


def test_librarian_protects_governance_runtime_state_files():
    librarian_cls = getattr(importlib.import_module("librarian_v2"), "Librarian" + "V" + "2")

    protected = librarian_cls.PROTECTED_FILES

    assert "mcp_pending_requests.json" in protected
    assert "actioncards.db" in protected
    assert "actioncards.db-shm" in protected
    assert "actioncards.db-wal" in protected
