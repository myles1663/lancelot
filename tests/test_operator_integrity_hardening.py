import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth
from src.core import auth_api
from src.core.operator_identity import OperatorIdentity


def _insert_session(token: str, capabilities: set[str], *, display_name: str = "Arthur", operator_id: str = "op-arthur", session_id: str = "session-1") -> None:
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": display_name,
        "operator_identity": OperatorIdentity(
            operator_id=operator_id,
            display_name=display_name,
            session_id=session_id,
            session_started_at="2026-04-10T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": sorted(capabilities),
        "groups": [],
    }


def _client_for_router(module_name: str):
    module = importlib.import_module(module_name)
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    router = getattr(module, "router", None) or getattr(module, "graph_router")
    app.include_router(router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "limited-session")
    return client


@pytest.mark.parametrize(
    ("module_name", "method", "path", "json_body"),
    [
        ("src.core.governance_api", "post", "/api/governance/approvals/nonexistent/approve", {}),
        ("src.core.trust_api", "post", "/api/trust/proposals/nonexistent/approve", {}),
        ("src.core.apl_api", "post", "/api/apl/proposals/nonexistent/activate", {}),
        ("src.core.skills_api", "get", "/api/skills", None),
        ("src.core.connectors_api", "get", "/api/connectors", None),
        ("src.connectors.credential_api", "get", "/connectors/email/credentials/status", None),
        ("src.observability.api", "get", "/api/observability/config", None),
        ("src.incidents.api", "get", "/api/incidents/stats", None),
        ("src.federation.api", "post", "/api/federation/manage/register-peer", {"target_address": "http://example.invalid"}),
        ("src.federation.graph_api", "post", "/api/federation/graph/topologies", {"topology_name": "Denied"}),
        ("src.hive.api", "get", "/api/hive/status", None),
        ("src.compliance.api", "get", "/api/compliance/history", None),
    ],
)
def test_additional_admin_routers_require_capability(module_name, method, path, json_body):
    auth_api._sessions.clear()
    _insert_session("limited-session", {"warroom.login"})
    client = _client_for_router(module_name)

    if json_body is None:
        response = getattr(client, method)(path)
    else:
        response = getattr(client, method)(path, json=json_body)

    assert response.status_code == 403


def test_federation_graph_created_by_uses_authenticated_operator(monkeypatch, tmp_path):
    from src.federation import graph_api

    auth_api._sessions.clear()
    _insert_session("federation-admin", {"warroom.login", "federation.admin"})
    api_auth.init_api_auth(lambda request: True)
    graph_api.init_graph_api(str(tmp_path))

    app = FastAPI()
    app.include_router(graph_api.graph_router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "federation-admin")

    response = client.post(
        "/api/federation/graph/topologies",
        json={"topology_name": "Audit Graph", "created_by": "Mallory"},
    )

    assert response.status_code == 200

    active = client.get("/api/federation/graph/topologies/active")
    assert active.status_code == 200
    assert active.json()["created_by"] == "Arthur"


def test_federation_graph_yellow_ack_uses_authenticated_operator(tmp_path):
    from src.federation import graph_api

    auth_api._sessions.clear()
    _insert_session("federation-admin", {"warroom.login", "federation.admin"})
    api_auth.init_api_auth(lambda request: True)
    graph_api.init_graph_api(str(tmp_path))

    app = FastAPI()
    app.include_router(graph_api.graph_router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "federation-admin")

    client.post("/api/federation/graph/topologies", json={"topology_name": "Audit Graph"})
    client.post(
        "/api/federation/graph/nodes",
        json={"node_id": "node-a", "instance_role": "peer", "soul_source_mode": "custom"},
    )
    client.post(
        "/api/federation/graph/nodes",
        json={"node_id": "node-b", "instance_role": "peer", "soul_source_mode": "custom"},
    )
    edge_response = client.post(
        "/api/federation/graph/edges",
        json={"source_node_id": "node-a", "target_node_id": "node-b"},
    )
    edge_id = edge_response.json()["edge_id"]

    response = client.post(
        f"/api/federation/graph/edges/{edge_id}/acknowledge",
        json={"operator": "Mallory", "condition": "test", "note": "ack"},
    )

    assert response.status_code == 200

    active = client.get("/api/federation/graph/topologies/active")
    edge = active.json()["edges"][0]
    assert edge["yellow_acknowledgments"][0]["operator"] == "Arthur"


