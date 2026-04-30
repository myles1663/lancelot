import pytest

from src.mcp.proxy import GovernedMCPProxy
from src.mcp.registry import MCPAuthType, MCPRiskTier, MCPServerConfig, MCPServerRegistry, MCPServerStatus
from src.mcp.receipts import MCPReceiptManager


class _ReceiptServiceStub:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


class _PermissionResult:
    allowed = True
    block_reason = ""

    class _Tier:
        value = "T2"

    risk_tier = _Tier()


class _PermissionEvaluator:
    soul_version = "soul-v1"

    def check_tool_access(self, server_id, tool_name):
        return _PermissionResult()

    def check_server_access(self, server_id):
        return _PermissionResult()


class _Registry(MCPServerRegistry):
    def __init__(self):
        super().__init__(vault=None)
        self.register(
            MCPServerConfig(
                server_id="srv-1",
                name="Server 1",
                endpoint="https://example.com/mcp",
                transport=None,
                auth_type=MCPAuthType.NONE,
                default_risk_tier=MCPRiskTier.T2,
                status=MCPServerStatus.ACTIVE,
            )
        )

    def resolve_credential(self, server_id):
        return None


class _ScreenResult:
    passed = True
    severity = "none"
    violations = []


class _ArgumentScreener:
    def screen(self, arguments, server_id=None, tool_name=None):
        return _ScreenResult()


class _ResponseGuard:
    class _GuardResult:
        clean = True
        redactions = []

    def inspect(self, result, server_id=None, tool_name=None):
        return result, self._GuardResult()


class _NetworkInterceptor:
    def check_url(self, endpoint):
        return True


@pytest.mark.asyncio
async def test_governed_mcp_proxy_propagates_operator_identity(monkeypatch):
    service = _ReceiptServiceStub()
    receipts = MCPReceiptManager(service)
    proxy = GovernedMCPProxy(
        permission_evaluator=_PermissionEvaluator(),
        registry=_Registry(),
        receipt_manager=receipts,
        argument_screener=_ArgumentScreener(),
        response_guard=_ResponseGuard(),
        network_interceptor=_NetworkInterceptor(),
    )

    class _ClientResult:
        success = True
        result = {"ok": True}
        error = ""
        duration_ms = 7
        server_id = "srv-1"
        tool_name = "lookup"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def call_tool(self, tool_name, arguments, server_id=None):
            return _ClientResult()

    monkeypatch.setattr("src.mcp.proxy.MCPClient", _Client)
    monkeypatch.setattr(
        "src.mcp.proxy.check_mcp_kill_switches",
        lambda kill_switch_id: type("KillResult", (), {"allowed": True, "reason": ""})(),
    )

    result = await proxy.invoke_tool(
        "srv-1",
        "lookup",
        {"query": "status"},
        quest_id="quest-1",
        parent_receipt_id="parent-1",
        operator_id="op-1",
        session_id="sess-1",
    )

    assert result.success is True
    assert service.calls
    receipt = service.calls[-1]
    assert receipt["operator_id"] == "op-1"
    assert receipt["session_id"] == "sess-1"
    assert receipt["quest_id"] == "quest-1"
    assert receipt["parent_id"] == "parent-1"
