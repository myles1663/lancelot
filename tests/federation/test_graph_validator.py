# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Graph Builder 4-dimension compatibility validator."""

import pytest
from src.federation.graph_models import (
    DimensionState,
    EdgeState,
    GraphEdge,
    GraphNode,
    InstanceRole,
    NodeBudgetConfig,
    NodeHiveConfig,
    NodePosition,
    RelationshipType,
    SoulSourceMode,
    TopologyDocument,
    YellowAcknowledgment,
)
from src.federation.graph_validator import (
    DeploymentGateResult,
    check_deployment_gate,
    evaluate_capability_dependency,
    evaluate_content_type,
    evaluate_risk_tier,
    evaluate_trust_tier,
    validate_edge,
    validate_topology,
)


def _node(
    node_id="node-a",
    name="Node A",
    endpoint="https://a.example.com",
    pub_key="pubkey-a",
    role=InstanceRole.PEER,
    soul_mode=SoulSourceMode.CUSTOM,
    soul_version="v1",
    soul_hash="abc123",
    is_local=False,
    hive_enabled=False,
    uab_enabled=False,
    max_agents=10,
    budget_usd=10.0,
    connection_status="green",
):
    return GraphNode(
        node_id=node_id,
        instance_name=name,
        endpoint=endpoint,
        federation_identity_public_key=pub_key,
        instance_role=role,
        soul_source_mode=soul_mode,
        soul_version=soul_version,
        soul_version_hash=soul_hash,
        is_local=is_local,
        hive_config=NodeHiveConfig(
            enabled=hive_enabled,
            max_concurrent_agents=max_agents,
            uab_enabled=uab_enabled,
        ),
        budget_config=NodeBudgetConfig(daily_ceiling_usd=budget_usd),
        connection_status=connection_status,
    )


def _edge(source="node-a", target="node-b", edge_id="edge-1"):
    return GraphEdge(
        edge_id=edge_id,
        source_node_id=source,
        target_node_id=target,
    )


# ═══════════════════════════════════════════════════════════════
# Trust Tier
# ═══════════════════════════════════════════════════════════════

class TestTrustTier:
    def test_green_both_have_identity(self):
        result = evaluate_trust_tier(_node(), _node(node_id="b", pub_key="pubkey-b"))
        assert result.state == DimensionState.GREEN

    def test_red_source_no_identity(self):
        src = _node(pub_key="")
        result = evaluate_trust_tier(src, _node(node_id="b"))
        assert result.state == DimensionState.RED
        assert "no federation identity" in result.report.lower()

    def test_red_target_no_identity(self):
        tgt = _node(node_id="b", pub_key="")
        result = evaluate_trust_tier(_node(), tgt)
        assert result.state == DimensionState.RED

    def test_red_target_no_endpoint(self):
        tgt = _node(node_id="b", endpoint="")
        result = evaluate_trust_tier(_node(), tgt)
        assert result.state == DimensionState.RED
        assert "endpoint" in result.report.lower()

    def test_yellow_source_leaf(self):
        src = _node(role=InstanceRole.LEAF)
        result = evaluate_trust_tier(src, _node(node_id="b"))
        assert result.state == DimensionState.YELLOW

    def test_yellow_target_leaf(self):
        tgt = _node(node_id="b", role=InstanceRole.LEAF)
        result = evaluate_trust_tier(_node(), tgt)
        assert result.state == DimensionState.YELLOW

    def test_local_node_no_identity_ok(self):
        src = _node(pub_key="", is_local=True)
        result = evaluate_trust_tier(src, _node(node_id="b"))
        assert result.state != DimensionState.RED

    def test_local_target_no_endpoint_ok(self):
        tgt = _node(node_id="b", endpoint="", is_local=True)
        result = evaluate_trust_tier(_node(), tgt)
        assert result.state != DimensionState.RED


# ═══════════════════════════════════════════════════════════════
# Content Type
# ═══════════════════════════════════════════════════════════════

