# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Graph Builder Models — data models for federation topology editor.

Defines the JSON-serializable schema for nodes, edges, and complete
topology documents used by the Graph Builder UI and deployment pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════

class InstanceRole(str, Enum):
    ROOT = "root"
    CHILD = "child"
    PEER = "peer"
    LEAF = "leaf"


class SoulSourceMode(str, Enum):
    INHERITED = "inherited"  # Inherits from parent (hierarchical only)
    CUSTOM = "custom"        # Operator-defined Soul document
    LINKED = "linked"        # Linked to a specific Soul version on remote


class RelationshipType(str, Enum):
    HIERARCHICAL_PARENT_CHILD = "hierarchical_parent_child"
    FEDERATED_HANDOFF = "federated_handoff"


class TriggerCondition(str, Enum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"


class EdgeState(str, Enum):
    GREEN = "green"    # Fully compatible
    YELLOW = "yellow"  # Conditional compatibility
    RED = "red"        # Incompatible — blocks deployment
    UNKNOWN = "unknown"  # Not yet validated


class DimensionState(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    NOT_EVALUATED = "not_evaluated"


class AssumptionType(str, Enum):
    EVALUABLE = "evaluable"        # Can be programmatically checked
    INFORMATIONAL = "informational"  # Context only, not checkable


class AssumptionCriticality(str, Enum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    INFORMATIONAL = "informational"


# ═══════════════════════════════════════════════════════════════
# Node Models
# ═══════════════════════════════════════════════════════════════

class NodePosition(BaseModel):
    """Canvas position for a node."""
    x: float = 0.0
    y: float = 0.0


class NodeBudgetConfig(BaseModel):
    """Budget configuration for a single node."""
    daily_ceiling_usd: float = Field(default=10.0, ge=0.01)
    warning_pct: float = Field(default=80.0, ge=50.0, le=100.0)
    critical_pct: float = Field(default=95.0, ge=70.0, le=100.0)


class NodeHiveConfig(BaseModel):
    """HIVE configuration for a node (bounded by Soul constraints)."""
    enabled: bool = False
    max_concurrent_agents: int = Field(default=10, ge=1, le=100)
    default_task_timeout: int = Field(default=300, ge=30)
    max_actions_per_agent: int = Field(default=50, ge=1)
    uab_enabled: bool = False


class GraphNode(BaseModel):
    """A node in the federation topology graph."""
    node_id: str  # Federation public key or LOCAL_INSTANCE
    instance_name: str = ""
    endpoint: str = ""  # URL for remote nodes
    federation_identity_public_key: str = ""
    fingerprint: str = ""
    instance_role: InstanceRole = InstanceRole.PEER
    soul_source_mode: SoulSourceMode = SoulSourceMode.CUSTOM
    soul_version: str = ""
    soul_version_hash: str = ""
    connection_status: str = "unknown"  # green, grey, unknown
    hive_config: NodeHiveConfig = Field(default_factory=NodeHiveConfig)
    budget_config: NodeBudgetConfig = Field(default_factory=NodeBudgetConfig)
    position: NodePosition = Field(default_factory=NodePosition)
    timezone: str = "UTC"
    is_local: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# Edge Models
# ═══════════════════════════════════════════════════════════════

class DimensionResult(BaseModel):
    """Result of a single compatibility dimension evaluation."""
    dimension: str  # trust_tier, content_type, risk_tier, capability_dependency
    state: DimensionState = DimensionState.NOT_EVALUATED
    report: str = ""
    resolution_options: List[str] = Field(default_factory=list)


class YellowAcknowledgment(BaseModel):
    """Operator acknowledgment of a yellow edge condition."""
    operator: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    condition: str = ""
    note: str = ""


class ResolutionRecord(BaseModel):
    """Record of a conflict resolution action."""
    conflict_type: str = ""
    resolution_selected: str = ""
    soul_changes: str = ""
    operator: str = ""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    result_after: str = ""  # Edge state after resolution


class ContractAssumption(BaseModel):
    """A single assumption in a handoff contract."""
    text: str
    assumption_type: AssumptionType = AssumptionType.EVALUABLE
    criticality: AssumptionCriticality = AssumptionCriticality.IMPORTANT
    is_vague: bool = False
    refinement_suggestion: str = ""


class HandoffContract(BaseModel):
    """Structured handoff contract for workflow transfer between instances."""
    context_and_assumptions: List[ContractAssumption] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    data_payload_schema: Dict[str, Any] = Field(default_factory=dict)
    soul_context_constraints: Dict[str, Any] = Field(default_factory=dict)
    template_id: Optional[str] = None


class GraphEdge(BaseModel):
    """An edge in the federation topology graph."""
    edge_id: str = ""
    source_node_id: str  # Federation public key of source
    target_node_id: str  # Federation public key of target
    relationship_type: RelationshipType = RelationshipType.FEDERATED_HANDOFF
    trigger_condition: TriggerCondition = TriggerCondition.ALWAYS
    priority: int = Field(default=0, ge=0)
    compatibility_state: EdgeState = EdgeState.UNKNOWN
    dimension_results: List[DimensionResult] = Field(default_factory=list)
    yellow_acknowledgments: List[YellowAcknowledgment] = Field(default_factory=list)
    resolution_history: List[ResolutionRecord] = Field(default_factory=list)
    handoff_contract: HandoffContract = Field(default_factory=HandoffContract)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# Topology Document
# ═══════════════════════════════════════════════════════════════

class TopologyDocument(BaseModel):
    """Complete federation topology — the authoritative source of truth.

    Versioned, JSON-serializable, deployable. Every instance receives a copy
    at deployment time.
    """
    topology_id: str = ""
    topology_name: str = ""
    version: int = 1
    version_hash: str = ""
    deployment_mode: str = "standalone"
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    deployed_at: Optional[str] = None
    created_by: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def compute_version_hash(self) -> str:
        """Compute deterministic hash of topology configuration."""
        # Exclude timestamps and deployment state from hash
        hashable = {
            "nodes": [n.model_dump(exclude={"position", "connection_status", "metadata"})
                      for n in self.nodes],
            "edges": [e.model_dump(exclude={"metadata"})
                      for e in self.edges],
        }
        canonical = json.dumps(hashable, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Find a node by ID."""
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def get_edges_from(self, node_id: str) -> List[GraphEdge]:
        """Get all edges originating from a node."""
        return [e for e in self.edges if e.source_node_id == node_id]

    def get_edges_to(self, node_id: str) -> List[GraphEdge]:
        """Get all edges targeting a node."""
        return [e for e in self.edges if e.target_node_id == node_id]

    def detect_deployment_mode(self) -> str:
        """Detect deployment mode from topology shape."""
        if not self.nodes or len(self.nodes) <= 1:
            return "standalone"

        roles = [n.instance_role for n in self.nodes]
        has_root = InstanceRole.ROOT in roles
        has_child = InstanceRole.CHILD in roles

        if has_root or has_child:
            return "hierarchical"
        return "federated"
