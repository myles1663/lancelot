# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Graph Builder API — REST endpoints for the federation topology editor.

Provides CRUD for topology documents, node/edge management, validation,
and deployment gate checking. Mounted under /api/federation/graph/.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity

from src.federation.graph_models import (
    GraphEdge,
    GraphNode,
    HandoffContract,
    TopologyDocument,
    YellowAcknowledgment,
)
from src.federation.graph_persistence import TopologyStore
from src.federation.graph_validator import (
    check_deployment_gate,
    validate_edge,
    validate_topology,
)

logger = logging.getLogger(__name__)

graph_router = APIRouter(
    prefix="/api/federation/graph",
    tags=["graph-builder"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("federation.admin")),
    ],
)

# Module-level store — initialized by init_graph_api()
_store: Optional[TopologyStore] = None


def init_graph_api(data_dir: str) -> TopologyStore:
    """Initialize the graph API with a data directory."""
    global _store
    _store = TopologyStore(data_dir)
    logger.info("Graph Builder API initialized (data_dir=%s)", data_dir)
    return _store


def _get_store() -> TopologyStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Graph Builder not initialized")
    return _store


# ═══════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════

class CreateTopologyRequest(BaseModel):
    topology_name: str = "New Topology"


class AddNodeRequest(BaseModel):
    node_id: str
    instance_name: str = ""
    endpoint: str = ""
    federation_identity_public_key: str = ""
    fingerprint: str = ""
    instance_role: str = "peer"
    soul_source_mode: str = "custom"
    soul_version: str = ""
    soul_version_hash: str = ""
    hive_enabled: bool = False
    hive_max_agents: int = 10
    hive_uab_enabled: bool = False
    budget_daily_ceiling_usd: float = 10.0
    position_x: float = 0.0
    position_y: float = 0.0
    is_local: bool = False


class AddEdgeRequest(BaseModel):
    source_node_id: str
    target_node_id: str
    relationship_type: str = "federated_handoff"
    trigger_condition: str = "always"
    priority: int = 0


class AcknowledgeYellowRequest(BaseModel):
    condition: str = ""
    note: str = ""


class UpdateContractRequest(BaseModel):
    success_criteria: List[str] = Field(default_factory=list)
    data_payload_schema: Dict[str, Any] = Field(default_factory=dict)
    soul_context_constraints: Dict[str, Any] = Field(default_factory=dict)
    template_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# Topology CRUD
# ═══════════════════════════════════════════════════════════════

@graph_router.post("/topologies")
async def create_topology(req: CreateTopologyRequest, request: Request):
    """Create a new empty topology document."""
    store = _get_store()
    identity = resolve_authenticated_identity(request)
    created_by = identity.display_name or identity.operator_id or "operator"
    topo = TopologyDocument(
        topology_id=str(uuid.uuid4()),
        topology_name=req.topology_name,
        created_by=created_by,
    )
    saved = store.save(topo)
    return {"topology_id": saved.topology_id, "version": saved.version}


@graph_router.get("/topologies/active")
async def get_active_topology():
    """Get the current active topology."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")
    return topo.model_dump(mode="json")


@graph_router.delete("/topologies/active")
async def delete_active_topology():
    """Delete the active topology."""
    store = _get_store()
    if store.delete_active():
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="No active topology")


@graph_router.get("/topologies/versions")
async def list_topology_versions():
    """List all saved topology versions."""
    store = _get_store()
    return {"versions": store.list_versions()}


@graph_router.get("/topologies/versions/{version}")
async def get_topology_version(version: int):
    """Get a specific topology version."""
    store = _get_store()
    topo = store.load_version(version)
    if not topo:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    return topo.model_dump(mode="json")


@graph_router.post("/topologies/active/save-version")
async def save_topology_version():
    """Save the active topology as a versioned snapshot."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")
    version = store.save_version(topo)
    return {"version": version, "version_hash": topo.version_hash}


# ═══════════════════════════════════════════════════════════════
# Node Operations
# ═══════════════════════════════════════════════════════════════

