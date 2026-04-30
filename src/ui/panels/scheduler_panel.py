"""
War Room — Scheduler Panel (E4).

Lists jobs with enable/disable, run-now, and last run status.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.core.scheduler.service import SchedulerService, JobRecord
from src.core.scheduler.schema import SchedulerError

logger = logging.getLogger(__name__)


class SchedulerPanel:
    """Scheduler panel data provider for the War Room."""

    def __init__(self, scheduler_service: SchedulerService):
        self._service = scheduler_service

    def list_jobs(self) -> List[Dict[str, Any]]:
        try:
            jobs = self._service.list_jobs()
            return [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "trigger_type": j.trigger_type,
                    "trigger_value": j.trigger_value,
                    "last_run_at": j.last_run_at,
                    "last_run_status": j.last_run_status,
                    "run_count": j.run_count,
                    "skill": j.skill,
                }
                for j in jobs
            ]
        except Exception as exc:
            logger.warning("Scheduler panel error: %s", exc)
            return []

    def enable_job(self, job_id: str) -> Dict[str, Any]:
        try:
            self._service.enable_job(job_id)
            return {"status": "enabled", "job_id": job_id}
        except SchedulerError as exc:
            return {"error": str(exc)}

    def disable_job(self, job_id: str) -> Dict[str, Any]:
        try:
            self._service.disable_job(job_id)
            return {"status": "disabled", "job_id": job_id}
        except SchedulerError as exc:
            return {"error": str(exc)}

    def run_now(self, job_id: str) -> Dict[str, Any]:
        try:
            job = self._service.run_now(job_id)
            return {
                "status": "triggered",
                "job_id": job_id,
                "run_count": job.run_count,
            }
        except SchedulerError as exc:
            return {"error": str(exc)}

    def render_data(self) -> Dict[str, Any]:
        return {
            "panel": "scheduler",
            "jobs": self.list_jobs(),
            "last_tick": self._service.last_scheduler_tick_at,
        }
