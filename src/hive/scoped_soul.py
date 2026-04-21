"""
HIVE Scoped Soul Generator — creates constrained Soul copies for sub-agents.

Scoped Souls can ONLY be more restrictive than the parent Soul.
Constraints are additive: more risk rules, fewer allowed_autonomous actions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.core.soul.store import Soul, AutonomyPosture, RiskRule, SchedulingBoundaries
from src.hive.types import TaskSpec, ControlMethod
from src.hive.errors import ScopedSoulViolationError
from src.mcp.federation_ceiling import validate_child_within_ceiling
from src.mcp.permissions import MCPServerPermission

logger = logging.getLogger(__name__)


_CATEGORY_ALIASES = {
    "app": "uab",
    "app_control": "uab",
    "browser": "uab",
    "desktop": "uab",
    "ui": "uab",
}

_EXACT_CAPABILITY_CATEGORIES = {
    "classify_intent": {"classify"},
    "summarize": {"summarize", "read"},
    "rag_rewrite": {"write"},
    "extract_json": {"query", "read"},
    "redact": {"redact", "write"},
    "health_check": {"health", "read"},
    "read_document": {"read"},
    "query_document": {"query", "read"},
    "uab_automation": {"uab"},
    "uab_query": {"uab", "query", "read"},
    "uab_state": {"uab", "query", "read"},
    "state": {"query", "read"},
    "query": {"query", "read"},
}

_PREFIX_CAPABILITY_CATEGORIES = {
    "classify": "classify",
    "health": "health",
    "query": "query",
    "read": "read",
    "redact": "redact",
    "summarize": "summarize",
    "uab": "uab",
}


def _permission_entry_to_dict(permission):
    """Normalize Soul permission entries for code that still expects dicts."""
    return permission.model_dump() if hasattr(permission, "model_dump") else permission


def normalize_allowed_categories(categories: Optional[Sequence[str]]) -> List[str]:
    """Normalize task categories to a canonical, deduplicated form."""
    normalized: List[str] = []
    seen: Set[str] = set()
    for raw in categories or []:
        value = str(raw or "").strip().lower()
        if not value:
            continue
        value = _CATEGORY_ALIASES.get(value, value)
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def capability_categories(capability: str) -> Set[str]:
    """Return canonical categories for a capability name."""
    normalized = str(capability or "").strip().lower()
    if not normalized:
        return set()

    categories = set(_EXACT_CAPABILITY_CATEGORIES.get(normalized, set()))
    prefix = normalized.split("_", 1)[0]
    prefix_category = _PREFIX_CAPABILITY_CATEGORIES.get(prefix)
    if prefix_category:
        categories.add(prefix_category)
    return categories


def capability_matches_allowed_categories(
    capability: str,
    allowed_categories: Optional[Sequence[str]],
) -> bool:
    """Check whether a capability is inside the task's scoped categories."""
    normalized_categories = normalize_allowed_categories(allowed_categories)
    if not normalized_categories:
        return True
    return bool(capability_categories(capability) & set(normalized_categories))


def scoped_soul_capability_decision(scoped_soul: Optional[object], capability: str) -> str:
    """Return 'allow', 'requires_approval', or 'deny' for a scoped capability."""
    boundary = scoped_capability_boundary(scoped_soul)
    if boundary is None:
        return "allow"

    normalized = str(capability or "").strip().lower()
    if not normalized:
        return "allow"

    allowed = set(boundary.allowed_autonomous)
    requires_approval = set(boundary.requires_approval)

    if normalized in allowed:
        return "allow"
    if normalized in requires_approval:
        return "requires_approval"
    return "deny"


def scoped_soul_enforces_capability(scoped_soul: Optional[object], capability: str) -> bool:
    """Whether the scoped Soul explicitly names this capability."""
    boundary = scoped_capability_boundary(scoped_soul)
    if boundary is None:
        return False
    normalized = str(capability or "").strip().lower()
    if not normalized:
        return False
    named_capabilities = boundary.named_capabilities
    if normalized in named_capabilities:
        return True

    requested_categories = capability_categories(normalized)
    if not requested_categories:
        return False

    for entry in named_capabilities:
        if capability_categories(entry) & requested_categories:
            return True
    return False


