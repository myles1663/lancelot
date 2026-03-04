"""
HIVE Scoped Soul Generator — creates constrained Soul copies for sub-agents.

Scoped Souls can ONLY be more restrictive than the parent Soul.
Constraints are additive: more risk rules, fewer allowed_autonomous actions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import List, Optional

from src.core.soul.store import Soul, AutonomyPosture, RiskRule, SchedulingBoundaries
from src.hive.types import TaskSpec, ControlMethod
from src.hive.errors import ScopedSoulViolationError

logger = logging.getLogger(__name__)


class ScopedSoulGenerator:
    """Generates constrained Soul documents for HIVE sub-agents.

    A Scoped Soul inherits the parent's governance and adds restrictions:
    - Allowed autonomous actions are limited to the task's scope
    - Risk rules from the parent are preserved (never removed)
    - Additional risk rules may be added
    - Scheduling boundaries are tightened
    """

    def generate(
        self,
        parent_soul: Soul,
        task_spec: TaskSpec,
        extra_risk_rules: Optional[List[RiskRule]] = None,
    ) -> Soul:
        """Generate a Scoped Soul for a sub-agent.

        The generated Soul is always MORE restrictive than the parent.

        Args:
            parent_soul: The parent (system-wide) Soul.
            task_spec: Task specification for the sub-agent.
            extra_risk_rules: Additional risk rules beyond parent.

        Returns:
            A new Soul instance with tightened constraints.
        """
        # Start with parent's allowed autonomous actions
        scoped_allowed = list(parent_soul.autonomy_posture.allowed_autonomous)
        scoped_requires_approval = list(parent_soul.autonomy_posture.requires_approval)

        # If task specifies allowed categories, restrict further
        if task_spec.allowed_categories:
            # Only keep parent actions that match allowed categories
            scoped_allowed = [
                a for a in scoped_allowed
                if any(cat in a for cat in task_spec.allowed_categories)
            ]

        # Manual confirm mode = everything requires approval
        if task_spec.control_method == ControlMethod.MANUAL_CONFIRM:
            scoped_requires_approval = list(
                set(scoped_requires_approval) | set(scoped_allowed)
            )
            scoped_allowed = []

        # Build risk rules: parent + extra (never remove parent rules)
        scoped_risk_rules = list(parent_soul.risk_rules)
        existing_names = {r.name for r in scoped_risk_rules}

        # Add HIVE-specific rule
        hive_rule = RiskRule(
            name=f"hive_scoped_{task_spec.task_id[:8]}",
            description=(
                f"Scoped Soul constraints for sub-agent task: "
                f"{task_spec.description[:100]}"
            ),
            enforced=True,
        )
        if hive_rule.name not in existing_names:
            scoped_risk_rules.append(hive_rule)
            existing_names.add(hive_rule.name)

        if extra_risk_rules:
            for rule in extra_risk_rules:
                if rule.name not in existing_names:
                    scoped_risk_rules.append(rule)
                    existing_names.add(rule.name)

        # Tighten scheduling boundaries
        scoped_sched = SchedulingBoundaries(
            max_concurrent_jobs=1,  # Sub-agents run one job at a time
            max_job_duration_seconds=min(
                task_spec.timeout_seconds,
                parent_soul.scheduling_boundaries.max_job_duration_seconds,
            ),
            no_autonomous_irreversible=True,  # Always true for sub-agents
            require_ready_state=True,
            description=(
                f"{parent_soul.scheduling_boundaries.description}\n\n"
                f"[HIVE Scoped] Sub-agent timeout: {task_spec.timeout_seconds}s, "
                f"max actions: {task_spec.max_actions}"
            ).strip(),
        )

        # Build the scoped Soul — version tagged as scoped
        scoped_soul = Soul(
            version=parent_soul.version,
            mission=parent_soul.mission,
            allegiance=parent_soul.allegiance,
            autonomy_posture=AutonomyPosture(
                level="scoped",
                description=(
                    f"HIVE sub-agent scoped from parent ({parent_soul.autonomy_posture.level}). "
                    f"Control method: {task_spec.control_method.value}"
                ),
                allowed_autonomous=scoped_allowed,
                requires_approval=scoped_requires_approval,
            ),
            risk_rules=[
                r.model_dump() if hasattr(r, "model_dump")
                else {"name": r.name, "description": r.description, "enforced": r.enforced}
                for r in scoped_risk_rules
            ],
            approval_rules=(
                parent_soul.approval_rules.model_dump()
                if hasattr(parent_soul.approval_rules, "model_dump")
                else parent_soul.approval_rules
            ),
            tone_invariants=list(parent_soul.tone_invariants),
            memory_ethics=list(parent_soul.memory_ethics),
            scheduling_boundaries=scoped_sched.model_dump(),
        )

        logger.info(
            "Scoped Soul generated: allowed_auto=%d, requires_approval=%d, "
            "risk_rules=%d, control=%s",
            len(scoped_allowed), len(scoped_requires_approval),
            len(scoped_risk_rules), task_spec.control_method.value,
        )
        return scoped_soul

    def validate_more_restrictive(
        self,
        scoped: Soul,
        parent: Soul,
    ) -> bool:
        """Validate that a scoped Soul is more restrictive than parent.

        Checks:
        1. All parent risk rules preserved
        2. No new allowed_autonomous actions beyond parent
        3. Scheduling boundaries not loosened

        Returns True if valid (more restrictive), False otherwise.
        """
        # Check: all parent risk rule names preserved
        parent_rule_names = {r.name for r in parent.risk_rules}
        scoped_rule_names = {r.name for r in scoped.risk_rules}
        if not parent_rule_names.issubset(scoped_rule_names):
            return False

        # Check: no new allowed_autonomous beyond parent
        parent_allowed = set(parent.autonomy_posture.allowed_autonomous)
        scoped_allowed = set(scoped.autonomy_posture.allowed_autonomous)
        if not scoped_allowed.issubset(parent_allowed):
            return False

        # Check: scheduling not loosened
        if (scoped.scheduling_boundaries.max_job_duration_seconds >
                parent.scheduling_boundaries.max_job_duration_seconds):
            return False
        if (scoped.scheduling_boundaries.max_concurrent_jobs >
                parent.scheduling_boundaries.max_concurrent_jobs):
            return False

        # Check: no_autonomous_irreversible must remain true if parent has it
        if (parent.scheduling_boundaries.no_autonomous_irreversible and
                not scoped.scheduling_boundaries.no_autonomous_irreversible):
            return False

        return True

    @staticmethod
    def hash_soul(soul: Soul) -> str:
        """Compute a deterministic hash of a Soul document.

        Used to detect Soul mutations and verify integrity.
        """
        # Serialize to a canonical JSON string
        data = soul.model_dump()
        canonical = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
