"""Receipt database schema and migration helpers."""

from __future__ import annotations

import logging
import sqlite3

SCHEMA_VERSION = 3

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS receipts (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        action_type TEXT NOT NULL,
        action_name TEXT NOT NULL,
        inputs TEXT NOT NULL,
        outputs TEXT NOT NULL,
        status TEXT NOT NULL,
        duration_ms INTEGER,
        token_count INTEGER,
        tier INTEGER NOT NULL DEFAULT 0,
        parent_id TEXT,
        quest_id TEXT,
        error_message TEXT,
        metadata TEXT NOT NULL DEFAULT '{}',
        operator_id TEXT,
        session_id TEXT,
        integrity_prev_hash TEXT,
        integrity_hash TEXT,
        integrity_key_id TEXT,
        integrity_signature TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_receipts_timestamp ON receipts(timestamp);
    CREATE INDEX IF NOT EXISTS idx_receipts_action_type ON receipts(action_type);
    CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
    CREATE INDEX IF NOT EXISTS idx_receipts_quest_id ON receipts(quest_id);
    CREATE INDEX IF NOT EXISTS idx_receipts_parent_id ON receipts(parent_id);
    """


CREATE_STAGING_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS receipt_staging (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        action_type TEXT NOT NULL,
        action_name TEXT NOT NULL,
        inputs TEXT NOT NULL,
        outputs TEXT NOT NULL,
        status TEXT NOT NULL,
        duration_ms INTEGER,
        token_count INTEGER,
        tier INTEGER NOT NULL DEFAULT 0,
        parent_id TEXT,
        quest_id TEXT,
        error_message TEXT,
        metadata TEXT NOT NULL DEFAULT '{}',
        operator_id TEXT,
        session_id TEXT,
        integrity_prev_hash TEXT,
        integrity_hash TEXT,
        integrity_key_id TEXT,
        integrity_signature TEXT
    );
    """




def ensure_receipt_schema(conn: sqlite3.Connection, logger: logging.Logger | None = None) -> None:
    """Create and migrate receipt tables in-place without changing storage format."""
    conn.executescript(CREATE_TABLE_SQL)
    conn.executescript(CREATE_STAGING_TABLE_SQL)
    try:
        cursor = conn.execute("PRAGMA table_info(receipts)")
        columns = {row[1] for row in cursor.fetchall()}
        if "operator_id" not in columns:
            conn.execute("ALTER TABLE receipts ADD COLUMN operator_id TEXT")
        if "session_id" not in columns:
            conn.execute("ALTER TABLE receipts ADD COLUMN session_id TEXT")
        if "integrity_prev_hash" not in columns:
            conn.execute("ALTER TABLE receipts ADD COLUMN integrity_prev_hash TEXT")
        if "integrity_hash" not in columns:
            conn.execute("ALTER TABLE receipts ADD COLUMN integrity_hash TEXT")
        if "integrity_key_id" not in columns:
            conn.execute("ALTER TABLE receipts ADD COLUMN integrity_key_id TEXT")
        if "integrity_signature" not in columns:
            conn.execute("ALTER TABLE receipts ADD COLUMN integrity_signature TEXT")
    except Exception as exc:
        if logger:
            logger.debug(
                "Receipt schema migration check skipped while initializing database: %s",
                exc,
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_receipts_operator_id "
        "ON receipts(operator_id)"
    )
    cursor = conn.execute("PRAGMA table_info(receipt_staging)")
    staging_columns = {row[1] for row in cursor.fetchall()}
    if "operator_id" not in staging_columns:
        conn.execute("ALTER TABLE receipt_staging ADD COLUMN operator_id TEXT")
    if "session_id" not in staging_columns:
        conn.execute("ALTER TABLE receipt_staging ADD COLUMN session_id TEXT")
    if "integrity_prev_hash" not in staging_columns:
        conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_prev_hash TEXT")
    if "integrity_hash" not in staging_columns:
        conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_hash TEXT")
    if "integrity_key_id" not in staging_columns:
        conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_key_id TEXT")
    if "integrity_signature" not in staging_columns:
        conn.execute("ALTER TABLE receipt_staging ADD COLUMN integrity_signature TEXT")