@graph_router.post("/nodes")
async def add_node(req: AddNodeRequest):
    """Add a node to the active topology."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    # Check for duplicate
    if topo.get_node(req.node_id):
        raise HTTPException(status_code=409, detail=f"Node '{req.node_id}' already exists")

    from src.federation.graph_models import (
        InstanceRole,
        NodeBudgetConfig,
        NodeHiveConfig,
        NodePosition,
        SoulSourceMode,
    )

    node = GraphNode(
        node_id=req.node_id,
        instance_name=req.instance_name,
        endpoint=req.endpoint,
        federation_identity_public_key=req.federation_identity_public_key,
        fingerprint=req.fingerprint,
        instance_role=InstanceRole(req.instance_role),
        soul_source_mode=SoulSourceMode(req.soul_source_mode),
        soul_version=req.soul_version,
        soul_version_hash=req.soul_version_hash,
        hive_config=NodeHiveConfig(
            enabled=req.hive_enabled,
            max_concurrent_agents=req.hive_max_agents,
            uab_enabled=req.hive_uab_enabled,
        ),
        budget_config=NodeBudgetConfig(
            daily_ceiling_usd=req.budget_daily_ceiling_usd,
        ),
        position=NodePosition(x=req.position_x, y=req.position_y),
        is_local=req.is_local,
    )
    topo.nodes.append(node)
    topo.deployment_mode = topo.detect_deployment_mode()
    store.save(topo)

    return {"node_id": node.node_id, "node_count": len(topo.nodes)}


@graph_router.delete("/nodes/{node_id}")
async def remove_node(node_id: str):
    """Remove a node and all connected edges."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    node = topo.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    # Remove connected edges
    removed_edges = [
        e.edge_id for e in topo.edges
        if e.source_node_id == node_id or e.target_node_id == node_id
    ]
    topo.edges = [
        e for e in topo.edges
        if e.source_node_id != node_id and e.target_node_id != node_id
    ]
    topo.nodes = [n for n in topo.nodes if n.node_id != node_id]
    topo.deployment_mode = topo.detect_deployment_mode()
    store.save(topo)

    return {
        "removed_node": node_id,
        "removed_edges": removed_edges,
        "node_count": len(topo.nodes),
    }


