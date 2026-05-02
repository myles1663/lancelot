import os
import shutil
from types import SimpleNamespace

import pytest

from src.memory import librarian as librarian_module


class ReceiptService:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, receipt):
        self.created.append(receipt)

    def update(self, receipt):
        self.updated.append(receipt)


def test_file_action_move_copy_mkdir_touch_and_write_record_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(librarian_module.time, "time", lambda: 123456)
    service = ReceiptService()
    action = librarian_module.FileAction(
        log_path=str(tmp_path / "librarian.log"),
        receipt_service=service,
    )

    source = tmp_path / "incoming.txt"
    source.write_text("hello", encoding="utf-8")
    dest_dir = tmp_path / "dest"

    moved = action.safe_move(str(source), str(dest_dir), "stage file")
    assert moved == str(dest_dir / "incoming.txt")
    assert not source.exists()
    assert (dest_dir / "incoming.txt").read_text(encoding="utf-8") == "hello"

    copy_source = tmp_path / "copy.txt"
    copy_source.write_text("copy", encoding="utf-8")
    copy_dest = tmp_path / "copies"
    (copy_dest).mkdir()
    (copy_dest / "copy.txt").write_text("existing", encoding="utf-8")
    copied = action.safe_copy(str(copy_source), str(copy_dest), "copy file")
    assert copied == str(copy_dest / "copy_123456_copy.txt")
    assert (copy_dest / "copy_123456_copy.txt").read_text(encoding="utf-8") == "copy"

    assert action.safe_mkdir(str(tmp_path / "nested" / "dir"), "make directory") is True
    touched = tmp_path / "touch" / "file.txt"
    assert action.touch(str(touched), "touch file") is True
    assert touched.exists()
    written = tmp_path / "write" / "file.txt"
    assert action.write_file(str(written), "content", "write file") is True
    assert written.read_text(encoding="utf-8") == "content"

    assert len(service.created) == 5
    assert len(service.updated) == 5
    assert "Action: WRITE" in (tmp_path / "librarian.log").read_text(encoding="utf-8")


def test_file_action_collision_move_and_failure_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(librarian_module.time, "time", lambda: 222)
    service = ReceiptService()
    action = librarian_module.FileAction(
        log_path=str(tmp_path / "librarian.log"),
        receipt_service=service,
    )

    src = tmp_path / "file.txt"
    src.write_text("new", encoding="utf-8")
    dst_dir = tmp_path / "dest"
    dst_dir.mkdir()
    (dst_dir / "file.txt").write_text("existing", encoding="utf-8")

    moved = action.safe_move(str(src), str(dst_dir), "collision")
    assert moved == str(dst_dir / "file_222.txt")
    assert (dst_dir / "file_222.txt").read_text(encoding="utf-8") == "new"

    monkeypatch.setattr(librarian_module.shutil, "move", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("move failed")))
    assert action.safe_move(str(tmp_path / "missing.txt"), str(dst_dir), "fail") is None

    monkeypatch.setattr(librarian_module.shutil, "copy2", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("copy failed")))
    assert action.safe_copy(str(dst_dir / "file.txt"), str(dst_dir), "fail") is None

    monkeypatch.setattr(librarian_module.os, "makedirs", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("mkdir failed")))
    assert action.safe_mkdir(str(tmp_path / "cannot"), "fail") is False
    assert action.touch(str(tmp_path / "cannot" / "file.txt"), "fail") is False
    assert action.write_file(str(tmp_path / "cannot" / "file.txt"), "content", "fail") is False
    assert len(service.updated) == len(service.created)


def test_safe_delete_wraps_trash_move_with_parent_receipt(tmp_path):
    service = ReceiptService()
    action = librarian_module.FileAction(receipt_service=service)
    calls = []
    action.safe_move = lambda src, dst, justification: calls.append((src, dst, justification)) or "/trash/file.txt"

    result = action.safe_delete(str(tmp_path / "file.txt"), "remove stale file")

    assert result == "/trash/file.txt"
    assert calls == [
        (
            str(tmp_path / "file.txt"),
            "/home/lancelot/data/.trash",
            "DELETE (Recycle Bin): remove stale file",
        )
    ]
    assert len(service.created) == 1
    assert len(service.updated) == 1