class TestContentType:
    def test_green_inherited(self):
        tgt = _node(node_id="b", soul_mode=SoulSourceMode.INHERITED)
        result = evaluate_content_type(_node(), tgt, _edge())
        assert result.state == DimensionState.GREEN

    def test_green_linked_matching_hash(self):
        src = _node(soul_hash="abc123", soul_mode=SoulSourceMode.LINKED)
        tgt = _node(node_id="b", soul_hash="abc123", soul_mode=SoulSourceMode.LINKED)
        result = evaluate_content_type(src, tgt, _edge())
        assert result.state == DimensionState.GREEN

    def test_yellow_linked_different_hash(self):
        src = _node(soul_hash="abc123", soul_mode=SoulSourceMode.LINKED)
        tgt = _node(node_id="b", soul_hash="def456", soul_mode=SoulSourceMode.LINKED)
        result = evaluate_content_type(src, tgt, _edge())
        assert result.state == DimensionState.YELLOW

    def test_yellow_linked_missing_hash(self):
        tgt = _node(node_id="b", soul_hash="", soul_mode=SoulSourceMode.LINKED)
        result = evaluate_content_type(_node(), tgt, _edge())
        assert result.state == DimensionState.YELLOW

    def test_green_both_custom_with_versions(self):
        result = evaluate_content_type(
            _node(soul_version="v1"),
            _node(node_id="b", soul_version="v1"),
            _edge(),
        )
        assert result.state == DimensionState.GREEN

    def test_yellow_custom_missing_version(self):
        result = evaluate_content_type(
            _node(soul_version="v1"),
            _node(node_id="b", soul_version=""),
            _edge(),
        )
        assert result.state == DimensionState.YELLOW


# ═══════════════════════════════════════════════════════════════
# Risk Tier
# ═══════════════════════════════════════════════════════════════

class TestRiskTier:
    def test_green_aligned(self):
        result = evaluate_risk_tier(_node(), _node(node_id="b"), _edge())
        assert result.state == DimensionState.GREEN

    def test_yellow_uab_mismatch(self):
        src = _node(hive_enabled=True, uab_enabled=False)
        tgt = _node(node_id="b", hive_enabled=True, uab_enabled=True)
        result = evaluate_risk_tier(src, tgt, _edge())
        assert result.state == DimensionState.YELLOW

    def test_red_agent_amplification(self):
        src = _node(hive_enabled=True, max_agents=5)
        tgt = _node(node_id="b", hive_enabled=True, max_agents=50)
        result = evaluate_risk_tier(src, tgt, _edge())
        assert result.state == DimensionState.RED
        assert "amplification" in result.report.lower()

    def test_red_budget_amplification(self):
        src = _node(budget_usd=10.0)
        tgt = _node(node_id="b", budget_usd=100.0)
        result = evaluate_risk_tier(src, tgt, _edge())
        assert result.state == DimensionState.RED
        assert "cost amplification" in result.report.lower()

    def test_yellow_child_to_root(self):
        src = _node(role=InstanceRole.CHILD)
        tgt = _node(node_id="b", role=InstanceRole.ROOT)
        edge = GraphEdge(
            edge_id="e1",
            source_node_id="node-a",
            target_node_id="node-b",
            relationship_type=RelationshipType.HIERARCHICAL_PARENT_CHILD,
        )
        result = evaluate_risk_tier(src, tgt, edge)
        assert result.state == DimensionState.YELLOW


# ═══════════════════════════════════════════════════════════════
# Capability Dependency
# ═══════════════════════════════════════════════════════════════

class TestCapabilityDependency:
    def test_green_all_satisfied(self):
        result = evaluate_capability_dependency(_node(), _node(node_id="b"), _edge())
        assert result.state == DimensionState.GREEN

    def test_yellow_hive_mismatch(self):
        src = _node(hive_enabled=True)
        tgt = _node(node_id="b", hive_enabled=False)
        result = evaluate_capability_dependency(src, tgt, _edge())
        assert result.state == DimensionState.YELLOW

    def test_red_target_offline(self):
        tgt = _node(node_id="b", connection_status="grey")
        result = evaluate_capability_dependency(_node(), tgt, _edge())
        assert result.state == DimensionState.RED

    def test_red_no_endpoint_for_handoff(self):
        tgt = _node(node_id="b", endpoint="")
        edge = GraphEdge(
            edge_id="e1",
            source_node_id="node-a",
            target_node_id="node-b",
            relationship_type=RelationshipType.FEDERATED_HANDOFF,
        )
        result = evaluate_capability_dependency(_node(), tgt, edge)
        assert result.state == DimensionState.RED


