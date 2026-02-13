"""
Schedule Job — builtin skill for dynamic job management.

Allows creating, listing, and deleting scheduled jobs from chat.
Jobs are persisted in SQLite and evaluated by the cron tick loop.

Actions:
    create — Create a new scheduled job (cron or interval trigger)
    list   — List all registered jobs with status
    delete — Remove a scheduled job by ID
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

MANIFEST = {
    "name": "schedule_job",
    "version": "1.0.0",
    "description": "Create, list, and delete scheduled jobs dynamically",
    "risk": "LOW",
    "permissions": ["scheduler.write"],
}


def _slugify(name: str) -> str:
    """Convert a human-readable name to a valid job ID slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:50] or "job"


def execute(context: Any, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the schedule_job skill."""
    action = inputs.get("action", "").lower()

    if action not in ("create", "list", "delete"):
        return {
            "status": "error",
            "error": f"Unknown action '{action}'. Use 'create', 'list', or 'delete'.",
        }

    # Get the scheduler service from gateway
    try:
        import gateway
        svc = getattr(gateway, "scheduler_service", None)
        if svc is None:
            return {"status": "error", "error": "Scheduler service not available"}
    except ImportError:
        return {"status": "error", "error": "Cannot import gateway module"}

    if action == "list":
        return _handle_list(svc)
    elif action == "create":
        return _handle_create(svc, inputs)
    elif action == "delete":
        return _handle_delete(svc, inputs)


def _handle_list(service: Any) -> Dict[str, Any]:
    """List all scheduled jobs."""
    jobs = service.list_jobs()
    job_list = []
    for j in jobs:
        job_list.append({
            "id": j.id,
            "name": j.name,
            "skill": j.skill,
            "enabled": j.enabled,
            "trigger": f"{j.trigger_type}: {j.trigger_value}",
            "last_run": j.last_run_at or "never",
            "run_count": j.run_count,
        })
    return {
        "status": "ok",
        "total": len(job_list),
        "jobs": job_list,
    }


def _handle_create(service: Any, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new scheduled job."""
    name = inputs.get("name", "")
    skill = inputs.get("skill", "")
    cron = inputs.get("cron", "")
    skill_inputs = inputs.get("inputs", {})

    if not name:
        return {"status": "error", "error": "Missing required field: 'name'"}
    if not skill:
        return {"status": "error", "error": "Missing required field: 'skill'"}
    if not cron:
        return {"status": "error", "error": "Missing required field: 'cron'"}

    # Parse skill_inputs if it's a string
    if isinstance(skill_inputs, str):
        try:
            skill_inputs = json.loads(skill_inputs)
        except (json.JSONDecodeError, TypeError):
            skill_inputs = {}

    job_id = _slugify(name)

    # Check for duplicate
    if service.get_job(job_id) is not None:
        return {"status": "error", "error": f"Job '{job_id}' already exists. Delete it first or use a different name."}

    try:
        record = service.create_job(
            job_id=job_id,
            name=name,
            skill=skill,
            trigger_type="cron",
            trigger_value=cron,
            inputs=skill_inputs,
            description=f"Created via chat: {name}",
        )
        logger.info("schedule_job: created '%s' (skill=%s, cron=%s)", job_id, skill, cron)
        return {
            "status": "created",
            "job_id": record.id,
            "name": record.name,
            "skill": record.skill,
            "cron": cron,
            "inputs": skill_inputs,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _handle_delete(service: Any, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a scheduled job."""
    job_id = inputs.get("job_id", "")
    if not job_id:
        return {"status": "error", "error": "Missing required field: 'job_id'"}

    try:
        service.delete_job(job_id)
        logger.info("schedule_job: deleted '%s'", job_id)
        return {"status": "deleted", "job_id": job_id}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
