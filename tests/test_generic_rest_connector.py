"""
Tests for Prompt 40: GenericRESTConnector.

Tests dynamic operation generation and strict input validation.
"""

import pytest
from src.connectors.connectors.generic_rest import GenericRESTConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


def _valid_config(**overrides):
    """Create a valid GenericREST config with optional overrides."""
    defaults = {
        "id": "myapi",
        "name": "My API",
        "base_url": "https://api.example.com",
        "auth_type": "bearer",
        "auth_vault_key": "myapi.token",
        "endpoints": [
            {"path": "/api/v1/users", "method": "GET", "name": "List Users"},
            {"path": "/api/v1/users", "method": "POST", "name": "Create User", "default_tier": 3},
            {"path": "/api/v1/users/{id}", "method": "GET", "name": "Get User"},
        ],
    }
    defaults.update(overrides)
    return defaults


# ── Valid Config ──────────────────────────────────────────────────

class TestValidConfig:
    def test_creates_valid_manifest(self):
        conn = GenericRESTConnector(_valid_config())
        conn.manifest.validate()

    def test_target_domains_extracted(self):
        conn = GenericRESTConnector(_valid_config())
        assert conn.manifest.target_domains == ["api.example.com"]

    def test_operations_generated(self):
        conn = GenericRESTConnector(_valid_config())
        ops = conn.get_operations()
        assert len(ops) == 3

    def test_get_defaults_t2(self):
        config = _valid_config(endpoints=[
            {"path": "/items", "method": "GET", "name": "List"},
        ])
        conn = GenericRESTConnector(config)
        assert conn.get_operations()[0].default_tier == RiskTier.T2_CONTROLLED

    def test_post_defaults_t3(self):
        config = _valid_config(endpoints=[
            {"path": "/items", "method": "POST", "name": "Create"},
        ])
        conn = GenericRESTConnector(config)
        assert conn.get_operations()[0].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_execute_builds_correct_url(self):
        conn = GenericRESTConnector(_valid_config())
        ops = conn.get_operations()
        result = conn.execute(ops[0].id, {})
        assert result.url == "https://api.example.com/api/v1/users"

    def test_path_params_substituted(self):
        conn = GenericRESTConnector(_valid_config())
        ops = conn.get_operations()
        get_user_op = [o for o in ops if "id" in o.id][0]
        result = conn.execute(get_user_op.id, {"id": "123"})
        assert "/users/123" in result.url

    def test_credential_vault_key_set(self):
        conn = GenericRESTConnector(_valid_config())
        ops = conn.get_operations()
        result = conn.execute(ops[0].id, {})
        assert result.credential_vault_key == "myapi.token"


# ── Missing Fields ────────────────────────────────────────────────

class TestMissingFields:
    def test_missing_base_url(self):
        with pytest.raises(ValueError, match="base_url is required"):
            GenericRESTConnector(_valid_config(base_url=""))

    def test_empty_endpoints(self):
        with pytest.raises(ValueError, match="endpoints must not be empty"):
            GenericRESTConnector(_valid_config(endpoints=[]))


# ── Input Validation ──────────────────────────────────────────────

class TestInputValidation:
    def test_http_rejected(self):
        with pytest.raises(ValueError, match="must start with https"):
            GenericRESTConnector(_valid_config(base_url="http://api.example.com"))

    def test_localhost_rejected(self):
        with pytest.raises(ValueError, match="private/localhost"):
            GenericRESTConnector(_valid_config(base_url="https://localhost/api"))

    def test_private_ip_rejected(self):
        with pytest.raises(ValueError, match="private/localhost"):
            GenericRESTConnector(_valid_config(base_url="https://127.0.0.1/api"))

    def test_private_10_network_rejected(self):
        with pytest.raises(ValueError, match="private/localhost"):
            GenericRESTConnector(_valid_config(base_url="https://10.0.0.1/api"))

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="path traversal"):
            GenericRESTConnector(_valid_config(endpoints=[
                {"path": "/../../../etc/passwd", "method": "GET", "name": "Evil"},
            ]))

    def test_invalid_auth_type_rejected(self):
        with pytest.raises(ValueError, match="auth_type must be one of"):
            GenericRESTConnector(_valid_config(auth_type="magic"))

    def test_more_than_50_endpoints_rejected(self):
        endpoints = [{"path": f"/ep{i}", "method": "GET", "name": f"EP{i}"} for i in range(51)]
        with pytest.raises(ValueError, match="max 50"):
            GenericRESTConnector(_valid_config(endpoints=endpoints))

    def test_param_with_special_chars_rejected(self):
        conn = GenericRESTConnector(_valid_config())
        ops = conn.get_operations()
        with pytest.raises(ValueError, match="invalid param name"):
            conn.execute(ops[0].id, {"param; DROP TABLE": "bad"})

    def test_wildcard_base_url_rejected(self):
        with pytest.raises(ValueError, match="wildcard"):
            GenericRESTConnector(_valid_config(base_url="https://*.example.com/api"))
