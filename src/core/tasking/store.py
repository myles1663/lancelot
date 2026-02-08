"""
Task Store — SQLite-backed persistence for TaskGraphs and TaskRuns.
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

from src.core.tasking.schema import RunStatus, TaskGraph, TaskRun, TaskStep

logger = logging.getLogger(__name__)


class TaskStore:
    """SQLite-backed persistence for TaskGraphs and TaskRuns."""

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS task_graphs (
        id TEXT PRIMARY KEY,
        goal TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        planner_version TEXT NOT NULL DEFAULT 'v1',
        steps TEXT NOT NULL DEFAULT '[]',
        session_id TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS task_runs (
        id TEXT PRIMARY KEY,
        task_graph_id TEXT NOT NULL,
        execution_token_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'QUEUED',
        current_step_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        receipts_index TEXT NOT NULL DEFAULT '[]',
        last_error TEXT,
        session_id TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (task_graph_id) REFERENCES task_graphs(id)
    );

    CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
    CREATE INDEX IF NOT EXISTS idx_task_runs_session ON task_runs(session_id);
    CREATE INDEX IF NOT EXISTS idx_task_graphs_session ON task_graphs(session_id);
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

    # --- TaskGraph CRUD ---

    def save_graph(self, graph: TaskGraph) -> str:
        """Persist a TaskGraph. Returns graph ID."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO task_graphs (id, goal, created_at, planner_version, steps, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                graph.id, graph.goal, graph.created_at,
                graph.planner_version,
                json.dumps([s.to_dict() for s in graph.steps]),
                graph.session_id,
            ))
        return graph.id

    def get_graph(self, graph_id: str) -> Optional[TaskGraph]:
        """Retrieve a TaskGraph by ID."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM task_graphs WHERE id = ?", (graph_id,))
        row = cursor.fetchone()
        return self._row_to_graph(row) if row else None

    def get_latest_graph_for_session(self, session_id: str) -> Optional[TaskGraph]:
        """Get the most recent TaskGraph for a session."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM task_graphs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        row = cursor.fetchone()
        return self._row_to_graph(row) if row else None

    # --- TaskRun CRUD ---

    def create_run(self, run: TaskRun) -> str:
        """Persist a new TaskRun. Returns run ID."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO task_runs (
                    id, task_graph_id, execution_token_id, status,
                    current_step_id, created_at, updated_at,
                    receipts_index, last_error, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run.id, run.task_graph_id, run.execution_token_id,
                run.status, run.current_step_id,
                run.created_at, run.updated_at,
                json.dumps(run.receipts_index),
                run.last_error, run.session_id,
            ))
        return run.id

    def get_run(self, run_id: str) -> Optional[TaskRun]:
        """Retrieve a TaskRun by ID."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        return self._row_to_run(row) if row else None

    def update_status(
        self,
        run_id: str,
        status: str,
        current_step: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update a TaskRun's status and optional fields."""
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction() as conn:
            conn.execute("""
                UPDATE task_runs SET status = ?, current_step_id = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ?
            """, (status, current_step, error, now, run_id))

    def add_receipt(self, run_id: str, receipt_id: str) -> None:
        """Add a receipt ID to a TaskRun's receipts_index."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT receipts_index FROM task_runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        if row:
            index = json.loads(row["receipts_index"])
            index.append(receipt_id)
            with self._transaction() as conn2:
                conn2.execute(
                    "UPDATE task_runs SET receipts_index = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(index), datetime.now(timezone.utc).isoformat(), run_id),
                )

    def get_active_run(self) -> Optional[TaskRun]:
        """Get the currently active (QUEUED or RUNNING) TaskRun."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM task_runs WHERE status IN (?, ?) ORDER BY created_at DESC LIMIT 1",
            (RunStatus.QUEUED.value, RunStatus.RUNNING.value),
        )
        row = cursor.fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, limit: int = 50, session_id: Optional[str] = None) -> List[TaskRun]:
        """List TaskRuns, optionally filtered by session."""
        query = "SELECT * FROM task_runs"
        params: list = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        conn = self._get_connection()
        cursor = conn.execute(query, params)
        return [self._row_to_run(row) for row in cursor.fetchall()]

    # --- Conversion helpers ---

    def _row_to_graph(self, row: sqlite3.Row) -> TaskGraph:
        steps_data = json.loads(row["steps"])
        steps = [TaskStep.from_dict(s) for s in steps_data]
        return TaskGraph(
            id=row["id"],
            goal=row["goal"],
            created_at=row["created_at"],
            planner_version=row["planner_version"],
            steps=steps,
            session_id=row["session_id"],
        )

    def _row_to_run(self, row: sqlite3.Row) -> TaskRun:
        return TaskRun(
            id=row["id"],
            task_graph_id=row["task_graph_id"],
            execution_token_id=row["execution_token_id"],
            status=row["status"],
            current_step_id=row["current_step_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            receipts_index=json.loads(row["receipts_index"]),
            last_error=row["last_error"],
            session_id=row["session_id"],
        )

    def close(self):
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
