"""
BAL Database — SQLite persistence for business automation data.

Thread-safe, WAL-mode database with schema versioning and migration support.
Stored in data/bal/bal.sqlite to avoid librarian interference.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DB_FILE = "bal.sqlite"

SCHEMA_V1 = """
-- BAL Schema Version 1

CREATE TABLE IF NOT EXISTS bal_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bal_clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'starter',
    status TEXT NOT NULL DEFAULT 'onboarding',
    preferences_json TEXT NOT NULL DEFAULT '{}',
    billing_json TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bal_intake (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'raw_text',
    title TEXT NOT NULL DEFAULT '',
    raw_content TEXT NOT NULL,
    analysis_json TEXT NOT NULL DEFAULT '{}',
    word_count INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT 'en',
    status TEXT NOT NULL DEFAULT 'received',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES bal_clients(id)
);

CREATE TABLE IF NOT EXISTS bal_content (
    id TEXT PRIMARY KEY,
    intake_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    content_body TEXT NOT NULL,
    verification_json TEXT NOT NULL DEFAULT '{}',
    quality_score REAL,
    status TEXT NOT NULL DEFAULT 'draft',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (intake_id) REFERENCES bal_intake(id),
    FOREIGN KEY (client_id) REFERENCES bal_clients(id)
);

CREATE TABLE IF NOT EXISTS bal_deliveries (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    delivered_at TEXT,
    error_message TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (content_id) REFERENCES bal_content(id),
    FOREIGN KEY (client_id) REFERENCES bal_clients(id)
);

CREATE TABLE IF NOT EXISTS bal_financial_receipts (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    event_type TEXT NOT NULL,
    stripe_id TEXT,
    stripe_event_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (client_id) REFERENCES bal_clients(id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_bal_clients_status ON bal_clients(status);
CREATE INDEX IF NOT EXISTS idx_bal_clients_email ON bal_clients(email);
CREATE INDEX IF NOT EXISTS idx_bal_intake_client ON bal_intake(client_id);
CREATE INDEX IF NOT EXISTS idx_bal_intake_status ON bal_intake(status);
CREATE INDEX IF NOT EXISTS idx_bal_content_client ON bal_content(client_id);
CREATE INDEX IF NOT EXISTS idx_bal_content_intake ON bal_content(intake_id);
CREATE INDEX IF NOT EXISTS idx_bal_content_status ON bal_content(status);
CREATE INDEX IF NOT EXISTS idx_bal_deliveries_client ON bal_deliveries(client_id);
CREATE INDEX IF NOT EXISTS idx_bal_deliveries_status ON bal_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_bal_financial_client ON bal_financial_receipts(client_id);
"""


SCHEMA_V2 = """
-- BAL Schema Version 2: Client Manager enhancements
ALTER TABLE bal_clients ADD COLUMN memory_block_id TEXT;

-- Enforce email uniqueness (replaces non-unique index from V1)
DROP INDEX IF EXISTS idx_bal_clients_email;
CREATE UNIQUE INDEX IF NOT EXISTS idx_bal_clients_email ON bal_clients(email);
"""


class BALDatabase:
    """Thread-safe SQLite database for BAL persistence."""

    CURRENT_SCHEMA_VERSION = 2

    def __init__(self, data_dir: str = "/home/lancelot/data/bal"):
        self._data_dir = data_dir
        self._db_path = os.path.join(data_dir, _DB_FILE)
        self._local = threading.local()

        # Ensure data directory exists
        os.makedirs(data_dir, exist_ok=True)

        # Initialize schema
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection with WAL mode."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30.0,
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
            self._local.connection.execute("PRAGMA foreign_keys=ON")
        return self._local.connection

    @contextmanager
    def transaction(self):
        """Context manager for database transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        """Initialize database schema with migration support."""
        current = self._get_schema_version()
        if current < self.CURRENT_SCHEMA_VERSION:
            self._apply_migrations(current)

    def _get_schema_version(self) -> int:
        """Read current schema version (0 if table does not exist)."""
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bal_schema_version'"
            )
            if cursor.fetchone() is None:
                return 0
            cursor = conn.execute("SELECT MAX(version) FROM bal_schema_version")
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0
        except sqlite3.Error:
            return 0

    def _apply_migrations(self, from_version: int) -> None:
        """Apply schema migrations from from_version to CURRENT_SCHEMA_VERSION."""
        migrations = {
            1: SCHEMA_V1,
            2: SCHEMA_V2,
        }

        conn = self._get_connection()
        for ver in range(from_version + 1, self.CURRENT_SCHEMA_VERSION + 1):
            sql = migrations.get(ver)
            if sql:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO bal_schema_version (version, applied_at) VALUES (?, ?)",
                    (ver, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                logger.info("BAL schema migrated to version %d", ver)

    @property
    def db_path(self) -> str:
        return self._db_path

    def close(self) -> None:
        """Close the thread-local database connection."""
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