def test_incident_actor_identity_uses_authenticated_operator(monkeypatch):
    from src.incidents import api as incidents_api

    auth_api._sessions.clear()
    _insert_session("admin-session", {"warroom.login", "incidents.admin"})
    api_auth.init_api_auth(lambda request: True)

    class FakeIncident:
        def __init__(self):
            self.status = "OPEN"
            self.severity = "LOW"
            self.responder_id = None
            self.acknowledged_at = None
            self.timeline = []
            self.remediation_receipts = []
            self.board_report_generated = False
            self.root_cause = None
            self.closed_at = None
            self.closed_by = None

        def add_timeline_entry(self, entry):
            self.timeline.append(entry)

    class FakeStore:
        def update(self, incident):
            self.last_updated = incident

    incident = FakeIncident()
    store = FakeStore()
    emitted = {}

    monkeypatch.setattr(incidents_api, "get_incident_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(incidents_api, "_get_or_404", lambda _store, incident_id: incident)
    monkeypatch.setattr(
        incidents_api,
        "_emit_receipt",
        lambda action_type, metadata, operator_id="": emitted.update({"operator_id": operator_id, "metadata": metadata}),
    )

    app = FastAPI()
    app.include_router(incidents_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "admin-session")

    response = client.post("/api/incidents/inc-1/acknowledge", json={"operator_id": "Mallory"})

    assert response.status_code == 200
    assert incident.responder_id == "op-arthur"
    assert incident.timeline[0].actor == "Arthur"
    assert emitted["operator_id"] == "op-arthur"


def test_memory_commit_uses_authenticated_operator(monkeypatch, tmp_path):
    import src.core.memory.api as memory_api
    from src.core.memory.compiler import ContextCompilerService
    from src.core.memory.commits import CommitManager
    from src.core.memory.gates import QuarantineManager, WriteGateValidator
    from src.core.memory.index import MemoryIndex
    from src.core.memory.sqlite_store import MemoryStoreManager
    from src.core.memory.store import CoreBlockStore

    auth_api._sessions.clear()
    _insert_session("memory-admin", {"warroom.login", "memory.admin"})
    api_auth.init_api_auth(lambda request: True)

    core_store = CoreBlockStore(data_dir=tmp_path)
    core_store.initialize()
    store_manager = MemoryStoreManager(data_dir=tmp_path)
    service = {
        "core_store": core_store,
        "store_manager": store_manager,
        "commit_manager": CommitManager(core_store, store_manager, tmp_path),
        "gate_validator": WriteGateValidator(),
        "quarantine_manager": QuarantineManager(core_store, store_manager),
        "memory_index": MemoryIndex(store_manager),
        "compiler_service": ContextCompilerService(tmp_path),
    }

    app = FastAPI()
    app.include_router(memory_api.router)
    app.dependency_overrides[memory_api.get_memory_service] = lambda: service
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "memory-admin")

    response = client.post("/memory/commit/begin", json={"created_by": "Mallory", "message": "test commit"})

    assert response.status_code == 200
    commit_id = response.json()["commit_id"]
    staged = service["commit_manager"].get_staged_commit(commit_id)
    assert staged is not None
    assert staged.created_by == "Arthur"


def test_soul_template_apply_uses_authenticated_operator(monkeypatch):
    from src.core.soul import template_api

    auth_api._sessions.clear()
    _insert_session("soul-admin", {"warroom.login", "soul.admin"})
    api_auth.init_api_auth(lambda request: True)

    captured = {}

    def fake_apply_template(**kwargs):
        captured.update(kwargs)
        return {
            "template_name": kwargs["template_name"],
            "template_version": "1.0",
            "proposal_id": "prop-1",
            "proposed_version": "v2",
            "fields_customized": [],
            "diff_summary": [],
        }

    monkeypatch.setattr(template_api, "apply_template", fake_apply_template)
    monkeypatch.setattr(template_api, "_emit_template_receipt", lambda *args, **kwargs: None)

    app = FastAPI()
    app.include_router(template_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "soul-admin")

    response = client.post(
        "/soul/templates/test-template/apply",
        json={"operator_id": "Mallory", "session_id": "fake-session", "customizations": {"mode": "strict"}},
    )

    assert response.status_code == 422
    assert "extra_forbidden" in response.text
    assert captured == {}

    response = client.post(
        "/soul/templates/test-template/apply",
        json={"customizations": {"mode": "strict"}},
    )

    assert response.status_code == 200
    assert captured["operator_id"] == "op-arthur"
    assert captured["session_id"] == "session-1"


def test_skill_approval_uses_authenticated_operator():
    from src.core import skills_api

    auth_api._sessions.clear()
    _insert_session("skills-admin", {"warroom.login", "skills.admin"})
    api_auth.init_api_auth(lambda request: True)

    class FakeFactory:
        def __init__(self):
            self.approved_by = None

        def approve_proposal(self, proposal_id, approved_by):
            self.approved_by = approved_by
            return SimpleNamespace(id=proposal_id, name="demo-skill", approved_by=approved_by)

    skills_api._skill_factory = FakeFactory()

    app = FastAPI()
    app.include_router(skills_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "skills-admin")

    response = client.post("/api/skills/proposals/proposal-1/approve", json={"approved_by": "Mallory"})

    assert response.status_code == 200
    assert skills_api._skill_factory.approved_by == "Arthur"
