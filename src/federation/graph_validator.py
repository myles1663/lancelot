# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Graph Validator — 4-dimension compatibility evaluation for federation edges.

Evaluates every edge in the federation topology across four dimensions:
1. trust_tier — Authorization and trust level compatibility
2. content_type — Data/workflow content type compatibility
3. risk_tier — Risk assessment and safety level compatibility
4. capability_dependency — Required capabilities and their availability

Each dimension produces a DimensionState (GREEN/YELLOW/RED) with a report
and resolution options. The overall EdgeState is the worst-case across all
dimensions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.federation.graph_models import (
    DimensionResult,
    DimensionState,
    EdgeState,
    GraphEdge,
    GraphNode,
    TopologyDocument,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Dimension Evaluators
# ═══════════════════════════════════════════════════════════════

def evaluate_trust_tier(
    source: GraphNode,
    target: GraphNode,
) -> DimensionResult:
    """Evaluate trust tier compatibility between two nodes.

    Trust tiers are derived from the instance role hierarchy:
    - root → child: inherent trust (GREEN)
    - child → root: reporting trust (GREEN)
    - peer → peer: mutual trust (GREEN if both have federation keys)
    - leaf → any: limited trust (YELLOW — leaf nodes have restricted autonomy)
    - any → unknown: no trust (RED — missing identity/endpoint)
    """
    result = DimensionResult(dimension="trust_tier")

    # RED: missing identity on either side
    if not source.federation_identity_public_key and not source.is_local:
        result.state = DimensionState.RED
        result.report = f"Source node '{source.instance_name}' has no federation identity"
        result.resolution_options = [
            "Generate federation identity for source node",
            "Remove this edge from topology",
        ]
        return result

    if not target.federation_identity_public_key and not target.is_local:
        result.state = DimensionState.RED
        result.report = f"Target node '{target.instance_name}' has no federation identity"
        result.resolution_options = [
            "Generate federation identity for target node",
            "Remove this edge from topology",
        ]
        return result

    # RED: missing endpoint on remote node
    if not target.endpoint and not target.is_local:
        result.state = DimensionState.RED
        result.report = f"Target node '{target.instance_name}' has no endpoint configured"
        result.resolution_options = [
            "Configure endpoint URL for target node",
        ]
        return result

    # YELLOW: leaf node involvement
    from src.federation.graph_models import InstanceRole
    if source.instance_role == InstanceRole.LEAF:
        result.state = DimensionState.YELLOW
        result.report = "Source is a leaf node — limited trust, restricted autonomy"
        result.resolution_options = [
            "Promote source to peer role",
            "Acknowledge leaf trust limitation",
        ]
        return result

    if target.instance_role == InstanceRole.LEAF:
        result.state = DimensionState.YELLOW
        result.report = "Target is a leaf node — limited trust, restricted autonomy"
        result.resolution_options = [
            "Promote target to peer role",
            "Acknowledge leaf trust limitation",
        ]
        return result

    # GREEN: both have identity, valid roles
    result.state = DimensionState.GREEN
    result.report = "Trust tier compatible"
    return result


def evaluate_content_type(
    source: GraphNode,
    target: GraphNode,
    edge: GraphEdge,
) -> DimensionResult:
    """Evaluate content type compatibility between two nodes.

    Content type compatibility is determined by the Soul source modes:
    - INHERITED: child inherits parent's Soul — content always compatible with parent
    - CUSTOM: operator-defined Soul — must validate compatibility
    - LINKED: linked to remote Soul version — compatible if version matches
    """
    from src.federation.graph_models import SoulSourceMode

    result = DimensionResult(dimension="content_type")

    # GREEN: inherited mode — child inherits parent's Soul
    if target.soul_source_mode == SoulSourceMode.INHERITED:
        result.state = DimensionState.GREEN
        result.report = "Target inherits Soul from parent — content compatible"
        return result

    # GREEN: linked mode with matching version hash
    if target.soul_source_mode == SoulSourceMode.LINKED:
        if source.soul_version_hash and target.soul_version_hash:
            if source.soul_version_hash == target.soul_version_hash:
                result.state = DimensionState.GREEN
                result.report = "Linked Soul versions match"
                return result
            else:
                result.state = DimensionState.YELLOW
                result.report = (
                    f"Linked Soul versions differ: "
                    f"source={source.soul_version_hash[:8]}, "
                    f"target={target.soul_version_hash[:8]}"
                )
                result.resolution_options = [
                    "Push Soul version update to target",
                    "Accept version divergence",
                ]
                return result
        else:
            result.state = DimensionState.YELLOW
            result.report = "Linked mode but version hash missing on one or both nodes"
            result.resolution_options = [
                "Fetch and verify Soul version hashes",
            ]
            return result

    # CUSTOM mode: check if both have soul versions configured
    if not source.soul_version and not target.soul_version:
        result.state = DimensionState.YELLOW
        result.report = "Neither node has a Soul version configured"
        result.resolution_options = [
            "Configure Soul versions on both nodes",
        ]
        return result

    if source.soul_version and target.soul_version:
        result.state = DimensionState.GREEN
        result.report = "Both nodes have custom Soul versions configured"
    else:
        missing = "source" if not source.soul_version else "target"
        result.state = DimensionState.YELLOW
        result.report = f"Custom Soul mode but {missing} node missing Soul version"
        result.resolution_options = [
            f"Configure Soul version on {missing} node",
        ]

    return result


def evaluate_risk_tier(
    source: GraphNode,
    target: GraphNode,
    edge: GraphEdge,
) -> DimensionResult:
    """Evaluate risk tier compatibility between two nodes.

    Risk assessment considers:
    - HIVE configuration differences (agent limits, UAB access)
    - Budget ceiling alignment
    - Hierarchical role risk (root→child vs peer→peer)
    """
    from src.federation.graph_models import InstanceRole, RelationshipType

    result = DimensionResult(dimension="risk_tier")
    warnings: List[str] = []

    # Check HIVE UAB risk
    if target.hive_config.uab_enabled and not source.hive_config.uab_enabled:
        warnings.append(
            "Target has UAB enabled but source does not — "
            "handoff may grant desktop control not available at source"
        )

    # Check agent limit mismatch
    if target.hive_config.enabled and source.hive_config.enabled:
        if target.hive_config.max_concurrent_agents > source.hive_config.max_concurrent_agents * 2:
            warnings.append(
                f"Target allows {target.hive_config.max_concurrent_agents} concurrent agents "
                f"vs source's {source.hive_config.max_concurrent_agents} — "
                f"significant resource amplification risk"
            )

    # Check budget ceiling mismatch
    source_ceiling = source.budget_config.daily_ceiling_usd
    target_ceiling = target.budget_config.daily_ceiling_usd
    if target_ceiling > source_ceiling * 3:
        warnings.append(
            f"Target budget ceiling (${target_ceiling:.2f}) is >3x source "
            f"(${source_ceiling:.2f}) — cost amplification risk"
        )

    # Check hierarchical handoff direction risk
    if edge.relationship_type == RelationshipType.HIERARCHICAL_PARENT_CHILD:
        if source.instance_role == InstanceRole.CHILD and target.instance_role == InstanceRole.ROOT:
            warnings.append(
                "Child→Root handoff detected — ensure child has authority to push to root"
            )

    # Determine state
    if not warnings:
        result.state = DimensionState.GREEN
        result.report = "Risk tiers compatible"
    elif any("amplification" in w for w in warnings):
        result.state = DimensionState.RED
        result.report = "; ".join(warnings)
        result.resolution_options = [
            "Align HIVE agent limits between nodes",
            "Align budget ceilings between nodes",
            "Add handoff contract with resource constraints",
        ]
    else:
        result.state = DimensionState.YELLOW
        result.report = "; ".join(warnings)
        result.resolution_options = [
            "Acknowledge risk tier differences",
            "Align configuration between nodes",
        ]

    return result


def evaluate_capability_dependency(
    source: GraphNode,
    target: GraphNode,
    edge: GraphEdge,
) -> DimensionResult:
    """Evaluate capability dependency compatibility.

    Checks that the target node can fulfill requirements implied by the
    edge relationship:
    - HIVE must be enabled on target if source expects agent handoff
    - Target must have HIVE enabled if source has HIVE enabled (for federated handoff)
    - Target must be reachable (has endpoint)
    """
    from src.federation.graph_models import RelationshipType

    result = DimensionResult(dimension="capability_dependency")
    issues: List[str] = []

    # For federated handoff, target must be reachable
    if edge.relationship_type == RelationshipType.FEDERATED_HANDOFF:
        if not target.endpoint and not target.is_local:
            issues.append("Target has no endpoint — federated handoff impossible")

    # If source has HIVE, target should too for agent handoff
    if source.hive_config.enabled and not target.hive_config.enabled:
        issues.append(
            "Source has HIVE enabled but target does not — "
            "agent handoff will not work"
        )

    # Check connection status
    if target.connection_status == "grey":
        issues.append("Target node is offline — handoff will fail")

    if not issues:
        result.state = DimensionState.GREEN
        result.report = "All capability dependencies satisfied"
    else:
        # Offline or missing endpoint = RED; HIVE mismatch = YELLOW
        if any("impossible" in i or "offline" in i for i in issues):
            result.state = DimensionState.RED
            result.report = "; ".join(issues)
            result.resolution_options = [
                "Configure target endpoint",
                "Enable HIVE on target node",
                "Bring target node online",
            ]
        else:
            result.state = DimensionState.YELLOW
            result.report = "; ".join(issues)
            result.resolution_options = [
                "Enable HIVE on target node",
                "Remove HIVE dependency from handoff contract",
            ]

    return result


# ═══════════════════════════════════════════════════════════════
# Edge Validator
# ═══════════════════════════════════════════════════════════════

def validate_edge(
    source: GraphNode,
    target: GraphNode,
    edge: GraphEdge,
) -> Tuple[EdgeState, List[DimensionResult]]:
    """Run all 4 dimension evaluations on an edge.

    Returns:
        (overall_edge_state, list_of_dimension_results)

    The overall state is the worst-case across all dimensions:
    - Any RED → EdgeState.RED
    - Any YELLOW (no RED) → EdgeState.YELLOW
    - All GREEN → EdgeState.GREEN
    """
    results = [
        evaluate_trust_tier(source, target),
        evaluate_content_type(source, target, edge),
        evaluate_risk_tier(source, target, edge),
        evaluate_capability_dependency(source, target, edge),
    ]

    # Worst-case aggregation
    states = [r.state for r in results]
    if DimensionState.RED in states:
        overall = EdgeState.RED
    elif DimensionState.YELLOW in states:
        overall = EdgeState.YELLOW
    else:
        overall = EdgeState.GREEN

    return overall, results


def validate_topology(
    topology: TopologyDocument,
) -> Dict[str, Tuple[EdgeState, List[DimensionResult]]]:
    """Validate all edges in a topology document.

    Returns:
        Dict mapping edge_id → (overall_state, dimension_results)
    """
    results: Dict[str, Tuple[EdgeState, List[DimensionResult]]] = {}

    for edge in topology.edges:
        source = topology.get_node(edge.source_node_id)
        target = topology.get_node(edge.target_node_id)

        if not source or not target:
            # Missing node — create RED result
            dim = DimensionResult(
                dimension="trust_tier",
                state=DimensionState.RED,
                report=f"Missing node: source={edge.source_node_id}, target={edge.target_node_id}",
            )
            results[edge.edge_id] = (EdgeState.RED, [dim])
            continue

        state, dims = validate_edge(source, target, edge)
        results[edge.edge_id] = (state, dims)

    return results


# ═══════════════════════════════════════════════════════════════
# Deployment Gate
# ═══════════════════════════════════════════════════════════════

class DeploymentGateResult:
    """Result of the deployment gate check."""

    def __init__(
        self,
        deployable: bool,
        blocking_edges: List[str],
        warning_edges: List[str],
        unacknowledged_yellows: List[str],
        report: str,
    ):
        self.deployable = deployable
        self.blocking_edges = blocking_edges
        self.warning_edges = warning_edges
        self.unacknowledged_yellows = unacknowledged_yellows
        self.report = report

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deployable": self.deployable,
            "blocking_edges": self.blocking_edges,
            "warning_edges": self.warning_edges,
            "unacknowledged_yellows": self.unacknowledged_yellows,
            "report": self.report,
        }


