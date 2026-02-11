"""Tests for vNext4 RollbackManager (Prompt 13)."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.rollback import RollbackManager, RollbackSnapshot


# ── Snapshot Creation Tests ──────────────────────────────────────

def test_create_snapshot_returns_snapshot():
    mgr = RollbackManager()
    snap = mgr.create_snapshot("task1", 0, "fs.write")
    assert isinstance(snap, RollbackSnapshot)
    assert snap.capability == "fs.write"
    assert snap.task_id == "task1"
    assert snap.step_index == 0


def test_snapshot_has_unique_id():
    mgr = RollbackManager()
    s1 = mgr.create_snapshot("task1", 0, "fs.write")
    s2 = mgr.create_snapshot("task1", 1, "fs.write")
    assert s1.snapshot_id != s2.snapshot_id


def test_snapshot_not_rolled_back_initially():
    mgr = RollbackManager()
    snap = mgr.create_snapshot("task1", 0, "fs.write")
    assert snap.rolled_back is False
    assert snap.rolled_back_at is None


def test_snapshot_has_created_at():
    mgr = RollbackManager()
    snap = mgr.create_snapshot("task1", 0, "fs.write")
    assert snap.created_at is not None
    assert "T" in snap.created_at  # ISO format


# ── File Snapshot Tests ──────────────────────────────────────────

def test_fs_write_snapshot_existing_file(tmp_path):
    test_file = tmp_path / "hello.txt"
    test_file.write_text("original content")

    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("task1", 0, "fs.write", target="hello.txt")
    assert snap.snapshot_data["file_existed"] is True
    assert snap.snapshot_data["content"] == "original content"


def test_fs_write_snapshot_nonexistent_file(tmp_path):
    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("task1", 0, "fs.write", target="missing.txt")
    assert snap.snapshot_data["file_existed"] is False


def test_fs_write_rollback_restores_file(tmp_path):
    test_file = tmp_path / "hello.txt"
    test_file.write_text("original content")

    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("task1", 0, "fs.write", target="hello.txt")

    # Simulate the action overwriting the file
    test_file.write_text("modified content")
    assert test_file.read_text() == "modified content"

    # Rollback should restore original
    rollback = mgr.get_rollback_action(snap.snapshot_id)
    rollback()
    assert test_file.read_text() == "original content"


def test_fs_write_rollback_removes_new_file(tmp_path):
    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("task1", 0, "fs.write", target="new_file.txt")
    assert snap.snapshot_data["file_existed"] is False

    # Simulate the action creating the file
    new_file = tmp_path / "new_file.txt"
    new_file.write_text("new content")
    assert new_file.exists()

    # Rollback should remove the new file
    rollback = mgr.get_rollback_action(snap.snapshot_id)
    rollback()
    assert not new_file.exists()


def test_rollback_marks_as_rolled_back(tmp_path):
    test_file = tmp_path / "hello.txt"
    test_file.write_text("original")

    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("task1", 0, "fs.write", target="hello.txt")
    test_file.write_text("changed")

    rollback = mgr.get_rollback_action(snap.snapshot_id)
    rollback()

    assert snap.rolled_back is True
    assert snap.rolled_back_at is not None


def test_double_rollback_is_noop(tmp_path):
    test_file = tmp_path / "hello.txt"
    test_file.write_text("original")

    mgr = RollbackManager(workspace=str(tmp_path))
    snap = mgr.create_snapshot("task1", 0, "fs.write", target="hello.txt")
    test_file.write_text("changed")

    rollback = mgr.get_rollback_action(snap.snapshot_id)
    rollback()
    assert test_file.read_text() == "original"

    # Modify again
    test_file.write_text("second change")
    # Second rollback should be a no-op (already rolled back)
    rollback()
    assert test_file.read_text() == "second change"


# ── Git/Memory Snapshot Tests ────────────────────────────────────

def test_git_commit_snapshot():
    mgr = RollbackManager()
    snap = mgr.create_snapshot("task1", 0, "git.commit", commit_sha="abc123")
    assert "git revert" in snap.snapshot_data["note"]
    assert snap.snapshot_data["commit_sha"] == "abc123"


def test_memory_write_snapshot():
    mgr = RollbackManager()
    snap = mgr.create_snapshot("task1", 0, "memory.write", key="test_key")
    assert "CommitManager" in snap.snapshot_data["note"]
    assert snap.snapshot_data["key"] == "test_key"


def test_unknown_capability_stores_kwargs():
    mgr = RollbackManager()
    snap = mgr.create_snapshot("task1", 0, "custom.action", foo="bar", baz=42)
    assert snap.snapshot_data["foo"] == "bar"
    assert snap.snapshot_data["baz"] == 42


# ── Manager State Tests ──────────────────────────────────────────

def test_get_snapshot():
    mgr = RollbackManager()
    snap = mgr.create_snapshot("task1", 0, "fs.write")
    retrieved = mgr.get_snapshot(snap.snapshot_id)
    assert retrieved is snap


def test_get_snapshot_nonexistent():
    mgr = RollbackManager()
    assert mgr.get_snapshot("nonexistent") is None


def test_active_snapshots(tmp_path):
    test_file = tmp_path / "f.txt"
    test_file.write_text("data")

    mgr = RollbackManager(workspace=str(tmp_path))
    s1 = mgr.create_snapshot("task1", 0, "fs.write", target="f.txt")
    s2 = mgr.create_snapshot("task1", 1, "fs.write", target="f.txt")
    assert len(mgr.active_snapshots) == 2

    test_file.write_text("changed")
    rollback = mgr.get_rollback_action(s1.snapshot_id)
    rollback()
    assert len(mgr.active_snapshots) == 1
    assert mgr.active_snapshots[0].snapshot_id == s2.snapshot_id


def test_rollback_nonexistent_snapshot_noop():
    mgr = RollbackManager()
    rollback = mgr.get_rollback_action("nonexistent")
    rollback()  # Should not raise
