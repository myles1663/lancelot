"""
Tests for Prompt 31: ConnectorProxy Core (Sync).

Uses mocked requests.Session to avoid real network calls.
Tests domain validation, rate limiting, and credential injection.
"""

import os
import pytest
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet

from src.connectors.base import ConnectorBase, ConnectorManifest, ConnectorStatus, CredentialSpec
from src.connectors.models import ConnectorOperation, ConnectorResult, HTTPMethod
from src.connectors.proxy import ConnectorProxy, DomainValidator
from src.connectors.rate_limiter import RateLimiterRegistry
from src.connectors.registry import ConnectorRegistry
from src.connectors.vault import CredentialVault
from src.core import feature_flags


# ── Test Helpers ──────────────────────────────────────────────────

class _TestConnector(ConnectorBase):
    def __init__(self, manifest):
        super().__init__(manifest)

    def get_operations(self):
        return [
            ConnectorOperation(
                id="read",
                connector_id=self.manifest.id,
                capability="connector.read",
                name="Read",
            )
        ]

    def execute(self, operation_id, params):
        return ConnectorResult(
            operation_id=operation_id,
            connector_id=self.manifest.id,
            method=HTTPMethod.GET,
            url=f"https://{self.manifest.target_domains[0]}/api",
        )

    def validate_credentials(self):
        return True


@pytest.fixture(autouse=True)
def enable_connectors():
    old = os.environ.get("FEATURE_CONNECTORS")
    os.environ["FEATURE_CONNECTORS"] = "true"
    feature_flags.reload_flags()
    yield
    if old is None:
        os.environ.pop("FEATURE_CONNECTORS", None)
    else:
        os.environ["FEATURE_CONNECTORS"] = old
    feature_flags.reload_flags()


@pytest.fixture
def vault(tmp_path):
    import yaml
    key = Fernet.generate_key().decode()
    os.environ["LANCELOT_VAULT_KEY"] = key
    config = {
        "version": "1.0",
        "storage": {
            "path": str(tmp_path / "cred.enc"),
            "backup_path": str(tmp_path / "cred.enc.bak"),
        },
        "encryption": {"key_env_var": "LANCELOT_VAULT_KEY"},
        "audit": {"log_access": False},
    }
    cfg_path = tmp_path / "vault.yaml"
    with open(cfg_path, "w") as f:
        yaml.dump(config, f)
    v = CredentialVault(config_path=str(cfg_path))
    yield v
    os.environ.pop("LANCELOT_VAULT_KEY", None)


@pytest.fixture
def registry():
    return ConnectorRegistry("config/connectors.yaml")


@pytest.fixture
def slack_connector():
    manifest = ConnectorManifest(
        id="slack",
        name="Slack",
        version="1.0.0",
        author="lancelot",
        source="first-party",
        target_domains=["api.slack.com"],
        required_credentials=[
            CredentialSpec(name="Bot Token", type="oauth_token", vault_key="slack_token"),
        ],
    )
    return _TestConnector(manifest)


# ── DomainValidator ───────────────────────────────────────────────

class TestDomainValidator:
    def test_extract_domain_https(self):
        assert DomainValidator.extract_domain("https://api.slack.com/chat") == "api.slack.com"

    def test_extract_domain_localhost(self):
        assert DomainValidator.extract_domain("http://localhost:8080/api") == "localhost"

    def test_is_domain_allowed_exact_match(self):
        assert DomainValidator.is_domain_allowed(
            "https://api.slack.com/msg", ["api.slack.com"]
        ) is True

    def test_is_domain_allowed_rejects_other(self):
        assert DomainValidator.is_domain_allowed(
            "https://evil.com/api", ["api.slack.com"]
        ) is False

    def test_is_domain_allowed_rejects_subdomain(self):
        assert DomainValidator.is_domain_allowed(
            "https://sub.api.slack.com/", ["api.slack.com"]
        ) is False


# ── ConnectorProxy ────────────────────────────────────────────────

class TestConnectorProxy:
    def test_initializes(self, registry, vault):
        proxy = ConnectorProxy(registry, vault)
        assert proxy.request_count == 0

    def test_undeclared_domain_returns_error(self, registry, vault, slack_connector):
        registry.register(slack_connector)
        proxy = ConnectorProxy(registry, vault)

        result = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="https://evil.com/steal-data",
        )
        resp = proxy.execute(result)
        assert resp.success is False
        assert "not in allowed domains" in resp.error

    def test_rate_limit_returns_429(self, registry, vault, slack_connector):
        registry.register(slack_connector)
        rate_config = {
            "default": {"max_requests_per_minute": 60, "burst_limit": 1},
        }
        rate_reg = RateLimiterRegistry(rate_config)
        proxy = ConnectorProxy(registry, vault, rate_limiter_registry=rate_reg)

        result = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="https://api.slack.com/api/test",
        )
        # First request consumes the single token
        with patch.object(proxy._session, "request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True}
            mock_resp.headers = {}
            mock_req.return_value = mock_resp
            proxy.execute(result)

        # Second should be rate limited
        resp = proxy.execute(result)
        assert resp.status_code == 429
        assert resp.success is False
        assert "Rate limited" in resp.error

    def test_request_count_starts_zero(self, registry, vault):
        proxy = ConnectorProxy(registry, vault)
        assert proxy.request_count == 0

    @patch("src.connectors.proxy.requests.Session.request")
    def test_credential_injection_oauth(self, mock_req, registry, vault, slack_connector):
        # Store credential and grant access
        vault.store("slack_token", "xoxb-real-token", type="oauth_token")
        vault.grant_connector_access("slack", slack_connector.manifest)
        registry.register(slack_connector)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_req.return_value = mock_resp

        proxy = ConnectorProxy(registry, vault)
        result = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="https://api.slack.com/api/conversations.list",
            credential_vault_key="slack_token",
        )
        resp = proxy.execute(result)

        assert resp.success is True
        # Verify Bearer token was injected
        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers.get("Authorization") == "Bearer xoxb-real-token"

    @patch("src.connectors.proxy.requests.Session.request")
    def test_credential_injection_api_key(self, mock_req, registry, vault, slack_connector):
        vault.store("slack_api_key", "sk-12345", type="api_key")
        vault.access_policy.grant("slack", "slack_api_key")
        registry.register(slack_connector)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_resp.headers = {}
        mock_req.return_value = mock_resp

        proxy = ConnectorProxy(registry, vault)
        result = ConnectorResult(
            operation_id="read",
            connector_id="slack",
            method=HTTPMethod.GET,
            url="https://api.slack.com/api/test",
            credential_vault_key="slack_api_key",
        )
        resp = proxy.execute(result)

        assert resp.success is True
        call_kwargs = mock_req.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers.get("X-API-Key") == "sk-12345"

    def test_unknown_connector_returns_error(self, registry, vault):
        proxy = ConnectorProxy(registry, vault)
        result = ConnectorResult(
            operation_id="read",
            connector_id="nonexistent",
            method=HTTPMethod.GET,
            url="https://example.com",
        )
        resp = proxy.execute(result)
        assert resp.success is False
        assert "not found" in resp.error
