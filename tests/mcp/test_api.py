from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.core import api_auth, auth_api
from src.core.operator_identity import OperatorIdentity
from src.mcp import api as mcp_api
from src.mcp.api import router
from src.mcp.registry import (
    MCPAuthType,
    MCPRiskTier,
    MCPServerConfig,
    MCPServerRegistry,
    MCPServerStatus,
    MCPTransport,
)


class _Registry(MCPServerRegistry):
    def __init__(self):
        super().__init__(vault=None)
        self.register(
            MCPServerConfig(
                server_id="srv-1",
                name="Server 1",
                endpoint="https://example.com/mcp",
                transport=MCPTransport.HTTP_SSE,
                auth_type=MCPAuthType.NONE,
                default_risk_tier=MCPRiskTier.T2,
                status=MCPServerStatus.ACTIVE,
            )
        )


def _set_admin_session():
    auth_api._sessions.clear()
    auth_api._sessions["mcp-test-session"] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-10T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": sorted({"warroom.login", "mcp.admin"}),
        "groups": [],
    }


def _build_client():
    api_auth.init_api_auth(lambda request: True)
    _set_admin_session()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "mcp-test-session")
    return client


def test_list_servers_degrades_when_feature_enabled_but_registry_missing(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    mcp_api.init_mcp_api(
        registry=None,
        evaluator=None,
        proxy=None,
        vault=None,
        network_policy=None,
        receipt_service=None,
    )
    client = _build_client()

    resp = client.get("/api/mcp/servers")

    assert resp.status_code == 200
    data = resp.json()
    assert data["feature_enabled"] is True
    assert data["runtime_degraded"] is True
    assert data["registry_ready"] is False
    assert any("registry not initialized" in reason.lower() for reason in data["degraded_reasons"])


def test_list_servers_degrades_when_registry_status_fails(monkeypatch):
    class _BrokenRegistry:
        def list_servers(self):
            raise RuntimeError("registry exploded")

    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    mcp_api.init_mcp_api(
        registry=_BrokenRegistry(),
        evaluator=object(),
        proxy=object(),
        vault=object(),
        network_policy=object(),
        receipt_service=None,
    )
    client = _build_client()

    resp = client.get("/api/mcp/servers")

    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_degraded"] is True
    assert any("registry status unavailable" in reason.lower() for reason in data["degraded_reasons"])
    assert any("registry exploded" in err.lower() for err in data["runtime_errors"])


def test_list_servers_returns_clean_runtime_state_when_initialized(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    mcp_api.init_mcp_api(
        registry=_Registry(),
        evaluator=object(),
        proxy=object(),
        vault=object(),
        network_policy=object(),
        receipt_service=None,
    )
    client = _build_client()

    resp = client.get("/api/mcp/servers")

    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_degraded"] is False
    assert data["registry_ready"] is True
    assert data["evaluator_ready"] is True
    assert data["proxy_ready"] is True
    assert data["total"] == 1


def test_server_detail_exposes_canonical_kill_switch_contract(monkeypatch):
    class _Evaluator:
        def check_server_access(self, server_id):
            return type("Perm", (), {"allowed": True})()

    class _NetworkPolicy:
        def check_invocation_allowed(self, endpoint):
            return True

    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    mcp_api.init_mcp_api(
        registry=_Registry(),
        evaluator=_Evaluator(),
        proxy=object(),
        vault=object(),
        network_policy=_NetworkPolicy(),
        receipt_service=None,
    )
    client = _build_client()

    resp = client.get("/api/mcp/servers/srv-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kill_switch_active"] is True
    assert data["kill_switch"]["allowed"] is True
    assert data["kill_switch"]["scope"] in {"mcp_master", "mcp_server"}


def test_register_server_rejects_unexpected_fields(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    mcp_api.init_mcp_api(
        registry=_Registry(),
        evaluator=object(),
        proxy=object(),
        vault=object(),
        network_policy=object(),
        receipt_service=None,
    )
    client = _build_client()

    resp = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "srv-2",
            "name": "Server 2",
            "endpoint": "https://example.com/second",
            "unexpected": "deny-me",
        },
    )

    assert resp.status_code == 422


def test_update_mcp_soul_handles_none_model_dump_dict_and_shutdown():
    calls = []

    class _Evaluator:
        def load_permissions(self, permissions, soul_version=""):
            calls.append(("permissions", permissions, soul_version))

        def load_from_soul(self, payload):
            calls.append(("soul", payload))

    mcp_api.init_mcp_api(
        registry=object(),
        evaluator=_Evaluator(),
        proxy=object(),
        vault=object(),
        network_policy=object(),
        receipt_service=object(),
    )

    mcp_api.update_mcp_soul(None)
    mcp_api.update_mcp_soul(type("Soul", (), {"model_dump": lambda self: {"version": "model"}})())
    mcp_api.update_mcp_soul(type("Soul", (), {"dict": lambda self: {"version": "dict"}})())
    mcp_api.update_mcp_soul({"version": "mapping"})
    mcp_api.shutdown_mcp_api()
    mcp_api.update_mcp_soul({"version": "ignored"})

    assert calls == [
        ("permissions", [], ""),
        ("soul", {"version": "model"}),
        ("soul", {"version": "dict"}),
        ("soul", {"version": "mapping"}),
    ]


def test_register_remove_and_status_lifecycle(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    registry = MCPServerRegistry(vault=None)

    class _NetworkPolicy:
        def validate_endpoint(self, endpoint):
            return type("Validation", (), {"valid": True, "violations": []})()

    mcp_api.init_mcp_api(
        registry=registry,
        evaluator=object(),
        proxy=object(),
        vault=object(),
        network_policy=_NetworkPolicy(),
        receipt_service=None,
    )
    client = _build_client()

    registered = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "srv-2",
            "name": "Server 2",
            "endpoint": "https://example.com/second",
            "auth_type": "bearer",
            "default_risk_tier": "T3",
            "network_domains": ["example.com"],
        },
    )
    status = client.post("/api/mcp/servers/srv-2/status", json={"status": "active"})
    removed = client.delete("/api/mcp/servers/srv-2")

    assert registered.json()["registered"] is True
    assert status.json() == {"server_id": "srv-2", "status": "active"}
    assert removed.json() == {"removed": True}
    assert client.delete("/api/mcp/servers/srv-2").status_code == 404