def test_safe_delete_records_failure_when_trash_move_fails(tmp_path):
    service = ReceiptService()
    action = librarian_module.FileAction(receipt_service=service)
    action.safe_move = lambda *args, **kwargs: None

    assert action.safe_delete(str(tmp_path / "file.txt"), "remove stale file") is None
    assert len(service.created) == 1
    assert len(service.updated) == 1


def test_librarian_handler_queues_only_files():
    librarian = SimpleNamespace(process_queue=[])
    handler = librarian_module.LibrarianHandler(librarian)

    handler.on_created(SimpleNamespace(is_directory=True, src_path="dir"))
    handler.on_created(SimpleNamespace(is_directory=False, src_path="file.txt"))

    assert librarian.process_queue == ["file.txt"]


def test_librarian_process_file_stages_file_and_ignores_runtime_state(tmp_path, monkeypatch):
    service = ReceiptService()
    monkeypatch.setattr(librarian_module, "get_receipt_service", lambda data_dir: service)
    lib = librarian_module.Librarian(data_dir=str(tmp_path))
    lib.action_handler = librarian_module.FileAction(
        log_path=str(tmp_path / "librarian.log"),
        receipt_service=service,
    )

    ignored = tmp_path / ".trash" / "ignored.txt"
    ignored.parent.mkdir()
    ignored.write_text("ignored", encoding="utf-8")
    lib.process_file(str(ignored))
    assert ignored.exists()

    protected = tmp_path / "USER.md"
    protected.write_text("identity", encoding="utf-8")
    lib.process_file(str(protected))
    assert protected.exists()

    note = tmp_path / "note.txt"
    note.write_text("operator memory" * 200, encoding="utf-8")
    lib.process_file(str(note))

    staged = tmp_path / "Unsorted" / "note.txt"
    assert staged.exists()
    assert "Queued for operator review" in (tmp_path / "MEMORY_SUMMARY.md").read_text(encoding="utf-8")
    assert any(getattr(receipt, "action_name", "") == "stage_file_for_review" for receipt in service.created)
    assert any(getattr(receipt, "action_name", "") == "move_file" for receipt in service.created)


def test_librarian_process_file_records_read_failure(tmp_path, monkeypatch, caplog):
    service = ReceiptService()
    monkeypatch.setattr(librarian_module, "get_receipt_service", lambda data_dir: service)
    lib = librarian_module.Librarian(data_dir=str(tmp_path))
    source = tmp_path / "broken.txt"
    source.write_text("broken", encoding="utf-8")

    def broken_open(path, *args, **kwargs):
        if str(path).endswith("broken.txt"):
            raise OSError("read failed")
        return open(path, *args, **kwargs)

    monkeypatch.setattr(librarian_module, "open", broken_open, raising=False)

    lib.process_file(str(source))

    assert source.exists()
    assert "Error reading file broken.txt" in caplog.text
    assert len(service.created) == 1
    assert len(service.updated) == 1


def test_librarian_check_queue_processes_existing_files_only(tmp_path, monkeypatch):
    service = ReceiptService()
    monkeypatch.setattr(librarian_module, "get_receipt_service", lambda data_dir: service)
    lib = librarian_module.Librarian(data_dir=str(tmp_path))
    seen = []
    lib.process_file = lambda path: seen.append(path)
    existing = tmp_path / "exists.txt"
    existing.write_text("exists", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    lib.process_queue.extend([str(missing), str(existing)])

    lib.check_queue()

    assert seen == [str(existing)]
    assert lib.process_queue == []


def test_start_watching_schedules_non_recursive_observer(tmp_path, monkeypatch):
    service = ReceiptService()
    monkeypatch.setattr(librarian_module, "get_receipt_service", lambda data_dir: service)
    lib = librarian_module.Librarian(data_dir=str(tmp_path))
    scheduled = []
    lib.observer = SimpleNamespace(
        schedule=lambda handler, data_dir, recursive=False: scheduled.append((handler, data_dir, recursive)),
        start=lambda: scheduled.append("started"),
    )

    lib.start_watching()

    assert scheduled[0][1:] == (str(tmp_path), False)
    assert scheduled[1] == "started"