def _filter_capabilities_for_categories(
    capabilities: Iterable[str],
    allowed_categories: Optional[Sequence[str]],
) -> List[str]:
    normalized_categories = normalize_allowed_categories(allowed_categories)
    if not normalized_categories:
        return [cap for cap in capabilities]
    return [
        cap for cap in capabilities
        if capability_matches_allowed_categories(cap, normalized_categories)
    ]


def _extract_allowed_mcp_servers(
    allowed_categories: Optional[Sequence[str]],
) -> List[str]:
    servers: List[str] = []
    seen: Set[str] = set()
    for category in normalize_allowed_categories(allowed_categories):
        selector = None
        if category.startswith("mcp:"):
            selector = category.split(":", 1)[1].strip()
        elif category.startswith("server:"):
            selector = category.split(":", 1)[1].strip()
        if selector and selector not in seen:
            seen.add(selector)
            servers.append(selector)
    return servers


def _normalize_capability_entries(entries: Optional[Sequence[str]]) -> Set[str]:
    """Normalize capability entries for exact subset comparisons."""
    normalized: Set[str] = set()
    for raw in entries or []:
        value = str(raw or "").strip().lower()
        if value:
            normalized.add(value)
    return normalized


@dataclass(frozen=True)
class ScopedCapabilityBoundary:
    """Immutable execution ceiling for a spawned child agent."""

    allowed_autonomous: Tuple[str, ...] = ()
    requires_approval: Tuple[str, ...] = ()

    @property
    def named_capabilities(self) -> Set[str]:
        return set(self.allowed_autonomous) | set(self.requires_approval)


