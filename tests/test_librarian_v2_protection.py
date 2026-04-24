import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "memory"))


def test_librarian_protects_governance_runtime_state_files():
    from librarian_v2 import LibrarianV2

    protected = LibrarianV2.PROTECTED_FILES

    assert "mcp_pending_requests.json" in protected
    assert "actioncards.db" in protected
    assert "actioncards.db-shm" in protected
    assert "actioncards.db-wal" in protected
