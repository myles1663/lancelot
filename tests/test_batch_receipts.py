"""Tests for vNext4 batch receipt system (Prompts 6-7)."""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))

from governance.batch_receipts import (
    ReceiptEntry,
    BatchSummary,
    BatchReceipt,
    BatchReceiptBuffer,
)
from governance.config import BatchReceiptConfig
from governance.models import RiskTier


@pytest.fixture
def config():
    return BatchReceiptConfig(buffer_size=20)


@pytest.fixture
def buffer(tmp_path, config):
    return BatchReceiptBuffer(task_id="test_task", config=config, data_dir=str(tmp_path))


# ── Data Model Tests ─────────────────────────────────────────────

def test_receipt_entry_creation():
    entry = ReceiptEntry(
        entry_index=0, timestamp="2026-01-01T00:00:00Z",
        capability="fs.read", tool_id="local", risk_tier=0,
        input_hash="abc", output_hash="def", success=True,
    )
    assert entry.capability == "fs.read"
    assert entry.success is True


def test_batch_summary_creation():
    summary = BatchSummary(total_actions=5, succeeded=4, failed=1, highest_risk_tier=1, total_elapsed_ms=100.0)
    assert summary.total_actions == 5


def test_batch_receipt_to_dict():
    receipt = BatchReceipt(batch_id="test", task_id="task", created_at="2026-01-01T00:00:00Z")
    d = receipt.to_dict()
    assert isinstance(d, dict)
    assert d["batch_id"] == "test"
    assert "entries" in d
    assert "summary" in d


# ── Buffer Core Tests ────────────────────────────────────────────

def test_buffer_starts_empty(buffer):
    assert buffer.size == 0
    assert buffer.is_empty is True


def test_append_increases_size(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, {"path": "test"}, "content", True)
    assert buffer.size == 1


def test_append_hashes_are_64_hex(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, {"path": "test"}, "content", True)
    entry = buffer._entries[0]
    assert len(entry.input_hash) == 64
    assert len(entry.output_hash) == 64
    assert all(c in "0123456789abcdef" for c in entry.input_hash)


def test_flush_writes_json_file(tmp_path, config):
    buf = BatchReceiptBuffer(task_id="task1", config=config, data_dir=str(tmp_path))
    buf.append("fs.read", "local", RiskTier.T0_INERT, "in", "out", True)
    receipt = buf.flush()
    assert receipt is not None
    # Check file was written
    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["task_id"] == "task1"


def test_flush_returns_correct_summary(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, "in", "out", True)
    buffer.append("fs.write", "local", RiskTier.T1_REVERSIBLE, "in", "out", False, error="fail")
    receipt = buffer.flush()
    assert receipt.summary.total_actions == 2
    assert receipt.summary.succeeded == 1
    assert receipt.summary.failed == 1
    assert receipt.summary.highest_risk_tier == 1


def test_flush_resets_buffer(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, "in", "out", True)
    buffer.flush()
    assert buffer.size == 0
    assert buffer.is_empty is True


def test_flush_empty_returns_none(buffer):
    result = buffer.flush()
    assert result is None


def test_auto_flush(tmp_path):
    config = BatchReceiptConfig(buffer_size=3)
    buf = BatchReceiptBuffer(task_id="task", config=config, data_dir=str(tmp_path))
    buf.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    buf.append("fs.read", "local", RiskTier.T0_INERT, "c", "d", True)
    assert buf.size == 2
    buf.append("fs.read", "local", RiskTier.T0_INERT, "e", "f", True)
    # Auto-flush should have triggered
    assert buf.size == 0


def test_auto_flush_writes_file(tmp_path):
    config = BatchReceiptConfig(buffer_size=3)
    buf = BatchReceiptBuffer(task_id="task", config=config, data_dir=str(tmp_path))
    for i in range(3):
        buf.append("fs.read", "local", RiskTier.T0_INERT, f"in{i}", f"out{i}", True)
    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 1


def test_multiple_flushes_create_separate_files(tmp_path, config):
    buf = BatchReceiptBuffer(task_id="task", config=config, data_dir=str(tmp_path))
    buf.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    buf.flush()
    buf.append("fs.read", "local", RiskTier.T0_INERT, "c", "d", True)
    buf.flush()
    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 2


def test_highest_risk_tier_tracked(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    buffer.append("fs.write", "local", RiskTier.T1_REVERSIBLE, "c", "d", True)
    receipt = buffer.flush()
    assert receipt.summary.highest_risk_tier == 1


# ── Tier Boundary Flush Tests (Prompt 7) ─────────────────────────

def test_tier_boundary_t0_no_flush(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    result = buffer.flush_if_tier_boundary(RiskTier.T0_INERT)
    assert result is None
    assert buffer.size == 1


def test_tier_boundary_t1_no_flush(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    result = buffer.flush_if_tier_boundary(RiskTier.T1_REVERSIBLE)
    assert result is None
    assert buffer.size == 1


def test_tier_boundary_t2_flushes(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    result = buffer.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    assert result is not None
    assert buffer.size == 0


def test_tier_boundary_t3_flushes(buffer):
    buffer.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    result = buffer.flush_if_tier_boundary(RiskTier.T3_IRREVERSIBLE)
    assert result is not None
    assert buffer.size == 0


def test_tier_boundary_empty_no_flush(buffer):
    result = buffer.flush_if_tier_boundary(RiskTier.T3_IRREVERSIBLE)
    assert result is None


def test_tier_boundary_disabled(tmp_path):
    config = BatchReceiptConfig(flush_on_tier_boundary=False)
    buf = BatchReceiptBuffer(task_id="task", config=config, data_dir=str(tmp_path))
    buf.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    result = buf.flush_if_tier_boundary(RiskTier.T3_IRREVERSIBLE)
    assert result is None
    assert buf.size == 1


# ── Context Manager Tests ────────────────────────────────────────

def test_context_manager_flushes_on_exit(tmp_path, config):
    with BatchReceiptBuffer(task_id="task", config=config, data_dir=str(tmp_path)) as buf:
        buf.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 1


def test_context_manager_empty_no_file(tmp_path, config):
    with BatchReceiptBuffer(task_id="task", config=config, data_dir=str(tmp_path)) as buf:
        pass
    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 0


def test_append_then_tier_boundary_sequence(tmp_path, config):
    buf = BatchReceiptBuffer(task_id="task", config=config, data_dir=str(tmp_path))
    buf.append("fs.read", "local", RiskTier.T0_INERT, "a", "b", True)
    buf.append("fs.read", "local", RiskTier.T0_INERT, "c", "d", True)
    receipt = buf.flush_if_tier_boundary(RiskTier.T2_CONTROLLED)
    assert receipt is not None
    assert receipt.summary.total_actions == 2
    assert buf.size == 0