def _normalize_boundary_entries(entries: Optional[Sequence[str]]) -> Tuple[str, ...]:
    ordered: List[str] = []
    seen: Set[str] = set()
    for raw in entries or ():
        value = str(raw or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def scoped_capability_boundary(
    scoped_soul: Optional[object],
) -> Optional[ScopedCapabilityBoundary]:
    """Normalize a Soul-like input into an immutable capability boundary."""
    if scoped_soul is None:
        return None
    if isinstance(scoped_soul, ScopedCapabilityBoundary):
        return scoped_soul

    if isinstance(scoped_soul, Mapping):
        posture = scoped_soul.get("autonomy_posture")
        if isinstance(posture, Mapping):
            allowed = posture.get("allowed_autonomous")
            requires = posture.get("requires_approval")
        else:
            allowed = scoped_soul.get("allowed_autonomous")
            requires = scoped_soul.get("requires_approval")
        return ScopedCapabilityBoundary(
            allowed_autonomous=_normalize_boundary_entries(allowed),
            requires_approval=_normalize_boundary_entries(requires),
        )

    posture = getattr(scoped_soul, "autonomy_posture", None)
    if posture is None:
        return None

    return ScopedCapabilityBoundary(
        allowed_autonomous=_normalize_boundary_entries(
            getattr(posture, "allowed_autonomous", None)
        ),
        requires_approval=_normalize_boundary_entries(
            getattr(posture, "requires_approval", None)
        ),
    )


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
        scoped_allowed, scoped_requires_approval = self._derive_scoped_autonomy(
            parent_soul,
            task_spec,
        )

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

        # Narrow MCP permissions for sub-agent (same ceiling contract)
        scoped_mcp_permissions = list(
            parent_soul.mcp_permissions
        ) if hasattr(parent_soul, "mcp_permissions") else []

        # Only narrow MCP permissions when the task explicitly names allowed
        # server selectors such as "mcp:github-mcp" or "server:github-mcp".
        allowed_mcp_servers = _extract_allowed_mcp_servers(task_spec.allowed_categories)
        if allowed_mcp_servers and scoped_mcp_permissions:
            scoped_mcp_permissions = [
                p for p in scoped_mcp_permissions
                if _permission_entry_to_dict(p).get("server_id") in allowed_mcp_servers
            ]

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
            mcp_permissions=scoped_mcp_permissions,
        )

        logger.info(
            "Scoped Soul generated: allowed_auto=%d, requires_approval=%d, "
            "risk_rules=%d, control=%s",
            len(scoped_allowed), len(scoped_requires_approval),
            len(scoped_risk_rules), task_spec.control_method.value,
        )
        return scoped_soul

    def build_execution_boundary(
        self,
        parent_soul: Soul,
        task_spec: TaskSpec,
    ) -> ScopedCapabilityBoundary:
        """Derive the immutable execution ceiling from parent Soul + task."""
        scoped_allowed, scoped_requires_approval = self._derive_scoped_autonomy(
            parent_soul,
            task_spec,
        )
        return ScopedCapabilityBoundary(
            allowed_autonomous=_normalize_boundary_entries(scoped_allowed),
            requires_approval=_normalize_boundary_entries(scoped_requires_approval),
        )

    @staticmethod
    def _derive_scoped_autonomy(
        parent_soul: Soul,
        task_spec: TaskSpec,
    ) -> Tuple[List[str], List[str]]:
        """Derive the child capability surface from parent Soul + frozen task."""
        scoped_allowed = list(parent_soul.autonomy_posture.allowed_autonomous)
        scoped_requires_approval = list(parent_soul.autonomy_posture.requires_approval)

        if task_spec.allowed_categories:
            scoped_allowed = _filter_capabilities_for_categories(
                scoped_allowed,
                task_spec.allowed_categories,
            )
            scoped_requires_approval = _filter_capabilities_for_categories(
                scoped_requires_approval,
                task_spec.allowed_categories,
            )

        if task_spec.control_method == ControlMethod.MANUAL_CONFIRM:
            scoped_requires_approval = list(
                set(scoped_requires_approval) | set(scoped_allowed)
            )
            scoped_allowed = []

        return scoped_allowed, scoped_requires_approval

    def validate_more_restrictive(
        self,
        scoped: Soul,
        parent: Soul,
    ) -> bool:
        """Validate that a scoped Soul is more restrictive than parent.

        Checks:
        1. All parent risk rules preserved
        2. No new allowed_autonomous actions beyond parent autonomy
        3. No new requires_approval capabilities beyond parent governed surface
        4. Scheduling boundaries not loosened

        Returns True if valid (more restrictive), False otherwise.
        """
        # Check: all parent risk rule names preserved
        parent_rule_names = {r.name for r in parent.risk_rules}
        scoped_rule_names = {r.name for r in scoped.risk_rules}
        if not parent_rule_names.issubset(scoped_rule_names):
            return False

        # Check: no new allowed_autonomous beyond parent autonomous surface
        parent_allowed = _normalize_capability_entries(
            parent.autonomy_posture.allowed_autonomous
        )
        scoped_allowed = _normalize_capability_entries(
            scoped.autonomy_posture.allowed_autonomous
        )
        if not scoped_allowed.issubset(parent_allowed):
            return False

        # Check: requires_approval cannot introduce capabilities the parent never governed.
        # A child may downgrade a parent autonomous capability into approval, but it may not
        # add a brand-new governed capability through requires_approval.
        parent_governed = parent_allowed | _normalize_capability_entries(
            parent.autonomy_posture.requires_approval
        )
        scoped_requires_approval = _normalize_capability_entries(
            scoped.autonomy_posture.requires_approval
        )
        if not scoped_requires_approval.issubset(parent_governed):
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

        # Check: MCP permissions narrowed (same ceiling contract used in federation)
        if hasattr(parent, "mcp_permissions") and hasattr(scoped, "mcp_permissions"):
            parent_permissions = [
                MCPServerPermission.from_dict(_permission_entry_to_dict(p))
                for p in (parent.mcp_permissions or [])
            ]
            scoped_permissions = [
                MCPServerPermission.from_dict(_permission_entry_to_dict(p))
                for p in (scoped.mcp_permissions or [])
            ]
            if validate_child_within_ceiling(scoped_permissions, parent_permissions):
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
