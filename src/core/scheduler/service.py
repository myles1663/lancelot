"""
Scheduler Service — persistent job store and management (Prompt 12 / D2-D3).

Uses SQLite for job persistence and provides methods for job lifecycle.

Public API:
    SchedulerService(data_dir, config_dir)
    list_jobs()       → list[JobRecord]
    get_job(job_id)   → JobRecord | None
    run_now(job_id)   → JobRecord
    enable_job(job_id)  → None
    disable_job(job_id) → None
    last_scheduler_tick_at → str | None
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.scheduler.schema import (
    JobSpec,
    SchedulerConfig,
    SchedulerError,
    load_scheduler_config,
)

logger = logging.getLogger(__name__)

_DB_FILE = "scheduler.sqlite"


# ---------------------------------------------------------------------------
# Job Record
# ---------------------------------------------------------------------------

class JobRecord(BaseModel):
    """A persisted job record."""
    id: str
    name: str
    skill: str = ""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    enabled: bool = True
    trigger_type: str = "interval"
    trigger_value: str = ""
    requires_ready: bool = True
    requires_approvals: List[str] = Field(default_factory=list)
    timeout_s: int = 300
    description: str = ""
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None
    run_count: int = 0
    registered_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SchedulerService:
    """Manages scheduled jobs with SQLite persistence."""

    def __init__(self, data_dir: str = "data", config_dir: str = "config"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / _DB_FILE
        self._config_dir = config_dir
        self._last_tick: Optional[str] = None

        self._init_db()

    @property
    def last_scheduler_tick_at(self) -> Optional[str]:
        return self._last_tick

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create the jobs table if it doesn't exist."""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    skill TEXT DEFAULT '',
                    inputs TEXT DEFAULT '{}',
                    timezone TEXT DEFAULT 'UTC',
                    enabled INTEGER DEFAULT 1,
                    trigger_type TEXT DEFAULT 'interval',
                    trigger_value TEXT DEFAULT '',
                    requires_ready INTEGER DEFAULT 1,
                    requires_approvals TEXT DEFAULT '[]',
                    timeout_s INTEGER DEFAULT 300,
                    description TEXT DEFAULT '',
                    last_run_at TEXT,
                    last_run_status TEXT,
                    run_count INTEGER DEFAULT 0,
                    registered_at TEXT NOT NULL
                )
            """)
            # Migration: add inputs column if missing (for existing DBs)
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN inputs TEXT DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass  # Column already exists
            # Migration: add timezone column if missing (for existing DBs)
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN timezone TEXT DEFAULT 'UTC'")
            except sqlite3.OperationalError:
                pass  # Column already exists
            conn.commit()
        finally:
            conn.close()

    def register_from_config(self) -> int:
        """Load scheduler.yaml and register any new jobs.

        Returns number of newly registered jobs.
        """
        try:
            config = load_scheduler_config(self._config_dir)
        except SchedulerError as exc:
            logger.warning("Failed to load scheduler config: %s", exc)
            return 0

        count = 0
        for job_spec in config.jobs:
            if self.get_job(job_spec.id) is None:
                self._register_job(job_spec)
                count += 1
            else:
                logger.debug("Job '%s' already registered, skipping", job_spec.id)

        self._last_tick = datetime.now(timezone.utc).isoformat()
        logger.info("Registered %d new jobs from config", count)
        return count

    def _register_job(self, spec: JobSpec) -> None:
        """Insert a job from a JobSpec."""
        trigger_value = ""
        if spec.trigger.seconds is not None:
            trigger_value = str(spec.trigger.seconds)
        elif spec.trigger.expression is not None:
            trigger_value = spec.trigger.expression

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO jobs
                   (id, name, skill, inputs, timezone, enabled, trigger_type, trigger_value,
                    requires_ready, requires_approvals, timeout_s,
                    description, registered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    spec.id,
                    spec.name,
                    spec.skill,
                    json.dumps(spec.inputs),
                    spec.timezone,
                    1 if spec.enabled else 0,
                    spec.trigger.type.value,
                    trigger_value,
                    1 if spec.requires_ready else 0,
                    json.dumps(spec.requires_approvals),
                    spec.timeout_s,
                    spec.description,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_record(self, row: sqlite3.Row) -> JobRecord:
        """Convert a database row to a JobRecord."""
        approvals = json.loads(row["requires_approvals"]) if row["requires_approvals"] else []
        inputs = json.loads(row["inputs"]) if row["inputs"] else {}
        tz = "UTC"
        try:
            tz = row["timezone"] or "UTC"
        except (IndexError, KeyError):
            pass
        return JobRecord(
            id=row["id"],
            name=row["name"],
            skill=row["skill"] or "",
            inputs=inputs,
            timezone=tz,
            enabled=bool(row["enabled"]),
            trigger_type=row["trigger_type"],
            trigger_value=row["trigger_value"] or "",
            requires_ready=bool(row["requires_ready"]),
            requires_approvals=approvals,
            timeout_s=row["timeout_s"],
            description=row["description"] or "",
            last_run_at=row["last_run_at"],
            last_run_status=row["last_run_status"],
            run_count=row["run_count"],
            registered_at=row["registered_at"],
        )

    def list_jobs(self) -> List[JobRecord]:
        """List all registered jobs."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
            return [self._row_to_record(r) for r in rows]
        finally:
            conn.close()

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Get a single job by ID."""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_record(row)
        finally:
            conn.close()

    def enable_job(self, job_id: str) -> None:
        """Enable a job.

        Raises SchedulerError if not found.
        """
        if self.get_job(job_id) is None:
            raise SchedulerError(f"Job '{job_id}' not found")
        conn = self._get_conn()
        try:
            conn.execute("UPDATE jobs SET enabled = 1 WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()
        logger.info("job_enabled: %s", job_id)

    def disable_job(self, job_id: str) -> None:
        """Disable a job.

        Raises SchedulerError if not found.
        """
        if self.get_job(job_id) is None:
            raise SchedulerError(f"Job '{job_id}' not found")
        conn = self._get_conn()
        try:
            conn.execute("UPDATE jobs SET enabled = 0 WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()
        logger.info("job_disabled: %s", job_id)

    def run_now(self, job_id: str) -> JobRecord:
        """Mark a job as having been run (for manual triggers).

        Raises SchedulerError if not found.
        """
        job = self.get_job(job_id)
        if job is None:
            raise SchedulerError(f"Job '{job_id}' not found")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE jobs
                   SET last_run_at = ?, last_run_status = 'triggered',
                       run_count = run_count + 1
                   WHERE id = ?""",
                (now, job_id),
            )
            conn.commit()
        finally:
            conn.close()

        self._last_tick = now
        logger.info("job_triggered: %s", job_id)
        return self.get_job(job_id)

    def create_job(
        self,
        job_id: str,
        name: str,
        skill: str,
        trigger_type: str,
        trigger_value: str,
        inputs: Optional[Dict[str, Any]] = None,
        timezone_str: str = "UTC",
        description: str = "",
        enabled: bool = True,
    ) -> JobRecord:
        """Create a new scheduled job dynamically.

        Args:
            job_id: Unique lowercase slug (e.g., "daily_wakeup").
            name: Human-readable name.
            skill: Skill to execute (e.g., "telegram_send").
            trigger_type: "cron" or "interval".
            trigger_value: Cron expression or interval seconds.
            inputs: Dict of inputs to pass to the skill.
            timezone_str: IANA timezone for cron evaluation (e.g., "America/New_York").
            description: Optional description.
            enabled: Whether the job starts enabled.

        Returns:
            The created JobRecord.

        Raises:
            SchedulerError if job already exists or validation fails.
        """
        if self.get_job(job_id) is not None:
            raise SchedulerError(f"Job '{job_id}' already exists")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO jobs
                   (id, name, skill, inputs, timezone, enabled, trigger_type, trigger_value,
                    requires_ready, requires_approvals, timeout_s,
                    description, registered_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    name,
                    skill,
                    json.dumps(inputs or {}),
                    timezone_str,
                    1 if enabled else 0,
                    trigger_type,
                    trigger_value,
                    0,  # requires_ready=False for dynamic jobs
                    "[]",
                    300,
                    description,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("job_created: %s (skill=%s, trigger=%s %s)", job_id, skill, trigger_type, trigger_value)
        return self.get_job(job_id)

    def update_job_timezone(self, job_id: str, tz: str) -> None:
        """Update the timezone of a scheduled job.

        Raises SchedulerError if not found.
        """
        if self.get_job(job_id) is None:
            raise SchedulerError(f"Job '{job_id}' not found")
        conn = self._get_conn()
        try:
            conn.execute("UPDATE jobs SET timezone = ? WHERE id = ?", (tz, job_id))
            conn.commit()
        finally:
            conn.close()
        logger.info("job_timezone_updated: %s → %s", job_id, tz)

    def delete_job(self, job_id: str) -> None:
        """Delete a scheduled job.

        Raises SchedulerError if not found.
        """
        if self.get_job(job_id) is None:
            raise SchedulerError(f"Job '{job_id}' not found")
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()
        logger.info("job_deleted: %s", job_id)
