# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Federation Peer Registry — SQLite-backed persistent peer storage.

Provides ACID-compliant peer record persistence that survives container
restarts. Also stores nonces for replay protection with automatic pruning.

Tables:
    peers   — registered federation peers with identity and health data
    nonces  — seen request nonces for replay protection
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Schema version for future migrations
SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS peers (
    instance_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL DEFAULT '',
    public_key_hex TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'peer',
    soul_version_hash TEXT DEFAULT '',
    last_heartbeat_at TEXT,
    registered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS nonces (
    nonce TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nonces_received ON nonces(received_at);
CREATE INDEX IF NOT EXISTS idx_peers_role ON peers(role);
"""


class PeerRegistryStore:
    """SQLite-backed persistent storage for federation peers and nonces.

    Thread-safe via a threading lock wrapping all DB operations.
    Uses WAL mode for concurrent read access.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()

        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Initialize schema
        self._init_db()
        logger.info("Peer registry store initialized: %s", db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Create a new connection (SQLite connections are not thread-safe)."""
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript(_SCHEMA_SQL)

                # Check/set schema version
                row = conn.execute(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                ).fetchone()
                if not row:
                    conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (SCHEMA_VERSION,),
                    )
                conn.commit()
            finally:
                conn.close()

    # ── Peer CRUD ─────────────────────────────────────────────

    def save_peer(
        self,
        instance_id: str,
        fingerprint: str = "",
        public_key_hex: str = "",
        address: str = "",
        role: str = "peer",
        soul_version_hash: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert or update a peer record."""
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {})

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO peers (
                        instance_id, fingerprint, public_key_hex, address,
                        role, soul_version_hash, registered_at, updated_at,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(instance_id) DO UPDATE SET
                        fingerprint = excluded.fingerprint,
                        public_key_hex = excluded.public_key_hex,
                        address = excluded.address,
                        role = excluded.role,
                        soul_version_hash = excluded.soul_version_hash,
                        updated_at = excluded.updated_at,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        instance_id, fingerprint, public_key_hex, address,
                        role, soul_version_hash, now, now, meta_json,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def remove_peer(self, instance_id: str) -> bool:
        """Remove a peer. Returns True if found and removed."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM peers WHERE instance_id = ?", (instance_id,)
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def get_peer(self, instance_id: str) -> Optional[Dict[str, Any]]:
        """Get a single peer record as a dict."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM peers WHERE instance_id = ?", (instance_id,)
                ).fetchone()
                if not row:
                    return None
                return self._row_to_dict(row)
            finally:
                conn.close()

    def list_peers(self) -> List[Dict[str, Any]]:
        """Get all peer records."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM peers ORDER BY registered_at"
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def update_heartbeat(
        self, instance_id: str, timestamp: str, soul_version_hash: str = "",
    ) -> bool:
        """Update a peer's last heartbeat. Returns False if unknown peer."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                if soul_version_hash:
                    cursor = conn.execute(
                        """UPDATE peers SET last_heartbeat_at = ?, soul_version_hash = ?,
                           updated_at = ? WHERE instance_id = ?""",
                        (timestamp, soul_version_hash, now, instance_id),
                    )
                else:
                    cursor = conn.execute(
                        "UPDATE peers SET last_heartbeat_at = ?, updated_at = ? WHERE instance_id = ?",
                        (timestamp, now, instance_id),
                    )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def get_peer_public_key(self, instance_id: str) -> Optional[bytes]:
        """Get a peer's public key bytes. Returns None if not found."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT public_key_hex FROM peers WHERE instance_id = ?",
                    (instance_id,),
                ).fetchone()
                if not row or not row["public_key_hex"]:
                    return None
                return bytes.fromhex(row["public_key_hex"])
            finally:
                conn.close()

    def peer_count(self) -> int:
        """Return the number of registered peers."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT COUNT(*) as cnt FROM peers").fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    # ── Nonce Replay Protection ───────────────────────────────

    def check_and_store_nonce(self, nonce: str) -> bool:
        """Check if a nonce is new and store it.

        Returns True if the nonce is fresh (not seen before).
        Returns False if it has already been recorded (replay).
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                existing = conn.execute(
                    "SELECT nonce FROM nonces WHERE nonce = ?", (nonce,)
                ).fetchone()
                if existing:
                    return False
                conn.execute(
                    "INSERT INTO nonces (nonce, received_at) VALUES (?, ?)",
                    (nonce, now),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def prune_old_nonces(self, max_age_s: float = 120.0) -> int:
        """Remove nonces older than max_age_s. Returns number pruned."""
        cutoff = datetime.now(timezone.utc)
        # Compute cutoff time
        from datetime import timedelta
        cutoff_iso = (cutoff - timedelta(seconds=max_age_s)).isoformat()

        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "DELETE FROM nonces WHERE received_at < ?", (cutoff_iso,)
                )
                conn.commit()
                pruned = cursor.rowcount
                if pruned > 0:
                    logger.debug("Pruned %d old nonces", pruned)
                return pruned
            finally:
                conn.close()

    def nonce_count(self) -> int:
        """Return the number of stored nonces."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT COUNT(*) as cnt FROM nonces").fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a sqlite3.Row to a plain dict."""
        d = dict(row)
        # Parse metadata JSON
        if "metadata_json" in d:
            try:
                d["metadata"] = json.loads(d.pop("metadata_json"))
            except (json.JSONDecodeError, TypeError):
                d["metadata"] = {}
        return d