def test_register_and_status_validate_bad_inputs(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)

    class _NetworkPolicy:
        def validate_endpoint(self, endpoint):
            return type("Validation", (), {"valid": False, "violations": ["blocked"]})()

    mcp_api.init_mcp_api(
        registry=MCPServerRegistry(vault=None),
        evaluator=object(),
        proxy=object(),
        vault=object(),
        network_policy=_NetworkPolicy(),
        receipt_service=None,
    )
    client = _build_client()

    blocked = client.post(
        "/api/mcp/servers",
        json={"server_id": "srv-2", "name": "Server 2", "endpoint": "http://blocked"},
    )
    bad_status = client.post("/api/mcp/servers/srv-2/status", json={"status": "not-real"})

    assert blocked.status_code == 400
    assert "Endpoint validation failed" in blocked.json()["detail"]
    assert bad_status.status_code == 400


def test_test_server_success_failure_and_proxy_missing(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    registry = _Registry()

    class _Tool:
        def to_dict(self):
            return {"name": "tool-1"}

    class _Proxy:
        async def list_server_tools(self, server_id):
            return [_Tool()]

    mcp_api.init_mcp_api(
        registry=registry,
        evaluator=object(),
        proxy=_Proxy(),
        vault=object(),
        network_policy=object(),
        receipt_service=None,
    )
    client = _build_client()

    success = client.post("/api/mcp/servers/srv-1/test")
    assert success.json()["success"] is True
    assert success.json()["tool_count"] == 1

    class _FailingProxy:
        async def list_server_tools(self, server_id):
            raise RuntimeError("connection failed")

    mcp_api.init_mcp_api(
        registry=registry,
        evaluator=object(),
        proxy=_FailingProxy(),
        vault=object(),
        network_policy=object(),
        receipt_service=None,
    )
    assert client.post("/api/mcp/servers/srv-1/test").json()["success"] is False

    mcp_api.init_mcp_api(
        registry=registry,
        evaluator=object(),
        proxy=None,
        vault=object(),
        network_policy=object(),
        receipt_service=None,
    )
    assert client.post("/api/mcp/servers/srv-1/test").status_code == 503
    assert client.post("/api/mcp/servers/missing/test").status_code == 404


def test_store_credential_updates_config_and_grants_access(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    registry = _Registry()
    stored = {}
    grants = []
    vault = type(
        "Vault",
        (),
        {
            "access_policy": type("Policy", (), {"grant": lambda self, accessor, key: grants.append((accessor, key))})(),
            "store": lambda self, key, value, type="api_key": stored.update({key: (value, type)}),
        },
    )()
    mcp_api.init_mcp_api(
        registry=registry,
        evaluator=object(),
        proxy=object(),
        vault=vault,
        network_policy=object(),
        receipt_service=None,
    )
    client = _build_client()

    response = client.post(
        "/api/mcp/servers/srv-1/credential",
        json={"vault_key": "", "value": "secret", "type": "bearer"},
    )

    assert response.json() == {"stored": True}
    assert stored == {"mcp.srv-1": ("secret", "bearer")}
    assert grants == [("mcp:srv-1", "mcp.srv-1")]
    assert registry.get("srv-1").vault_key == "mcp.srv-1"


def test_store_credential_handles_missing_server_and_vault(monkeypatch):
    monkeypatch.setattr("feature_flags.FEATURE_MCP", True, raising=False)
    mcp_api.init_mcp_api(
        registry=_Registry(),
        evaluator=object(),
        proxy=object(),
        vault=None,
        network_policy=object(),
        receipt_service=None,
    )
    client = _build_client()

    assert client.post("/api/mcp/servers/missing/credential", json={"vault_key": "k", "value": "v"}).status_code == 404
    assert client.post("/api/mcp/servers/srv-1/credential", json={"vault_key": "k", "value": "v"}).status_code == 503


def test_receipt_summary_counts_blocked_gates_and_handles_errors():
    call = type(
        "Receipt",
        (),
        {
            "metadata": {"mcp_server_id": "srv-1", "mcp_tool_name": "list"},
            "status": "success",
            "timestamp": "2026-04-20T01:00:00Z",
        },
    )()
    blocked = type(
        "Receipt",
        (),
        {
            "metadata": {"block_gate": "network", "mcp_server_id": "srv-1", "mcp_tool_name": "write"},
            "status": "blocked",
            "timestamp": "2026-04-20T02:00:00Z",
        },
    )()

    class _ReceiptService:
        def search(self, action_type, limit):
            return [call] if action_type == "mcp_tool_call" else [blocked]

    mcp_api.init_mcp_api(receipt_service=_ReceiptService())
    client = _build_client()

    summary = client.get("/api/mcp/receipts/summary").json()
    assert summary["total_calls"] == 1
    assert summary["total_blocked"] == 1
    assert summary["block_gates"] == {"network": 1}
    assert summary["recent_calls"][0]["status"] == "blocked"

    class _FailingReceiptService:
        def search(self, action_type, limit):
            raise RuntimeError("no receipts")

    mcp_api.init_mcp_api(receipt_service=_FailingReceiptService())
    assert client.get("/api/mcp/receipts/summary").json()["total_calls"] == 0
