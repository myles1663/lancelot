"""Receipt SQLite connection and row mapping helpers."""

from __future__ import annotations

import json
import sqlite3

try:
    from .receipts_models import Receipt
except ImportError:  # pragma: no cover - legacy top-level import path
    from receipts_models import Receipt


def open_receipt_connection(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def receipt_from_row(row: sqlite3.Row) -> Receipt:
    row_keys = row.keys() if hasattr(row, "keys") else []
    return Receipt(
        id=row["id"],
        timestamp=row["timestamp"],
        action_type=row["action_type"],
        action_name=row["action_name"],
        inputs=json.loads(row["inputs"]),
        outputs=json.loads(row["outputs"]),
        status=row["status"],
        duration_ms=row["duration_ms"],
        token_count=row["token_count"],
        tier=row["tier"],
        parent_id=row["parent_id"],
        quest_id=row["quest_id"],
        error_message=row["error_message"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        operator_id=row["operator_id"] if "operator_id" in row_keys else None,
        session_id=row["session_id"] if "session_id" in row_keys else None,
        integrity_prev_hash=(
            row["integrity_prev_hash"] if "integrity_prev_hash" in row_keys else None
        ),
        integrity_hash=row["integrity_hash"] if "integrity_hash" in row_keys else None,
        integrity_key_id=row["integrity_key_id"] if "integrity_key_id" in row_keys else None,
        integrity_signature=(
            row["integrity_signature"] if "integrity_signature" in row_keys else None
        ),
    )
