from fastapi import FastAPI
from fastapi.testclient import TestClient

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