# ═══════════════════════════════════════════════════════════════
# Edge & Topology Validation
# ═══════════════════════════════════════════════════════════════

class TestEdgeValidation:
    def test_all_green(self):
        state, dims = validate_edge(_node(), _node(node_id="b"), _edge())
        assert state == EdgeState.GREEN
        assert len(dims) == 4

    def test_worst_case_red(self):
        tgt = _node(node_id="b", pub_key="")  # RED trust
        state, dims = validate_edge(_node(), tgt, _edge())
        assert state == EdgeState.RED

    def test_yellow_propagates(self):
        src = _node(role=InstanceRole.LEAF)
        state, dims = validate_edge(src, _node(node_id="b"), _edge())
        assert state == EdgeState.YELLOW


class TestTopologyValidation:
    def test_validate_all_edges(self):
        topo = TopologyDocument(
            topology_id="t1",
            nodes=[_node(), _node(node_id="node-b", name="B", pub_key="pk-b")],
            edges=[_edge()],
        )
        results = validate_topology(topo)
        assert "edge-1" in results
        state, dims = results["edge-1"]
        assert state == EdgeState.GREEN

    def test_missing_node_red(self):
        topo = TopologyDocument(
            topology_id="t1",
            nodes=[_node()],
            edges=[_edge()],  # target node-b missing
        )
        results = validate_topology(topo)
        state, _ = results["edge-1"]
        assert state == EdgeState.RED

    def test_empty_topology(self):
        topo = TopologyDocument(topology_id="t1")
        results = validate_topology(topo)
        assert results == {}


# ═══════════════════════════════════════════════════════════════
# Deployment Gate
# ═══════════════════════════════════════════════════════════════

class TestDeploymentGate:
    def test_standalone_deployable(self):
        topo = TopologyDocument(topology_id="t1")
        gate = check_deployment_gate(topo)
        assert gate.deployable
        assert "standalone" in gate.report.lower()

    def test_all_green_deployable(self):
        topo = TopologyDocument(
            topology_id="t1",
            nodes=[_node(), _node(node_id="node-b", name="B", pub_key="pk-b")],
            edges=[_edge()],
        )
        gate = check_deployment_gate(topo)
        assert gate.deployable
        assert len(gate.blocking_edges) == 0

    def test_red_blocks_deployment(self):
        topo = TopologyDocument(
            topology_id="t1",
            nodes=[_node(), _node(node_id="node-b", pub_key="")],  # RED trust
            edges=[_edge()],
        )
        gate = check_deployment_gate(topo)
        assert not gate.deployable
        assert "edge-1" in gate.blocking_edges

    def test_unacked_yellow_blocks(self):
        src = _node(role=InstanceRole.LEAF)  # YELLOW trust
        tgt = _node(node_id="node-b", name="B", pub_key="pk-b")
        topo = TopologyDocument(
            topology_id="t1",
            nodes=[src, tgt],
            edges=[_edge()],
        )
        gate = check_deployment_gate(topo)
        assert not gate.deployable
        assert "edge-1" in gate.unacknowledged_yellows

    def test_acked_yellow_deployable(self):
        src = _node(role=InstanceRole.LEAF)  # YELLOW trust
        tgt = _node(node_id="node-b", name="B", pub_key="pk-b")
        edge = _edge()
        edge.yellow_acknowledgments = [
            YellowAcknowledgment(operator="admin", condition="leaf trust ok")
        ]
        topo = TopologyDocument(
            topology_id="t1",
            nodes=[src, tgt],
            edges=[edge],
        )
        gate = check_deployment_gate(topo)
        assert gate.deployable

    def test_gate_result_to_dict(self):
        gate = DeploymentGateResult(
            deployable=True,
            blocking_edges=[],
            warning_edges=[],
            unacknowledged_yellows=[],
            report="ok",
        )
        d = gate.to_dict()
        assert d["deployable"] is True
        assert d["report"] == "ok"
