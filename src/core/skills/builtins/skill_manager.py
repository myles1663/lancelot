"""
Built-in skill: skill_manager — create, list, and manage dynamic skills.

Wraps SkillFactory for proposal generation and SkillRegistry for listing.
Proposals require owner approval before installation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

MANIFEST = {
    "name": "skill_manager",
    "version": "1.0.0",
    "description": "Create, list, and manage dynamic skills via the proposal pipeline",
    "risk": "MEDIUM",
    "permissions": ["skill_manage"],
    "inputs": [
        {"name": "action", "type": "string", "required": True,
         "description": "propose|list_proposals|list_skills|run_skill"},
    ],
}


def execute(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a skill management action."""
    action = inputs.get("action", "").lower()

    if action == "propose":
        return _propose_skill(inputs)
    elif action == "list_proposals":
        return _list_proposals()
    elif action == "list_skills":
        return _list_skills()
    elif action == "run_skill":
        return _run_skill(context, inputs)
    else:
        raise ValueError(
            f"Unknown action: '{action}'. Must be propose|list_proposals|list_skills|run_skill"
        )


def _get_factory():
    """Get the SkillFactory from the orchestrator."""
    try:
        from gateway import main_orchestrator
        factory = getattr(main_orchestrator, 'skill_factory', None)
        if factory is None:
            raise RuntimeError("SkillFactory not initialized")
        return factory
    except Exception as exc:
        raise RuntimeError(f"Cannot access SkillFactory: {exc}")


def _get_registry():
    """Get the SkillRegistry from the orchestrator."""
    try:
        from gateway import main_orchestrator
        registry = getattr(main_orchestrator, 'skill_registry', None)
        if registry is None:
            raise RuntimeError("SkillRegistry not initialized")
        return registry
    except Exception as exc:
        raise RuntimeError(f"Cannot access SkillRegistry: {exc}")


def _get_executor():
    """Get the SkillExecutor from the orchestrator."""
    try:
        from gateway import main_orchestrator
        executor = getattr(main_orchestrator, 'skill_executor', None)
        if executor is None:
            raise RuntimeError("SkillExecutor not initialized")
        return executor
    except Exception as exc:
        raise RuntimeError(f"Cannot access SkillExecutor: {exc}")


def _propose_skill(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Create a skill proposal with real implementation code."""
    name = inputs.get("name", "").strip()
    description = inputs.get("description", "")
    permissions = inputs.get("permissions")
    execute_code = inputs.get("execute_code", "")

    if not name:
        raise ValueError("Missing required input: 'name'")
    if not execute_code:
        raise ValueError("Missing required input: 'execute_code' — provide the Python implementation")

    # Parse permissions if provided as string
    if isinstance(permissions, str):
        try:
            permissions = json.loads(permissions)
        except json.JSONDecodeError:
            permissions = [p.strip() for p in permissions.split(",") if p.strip()]

    factory = _get_factory()

    # Generate the skeleton proposal
    proposal = factory.generate_skeleton(
        name=name,
        description=description,
        permissions=permissions,
    )

    # Override the TODO stub with real implementation code
    proposal.execute_code = execute_code

    # Save the updated proposal
    proposals = factory._load_proposals()
    for i, p in enumerate(proposals):
        if p.id == proposal.id:
            proposals[i] = proposal
            break
    factory._save_proposals(proposals)

    logger.info("Skill proposed: name=%s, id=%s", name, proposal.id)

    return {
        "status": "proposed",
        "proposal_id": proposal.id,
        "name": name,
        "description": description,
        "message": (
            f"Skill '{name}' proposed (id: {proposal.id}). "
            "Awaiting owner approval in the War Room before installation."
        ),
    }


def _list_proposals() -> Dict[str, Any]:
    """List all skill proposals."""
    factory = _get_factory()
    proposals = factory.list_proposals()
    return {
        "proposals": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "status": p.status.value if hasattr(p.status, 'value') else str(p.status),
                "created_at": p.created_at,
                "approved_by": p.approved_by,
            }
            for p in proposals
        ],
        "total": len(proposals),
    }


def _list_skills() -> Dict[str, Any]:
    """List all installed skills."""
    registry = _get_registry()
    skills = registry.list_skills()
    return {
        "skills": [
            {
                "name": s.name,
                "version": s.version,
                "enabled": s.enabled,
                "ownership": s.ownership.value if hasattr(s.ownership, 'value') else str(s.ownership),
            }
            for s in skills
        ],
        "total": len(skills),
    }


def _run_skill(context, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run an installed skill by name."""
    skill_name = inputs.get("skill_name", "").strip()
    skill_inputs_raw = inputs.get("skill_inputs", "{}")

    if not skill_name:
        raise ValueError("Missing required input: 'skill_name'")

    # Parse skill_inputs if string
    if isinstance(skill_inputs_raw, str):
        try:
            skill_inputs = json.loads(skill_inputs_raw)
        except json.JSONDecodeError:
            skill_inputs = {"input_data": skill_inputs_raw}
    else:
        skill_inputs = skill_inputs_raw

    executor = _get_executor()
    result = executor.run(skill_name, skill_inputs, context)

    if result.success:
        return {
            "status": "success",
            "skill": skill_name,
            "outputs": result.outputs,
            "duration_ms": result.duration_ms,
        }
    else:
        return {
            "status": "error",
            "skill": skill_name,
            "error": result.error,
        }