def check_deployment_gate(
    topology: TopologyDocument,
) -> DeploymentGateResult:
    """Check if a topology is deployable.

    Rules:
    - Any RED edge → NOT deployable (blocking)
    - Any YELLOW edge without operator acknowledgment → NOT deployable
    - All GREEN or acknowledged YELLOW → deployable
    - Empty topology (no edges) → deployable (standalone mode)
    """
    if not topology.edges:
        return DeploymentGateResult(
            deployable=True,
            blocking_edges=[],
            warning_edges=[],
            unacknowledged_yellows=[],
            report="No edges — standalone deployment",
        )

    validation = validate_topology(topology)
    blocking: List[str] = []
    warning: List[str] = []
    unacked: List[str] = []

    for edge_id, (state, dims) in validation.items():
        if state == EdgeState.RED:
            blocking.append(edge_id)
        elif state == EdgeState.YELLOW:
            warning.append(edge_id)
            # Check for acknowledgment
            edge = next((e for e in topology.edges if e.edge_id == edge_id), None)
            if edge and not edge.yellow_acknowledgments:
                unacked.append(edge_id)

    deployable = len(blocking) == 0 and len(unacked) == 0

    if blocking:
        report = f"BLOCKED: {len(blocking)} edge(s) have RED compatibility"
    elif unacked:
        report = f"BLOCKED: {len(unacked)} YELLOW edge(s) need operator acknowledgment"
    elif warning:
        report = f"DEPLOYABLE: {len(warning)} acknowledged YELLOW edge(s)"
    else:
        report = "DEPLOYABLE: All edges GREEN"

    return DeploymentGateResult(
        deployable=deployable,
        blocking_edges=blocking,
        warning_edges=warning,
        unacknowledged_yellows=unacked,
        report=report,
    )