@graph_router.put("/nodes/{node_id}")
async def update_node(node_id: str, updates: Dict[str, Any]):
    """Update node fields."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    node = topo.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    # Apply allowed updates
    allowed = {
        "instance_name", "endpoint", "federation_identity_public_key",
        "fingerprint", "instance_role", "soul_source_mode", "soul_version",
        "soul_version_hash", "connection_status", "timezone", "is_local",
    }
    for key, value in updates.items():
        if key in allowed and hasattr(node, key):
            setattr(node, key, value)

    topo.deployment_mode = topo.detect_deployment_mode()
    store.save(topo)

    return {"updated": node_id}


# ═══════════════════════════════════════════════════════════════
# Edge Operations
# ═══════════════════════════════════════════════════════════════

@graph_router.post("/edges")
async def add_edge(req: AddEdgeRequest):
    """Add an edge between two nodes."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    # Validate nodes exist
    source = topo.get_node(req.source_node_id)
    target = topo.get_node(req.target_node_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source node '{req.source_node_id}' not found")
    if not target:
        raise HTTPException(status_code=404, detail=f"Target node '{req.target_node_id}' not found")

    # Self-loop check
    if req.source_node_id == req.target_node_id:
        raise HTTPException(status_code=400, detail="Self-loops not allowed")

    from src.federation.graph_models import RelationshipType, TriggerCondition

    edge_id = f"edge-{uuid.uuid4().hex[:8]}"
    edge = GraphEdge(
        edge_id=edge_id,
        source_node_id=req.source_node_id,
        target_node_id=req.target_node_id,
        relationship_type=RelationshipType(req.relationship_type),
        trigger_condition=TriggerCondition(req.trigger_condition),
        priority=req.priority,
    )

    # Auto-validate on creation
    state, dims = validate_edge(source, target, edge)
    edge.compatibility_state = state
    edge.dimension_results = dims

    topo.edges.append(edge)
    store.save(topo)

    return {
        "edge_id": edge_id,
        "compatibility_state": state.value,
        "edge_count": len(topo.edges),
    }


@graph_router.delete("/edges/{edge_id}")
async def remove_edge(edge_id: str):
    """Remove an edge."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    original_count = len(topo.edges)
    topo.edges = [e for e in topo.edges if e.edge_id != edge_id]
    if len(topo.edges) == original_count:
        raise HTTPException(status_code=404, detail=f"Edge '{edge_id}' not found")

    store.save(topo)
    return {"removed_edge": edge_id, "edge_count": len(topo.edges)}


# ═══════════════════════════════════════════════════════════════
# Validation & Deployment Gate
# ═══════════════════════════════════════════════════════════════

@graph_router.post("/validate")
async def validate_active_topology():
    """Run 4-dimension validation on all edges in the active topology."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    results = validate_topology(topo)

    # Update edges with validation results
    for edge in topo.edges:
        if edge.edge_id in results:
            state, dims = results[edge.edge_id]
            edge.compatibility_state = state
            edge.dimension_results = dims

    store.save(topo)

    # Build response
    edge_results = {}
    for edge_id, (state, dims) in results.items():
        edge_results[edge_id] = {
            "state": state.value,
            "dimensions": [d.model_dump(mode="json") for d in dims],
        }

    return {
        "edge_count": len(topo.edges),
        "results": edge_results,
    }


@graph_router.post("/validate/edge/{edge_id}")
async def validate_single_edge(edge_id: str):
    """Run 4-dimension validation on a single edge."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    edge = next((e for e in topo.edges if e.edge_id == edge_id), None)
    if not edge:
        raise HTTPException(status_code=404, detail=f"Edge '{edge_id}' not found")

    source = topo.get_node(edge.source_node_id)
    target = topo.get_node(edge.target_node_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Missing source or target node")

    state, dims = validate_edge(source, target, edge)
    edge.compatibility_state = state
    edge.dimension_results = dims
    store.save(topo)

    return {
        "edge_id": edge_id,
        "state": state.value,
        "dimensions": [d.model_dump(mode="json") for d in dims],
    }


@graph_router.post("/edges/{edge_id}/acknowledge")
async def acknowledge_yellow(edge_id: str, req: AcknowledgeYellowRequest, request: Request):
    """Acknowledge a YELLOW edge condition (operator sign-off)."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    edge = next((e for e in topo.edges if e.edge_id == edge_id), None)
    if not edge:
        raise HTTPException(status_code=404, detail=f"Edge '{edge_id}' not found")

    identity = resolve_authenticated_identity(request)
    operator = identity.display_name or identity.operator_id or "operator"
    ack = YellowAcknowledgment(
        operator=operator,
        condition=req.condition,
        note=req.note,
    )
    edge.yellow_acknowledgments.append(ack)
    store.save(topo)

    return {"acknowledged": True, "edge_id": edge_id}


@graph_router.put("/edges/{edge_id}/contract")
async def update_handoff_contract(edge_id: str, req: UpdateContractRequest):
    """Update the handoff contract on an edge."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    edge = next((e for e in topo.edges if e.edge_id == edge_id), None)
    if not edge:
        raise HTTPException(status_code=404, detail=f"Edge '{edge_id}' not found")

    edge.handoff_contract.success_criteria = req.success_criteria
    edge.handoff_contract.data_payload_schema = req.data_payload_schema
    edge.handoff_contract.soul_context_constraints = req.soul_context_constraints
    edge.handoff_contract.template_id = req.template_id
    store.save(topo)

    return {"updated": True, "edge_id": edge_id}


@graph_router.post("/deployment-gate")
async def check_deployment():
    """Check if the active topology passes the deployment gate."""
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    gate = check_deployment_gate(topo)
    return gate.to_dict()


@graph_router.post("/deploy")
async def deploy_topology():
    """Deploy the active topology (saves as deployed snapshot).

    Only succeeds if the deployment gate passes.
    """
    store = _get_store()
    topo = store.load()
    if not topo:
        raise HTTPException(status_code=404, detail="No active topology")

    gate = check_deployment_gate(topo)
    if not gate.deployable:
        raise HTTPException(
            status_code=400,
            detail=f"Deployment blocked: {gate.report}",
        )

    # Bump version and deploy
    topo.version += 1
    deployed = store.save_deployed(topo)

    # Update active with new version
    store.save(deployed)

    return {
        "deployed": True,
        "version": deployed.version,
        "version_hash": deployed.version_hash,
        "deployed_at": deployed.deployed_at,
    }
