from types import SimpleNamespace

import httpx
import pytest

from src.mcp.client import MCPCallResult, MCPClient, MCPToolSpec
from src.core.outbound_http import OutboundNetworkError


class _Response:
    def __init__(self, payload=None, *, status_error=None):
        self._payload = payload or {}
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


class _AsyncClient:
    def __init__(self, *, calls, handler, timeout):
        self._calls = calls
        self._handler = handler
        self._timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, endpoint, json, headers):
        self._calls.append({"endpoint": endpoint, "json": json, "headers": headers, "timeout": self._timeout})
        outcome = self._handler(endpoint=endpoint, json=json, headers=headers, timeout=self._timeout)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _patch_async_client(monkeypatch, handler):
    calls = []

    def _factory(*, timeout):
        return _AsyncClient(calls=calls, handler=handler, timeout=timeout)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return calls


@pytest.fixture(autouse=True)
def allow_outbound_requests(monkeypatch):
    monkeypatch.setattr("src.mcp.client.assert_url_allowed", lambda url, **kwargs: url)


@pytest.mark.asyncio
async def test_list_tools_success_maps_input_schema_and_increments_request_ids(monkeypatch):
    calls = _patch_async_client(
        monkeypatch,
        lambda **kwargs: _Response(
            {
                "result": {
                    "tools": [
                        {
                            "name": "lookup",
                            "description": "Fetch a record",
                            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                        }
                    ]
                }
            }
        ),
    )
    client = MCPClient("https://mcp.example.com/", auth_headers={"Authorization": "Bearer token"}, timeout_s=12.5)

    tools = await client.list_tools()
    more_tools = await client.list_tools()

    assert tools == [
        MCPToolSpec(
            name="lookup",
            description="Fetch a record",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
    ]
    assert more_tools[0].name == "lookup"
    assert calls[0]["endpoint"] == "https://mcp.example.com"
    assert calls[0]["json"]["id"] == 1
    assert calls[1]["json"]["id"] == 2
    assert calls[0]["headers"]["Authorization"] == "Bearer token"
    assert calls[0]["timeout"] == 12.5


@pytest.mark.asyncio
async def test_list_tools_returns_empty_on_http_and_rpc_errors(monkeypatch):
    request = httpx.Request("POST", "https://mcp.example.com")
    response = httpx.Response(502, request=request)
    _patch_async_client(
        monkeypatch,
        lambda **kwargs: _Response(status_error=httpx.HTTPStatusError("bad gateway", request=request, response=response)),
    )
    client = MCPClient("https://mcp.example.com")
    assert await client.list_tools() == []

    _patch_async_client(monkeypatch, lambda **kwargs: _Response({"error": {"code": -32000, "message": "bad"}}))
    assert await client.list_tools() == []


@pytest.mark.asyncio
async def test_list_tools_returns_empty_when_network_allowlist_blocks(monkeypatch):
    monkeypatch.setattr(
        "src.mcp.client.assert_url_allowed",
        lambda url, **kwargs: (_ for _ in ()).throw(
            OutboundNetworkError("MCP tools/list blocked by network allowlist")
        ),
    )
    client = MCPClient("https://mcp.example.com")

    assert await client.list_tools() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (httpx.TimeoutException("slow"), "Request timed out after 30.0s"),
        (httpx.ConnectError("boom"), "HTTP error: boom"),
        (RuntimeError("offline"), "Connection error: offline"),
    ],
)
async def test_call_tool_surfaces_transport_failures(monkeypatch, raised, expected):
    _patch_async_client(monkeypatch, lambda **kwargs: raised)
    client = MCPClient("https://mcp.example.com")

    result = await client.call_tool("lookup", {"q": "arthur"}, server_id="srv-1")

    assert result.success is False
    assert result.error == expected
    assert result.server_id == "srv-1"
    assert result.tool_name == "lookup"
    assert isinstance(result.duration_ms, int)


@pytest.mark.asyncio
async def test_call_tool_handles_rpc_errors_and_text_content(monkeypatch):
    _patch_async_client(
        monkeypatch,
        lambda **kwargs: _Response({"error": {"code": 403, "message": "denied"}}),
    )
    client = MCPClient("https://mcp.example.com")

    denied = await client.call_tool("delete", {"target": "tmp"}, server_id="srv-2")

    assert denied == MCPCallResult(
        success=False,
        error="MCP error 403: denied",
        duration_ms=denied.duration_ms,
        server_id="srv-2",
        tool_name="delete",
    )

    _patch_async_client(
        monkeypatch,
        lambda **kwargs: _Response(
            {
                "result": {
                    "content": [
                        {"type": "text", "text": "first line"},
                        {"type": "image", "mime": "image/png"},
                    ]
                }
            }
        ),
    )
    success = await client.call_tool("lookup", {"q": "arthur"}, server_id="srv-3")

    assert success.success is True
    assert success.result == "first line\n{'type': 'image', 'mime': 'image/png'}"
    assert success.server_id == "srv-3"
    assert success.tool_name == "lookup"


@pytest.mark.asyncio
async def test_call_tool_returns_raw_result_when_content_is_missing(monkeypatch):
    _patch_async_client(
        monkeypatch,
        lambda **kwargs: _Response({"result": {"structured": {"answer": 42}}}),
    )
    client = MCPClient("https://mcp.example.com")

    result = await client.call_tool("calc", {"value": 21})

    assert result.success is True
    assert result.result == {"structured": {"answer": 42}}
    assert result.to_dict()["result"] == {"structured": {"answer": 42}}


@pytest.mark.asyncio
async def test_call_tool_returns_failed_result_when_network_allowlist_blocks(monkeypatch):
    monkeypatch.setattr(
        "src.mcp.client.assert_url_allowed",
        lambda url, **kwargs: (_ for _ in ()).throw(
            OutboundNetworkError("MCP tools/call blocked by network allowlist")
        ),
    )
    client = MCPClient("https://mcp.example.com")

    result = await client.call_tool("lookup", {"q": "arthur"}, server_id="srv-4")

    assert result.success is False
    assert "network allowlist" in result.error


def test_mcp_dataclasses_to_dict_round_trip():
    spec = MCPToolSpec(name="lookup", description="Find data", input_schema={"type": "object"})
    result = MCPCallResult(success=False, error="boom", duration_ms=10, server_id="srv", tool_name="lookup")

    assert spec.to_dict() == {
        "name": "lookup",
        "description": "Find data",
        "input_schema": {"type": "object"},
    }
    assert result.to_dict() == {
        "success": False,
        "server_id": "srv",
        "tool_name": "lookup",
        "duration_ms": 10,
        "error": "boom",
    }
