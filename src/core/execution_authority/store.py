"""
ExecutionToken Store — SQLite-backed persistence for scoped authority tokens.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from src.core.execution_authority.schema import ExecutionToken, TokenStatus

logger = logging.getLogger(__name__)


class ExecutionTokenStore:
    """SQLite-backed persistence for ExecutionTokens."""

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS execution_tokens (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT '',
        task_type TEXT NOT NULL DEFAULT 'OTHER',
        allowed_tools TEXT NOT NULL DEFAULT '[]',
        allowed_skills TEXT NOT NULL DEFAULT '[]',
        allowed_paths TEXT NOT NULL DEFAULT '[]',
        network_policy TEXT NOT NULL DEFAULT 'OFF',
        network_allowlist TEXT NOT NULL DEFAULT '[]',
        secret_policy TEXT NOT NULL DEFAULT 'NO_SECRETS',
        max_duration_sec INTEGER NOT NULL DEFAULT 300,
        max_actions INTEGER NOT NULL DEFAULT 50,
        risk_tier TEXT NOT NULL DEFAULT 'LOW',
        requires_verifier INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        parent_receipt_id TEXT,
        actions_used INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT,
        session_id TEXT NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_tokens_status ON execution_tokens(status);
    CREATE INDEX IF NOT EXISTS idx_tokens_session ON execution_tokens(session_id);
    CREATE INDEX IF NOT EXISTS idx_tokens_expires ON execution_tokens(expires_at);
    """

    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._local = threading.local()
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=30.0
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
        return self._local.connection

    @contextmanager
    def _transaction(self):
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_database(self):
        with self._transaction() as conn:
            conn.executescript(self.CREATE_TABLE_SQL)

    def create(self, token: ExecutionToken) -> str:
        """Persist a new ExecutionToken. Returns token ID."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO execution_tokens (
                    id, created_at, created_by, scope, task_type,
                    allowed_tools, allowed_skills, allowed_paths,
                    network_policy, network_allowlist, secret_policy,
                    max_duration_sec, max_actions, risk_tier,
                    requires_verifier, status, parent_receipt_id,
                    actions_used, expires_at, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token.id, token.created_at, token.created_by,
                token.scope, token.task_type,
                json.dumps(token.allowed_tools),
                json.dumps(token.allowed_skills),
                json.dumps(token.allowed_paths),
                token.network_policy,
                json.dumps(token.network_allowlist),
                token.secret_policy,
                token.max_duration_sec, token.max_actions,
                token.risk_tier, int(token.requires_verifier),
                token.status, token.parent_receipt_id,
                token.actions_used, token.expires_at, token.session_id,
            ))
        return token.id

    def get(self, token_id: str) -> Optional[ExecutionToken]:
        """Retrieve a token by ID."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM execution_tokens WHERE id = ?", (token_id,))
        row = cursor.fetchone()
        return self._row_to_token(row) if row else None

    def get_active_for_session(self, session_id: str) -> List[ExecutionToken]:
        """Get all active tokens for a session."""
        self.expire_stale()
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM execution_tokens WHERE session_id = ? AND status = ? ORDER BY created_at DESC",
            (session_id, TokenStatus.ACTIVE.value),
        )
        return [self._row_to_token(row) for row in cursor.fetchall()]

    def revoke(self, token_id: str, reason: str = "") -> bool:
        """Revoke an active token. Returns True if revoked."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE execution_tokens SET status = ? WHERE id = ? AND status = ?",
                (TokenStatus.REVOKED.value, token_id, TokenStatus.ACTIVE.value),
            )
            if cursor.rowcount > 0:
                logger.info("Token %s revoked: %s", token_id, reason)
                return True
            return False

    def expire_stale(self) -> int:
        """Auto-expire tokens past their max_duration or max_actions."""
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction() as conn:
            cursor = conn.execute(
                """UPDATE execution_tokens SET status = ?
                   WHERE status = ? AND (
                       (expires_at IS NOT NULL AND expires_at < ?) OR
                       (actions_used >= max_actions)
                   )""",
                (TokenStatus.EXPIRED.value, TokenStatus.ACTIVE.value, now),
            )
            count = cursor.rowcount
            if count > 0:
                logger.info("Expired %d stale tokens", count)
            return count

    def increment_actions(self, token_id: str) -> bool:
        """Increment actions_used. Returns False if max_actions exceeded."""
        with self._transaction() as conn:
            cursor = conn.execute(
                """UPDATE execution_tokens SET actions_used = actions_used + 1
                   WHERE id = ? AND status = ? AND actions_used < max_actions""",
                (token_id, TokenStatus.ACTIVE.value),
            )
            return cursor.rowcount > 0

    def list_tokens(self, limit: int = 50, status: Optional[str] = None) -> List[ExecutionToken]:
        """List tokens with optional status filter."""
        query = "SELECT * FROM execution_tokens"
        params: list = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        conn = self._get_connection()
        cursor = conn.execute(query, params)
        return [self._row_to_token(row) for row in cursor.fetchall()]

    def _row_to_token(self, row: sqlite3.Row) -> ExecutionToken:
        return ExecutionToken(
            id=row["id"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            scope=row["scope"],
            task_type=row["task_type"],
            allowed_tools=json.loads(row["allowed_tools"]),
            allowed_skills=json.loads(row["allowed_skills"]),
            allowed_paths=json.loads(row["allowed_paths"]),
            network_policy=row["network_policy"],
            network_allowlist=json.loads(row["network_allowlist"]),
            secret_policy=row["secret_policy"],
            max_duration_sec=row["max_duration_sec"],
            max_actions=row["max_actions"],
            risk_tier=row["risk_tier"],
            requires_verifier=bool(row["requires_verifier"]),
            status=row["status"],
            parent_receipt_id=row["parent_receipt_id"],
            actions_used=row["actions_used"],
            expires_at=row["expires_at"],
            session_id=row["session_id"],
        )

    def close(self):
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
