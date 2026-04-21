"""Tests for MCP network policy validation and fail-closed behavior."""

from types import SimpleNamespace

import pytest

from src.mcp.network_policy import MCPNetworkPolicy


class _Interceptor:
    def __init__(self, allowed: bool, allowed_urls=None):
        self.allowed = allowed
        self.allowed_urls = set(allowed_urls or [])
        self.seen = []

    def check_url(self, endpoint: str) -> bool:
        self.seen.append(endpoint)
        if self.allowed_urls:
            return endpoint in self.allowed_urls
        return self.allowed


def test_check_invocation_allowed_denies_without_interceptor():
    policy = MCPNetworkPolicy(network_interceptor=None)

    assert policy.check_invocation_allowed("https://example.com/mcp") is False


def test_check_invocation_allowed_delegates_to_interceptor():
    interceptor = _Interceptor(allowed=True)
    policy = MCPNetworkPolicy(network_interceptor=interceptor)

    assert policy.check_invocation_allowed("https://example.com/mcp") is True
    assert interceptor.seen == ["https://example.com/mcp"]


def test_validate_endpoint_allows_public_https(monkeypatch):
    monkeypatch.setattr("src.mcp.network_policy.socket.gethostbyname", lambda hostname: "93.184.216.34")
    policy = MCPNetworkPolicy()

    result = policy.validate_endpoint("https://example.com/mcp")

    assert result.valid is True
    assert result.domain == "example.com"
    assert result.violations == []


def test_validate_endpoint_allows_localhost_http_when_enabled():
    policy = MCPNetworkPolicy(require_https=True, allow_localhost=True)

    result = policy.validate_endpoint("http://localhost:8765/mcp")

    assert result.valid is True
    assert result.domain == "localhost"


def test_validate_endpoint_rejects_localhost_http_when_disabled():
    policy = MCPNetworkPolicy(require_https=True, allow_localhost=False)

    result = policy.validate_endpoint("http://localhost:8765/mcp")

    assert result.valid is False
    assert any("HTTP not permitted" in violation for violation in result.violations)


def test_validate_endpoint_rejects_remote_http(monkeypatch):
    monkeypatch.setattr("src.mcp.network_policy.socket.gethostbyname", lambda hostname: "93.184.216.34")
    policy = MCPNetworkPolicy(require_https=True)

    result = policy.validate_endpoint("http://example.com/mcp")

    assert result.valid is False
    assert any("HTTP not permitted for remote endpoints" in violation for violation in result.violations)


def test_validate_endpoint_rejects_embedded_credentials(monkeypatch):
    monkeypatch.setattr("src.mcp.network_policy.socket.gethostbyname", lambda hostname: "93.184.216.34")
    policy = MCPNetworkPolicy()

    result = policy.validate_endpoint("https://user:pass@example.com/mcp")

    assert result.valid is False
    assert any("embedded credentials" in violation for violation in result.violations)


def test_validate_endpoint_rejects_blocked_metadata_domains():
    policy = MCPNetworkPolicy()

    result = policy.validate_endpoint("https://metadata.google.internal/computeMetadata/v1")

    assert result.valid is False
    assert any("blocked metadata service" in violation for violation in result.violations)


def test_validate_endpoint_rejects_private_ip_resolution(monkeypatch):
    monkeypatch.setattr("src.mcp.network_policy.socket.gethostbyname", lambda hostname: "10.0.0.7")
    policy = MCPNetworkPolicy()

    result = policy.validate_endpoint("https://internal.example/mcp")

    assert result.valid is False
    assert any("private/internal IP" in violation for violation in result.violations)


def test_extract_domains_returns_hostname():
    policy = MCPNetworkPolicy()

    assert policy.extract_domains("https://example.com:8443/mcp") == ["example.com"]


def test_get_missing_domains_only_returns_domains_not_allowed():
    interceptor = _Interceptor(
        allowed=False,
        allowed_urls={"https://allowed.example/test"},
    )
    policy = MCPNetworkPolicy(network_interceptor=interceptor)

    missing = policy.get_missing_domains(["allowed.example", "blocked.example"])

    assert missing == ["blocked.example"]
    assert interceptor.seen == [
        "https://allowed.example/test",
        "https://blocked.example/test",
    ]


def test_propose_allowlist_additions_uses_endpoint_and_network_domains():
    interceptor = _Interceptor(
        allowed=False,
        allowed_urls={"https://listed.example/test"},
    )
    policy = MCPNetworkPolicy(network_interceptor=interceptor)
    server_configs = [
        SimpleNamespace(
            endpoint="https://endpoint.example/mcp",
            network_domains=["listed.example", "missing.example"],
        )
    ]

    missing = sorted(policy.propose_allowlist_additions(server_configs))

    assert missing == ["endpoint.example", "missing.example"]
