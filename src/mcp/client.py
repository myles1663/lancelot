# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Protocol Client — HTTP+SSE transport for Model Context Protocol servers.

TRANSPORT RESTRICTION (SECURITY DECISION):
    This client supports HTTP+SSE transport ONLY. Stdio process spawning
    is explicitly excluded. In a containerized governance system, allowing
    an AI agent to spawn arbitrary host processes via stdio transport
    constitutes an unacceptable attack surface. The governance stack
    (Soul permissions, kill switches, network allowlist, argument screening)
    cannot meaningfully govern a subprocess that has direct host access.

    If a future MCP server only supports stdio, it must be wrapped in an
    HTTP+SSE adapter (sidecar container, proxy service) before Lancelot
    will connect to it. This is a deliberate security boundary, not a
    missing feature.

    — Decision documented for future reviewers and patent examination.

Protocol flow:
    1. Client connects to server's SSE endpoint
    2. Server sends capabilities (tools list, resources, etc.)
    3. Client sends JSON-RPC requests over HTTP POST
    4. Server responds with JSON-RPC results or streams via SSE

This client is STATELESS per invocation: the proxy opens a bounded HTTP
request, invokes one operation, and releases the connection. That keeps each
MCP call isolated under the current policy, allowlist, credential, and timeout
context instead of carrying long-lived transport state across governed actions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.outbound_http import assert_url_allowed

logger = logging.getLogger(__name__)


@dataclass
class MCPToolSpec:
    """Specification for a tool exposed by an MCP server."""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class MCPCallResult:
    """Result of an MCP tool invocation."""
    success: bool
    result: Any = None
    error: str = ""
    duration_ms: int = 0
    server_id: str = ""
    tool_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "success": self.success,
            "server_id": self.server_id,
            "tool_name": self.tool_name,
            "duration_ms": self.duration_ms,
        }
        if self.success:
            d["result"] = self.result
        else:
            d["error"] = self.error
        return d


class MCPClient:
    """HTTP+SSE client for MCP server communication.

    Handles JSON-RPC request/response over HTTP POST with optional
    SSE streaming for server-pushed events.

    The client does NOT perform any governance checks — that is the
    proxy's responsibility. The client is a pure transport layer.

    Args:
        endpoint: Base URL of the MCP server
        auth_headers: Pre-resolved auth headers (proxy resolves from vault)
        timeout_s: Request timeout in seconds
    """

    def __init__(
        self,
        endpoint: str,
        auth_headers: Optional[Dict[str, str]] = None,
        timeout_s: float = 30.0,
        network_interceptor=None,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._auth_headers = auth_headers or {}
        self._timeout_s = timeout_s
        self._request_id = 0
        self._network_interceptor = network_interceptor

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def list_tools(self) -> List[MCPToolSpec]:
        """Discover tools exposed by the MCP server.

        Sends a tools/list JSON-RPC request.

        Returns:
            List of MCPToolSpec objects.
        """
        import httpx

        request_body = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/list",
            "params": {},
        }

        try:
            assert_url_allowed(
                self._endpoint,
                component="MCP tools/list",
                network_interceptor=self._network_interceptor,
            )
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    self._endpoint,
                    json=request_body,
                    headers={
                        "Content-Type": "application/json",
                        **self._auth_headers,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as e:
            logger.error("MCP tools/list failed for %s: %s", self._endpoint, e)
            return []
        except Exception as e:
            logger.error("MCP tools/list error for %s: %s", self._endpoint, e)
            return []

        # Parse JSON-RPC response
        if "error" in data:
            logger.warning(
                "MCP tools/list returned error: %s", data["error"]
            )
            return []

        tools_data = data.get("result", {}).get("tools", [])
        return [
            MCPToolSpec(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools_data
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        server_id: str = "",
    ) -> MCPCallResult:
        """Invoke a tool on the MCP server.

        Sends a tools/call JSON-RPC request with the given arguments.

        Args:
            tool_name: Name of the tool to invoke
            arguments: Tool arguments (validated by proxy before calling)
            server_id: For result attribution

        Returns:
            MCPCallResult with success/failure and result data.
        """
        import httpx

        request_body = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        start_ms = int(time.time() * 1000)

        try:
            assert_url_allowed(
                self._endpoint,
                component="MCP tools/call",
                network_interceptor=self._network_interceptor,
            )
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    self._endpoint,
                    json=request_body,
                    headers={
                        "Content-Type": "application/json",
                        **self._auth_headers,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            duration = int(time.time() * 1000) - start_ms
            return MCPCallResult(
                success=False,
                error=f"Request timed out after {self._timeout_s}s",
                duration_ms=duration,
                server_id=server_id,
                tool_name=tool_name,
            )
        except httpx.HTTPError as e:
            duration = int(time.time() * 1000) - start_ms
            return MCPCallResult(
                success=False,
                error=f"HTTP error: {e}",
                duration_ms=duration,
                server_id=server_id,
                tool_name=tool_name,
            )
        except Exception as e:
            duration = int(time.time() * 1000) - start_ms
            return MCPCallResult(
                success=False,
                error=f"Connection error: {e}",
                duration_ms=duration,
                server_id=server_id,
                tool_name=tool_name,
            )

        duration = int(time.time() * 1000) - start_ms

        # Parse JSON-RPC response
        if "error" in data:
            err = data["error"]
            return MCPCallResult(
                success=False,
                error=f"MCP error {err.get('code', '?')}: {err.get('message', 'unknown')}",
                duration_ms=duration,
                server_id=server_id,
                tool_name=tool_name,
            )

        result = data.get("result", {})
        content = result.get("content", [])

        # Extract text content from MCP response format
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, dict):
                text_parts.append(str(item))

        return MCPCallResult(
            success=True,
            result="\n".join(text_parts) if text_parts else result,
            duration_ms=duration,
            server_id=server_id,
            tool_name=tool_name,
        )
