import json

import pytest

from src.mcp.permissions import MCPRiskTier
from src.mcp.registry import (
    MCPAuthType,
    MCPServerConfig,
    MCPServerRegistry,
    MCPServerStatus,
)


class _Policy:
    def __init__(self):
        self.grants = []
        self.revokes = []

    def grant(self, accessor, key):
        self.grants.append((accessor, key))

    def revoke(self, accessor, key):
        self.revokes.append((accessor, key))


class _Vault:
    def __init__(self, raw=None, fail_store=False):
        self.raw = raw
        self.fail_store = fail_store
        self.stored = []
        self.access_policy = _Policy()
        self.secrets = {"secret-key": "secret-value"}

    def retrieve(self, key, accessor_id=""):
        if key == "mcp_server_registry":
            if self.raw is None:
                raise KeyError(key)
            return self.raw
        return self.secrets[key]

    def store(self, key, value, type="config"):
        if self.fail_store:
            raise RuntimeError("store failed")
        self.stored.append((key, json.loads(value), type))


def _config(server_id="srv", **kwargs):
    return MCPServerConfig(
        server_id=server_id,
        name=kwargs.pop("name", "Server"),
        endpoint=kwargs.pop("endpoint", "https://mcp.example.com/sse"),
        vault_key=kwargs.pop("vault_key", "secret-key"),
        network_domains=kwargs.pop("network_domains", ["mcp.example.com"]),
        **kwargs,
    )


def test_config_roundtrip_and_safe_summary_redacts_vault_key():
    config = _config(
        auth_type=MCPAuthType.CUSTOM_HEADER,
        auth_header="X-Token",
        default_risk_tier=MCPRiskTier.T3,
        status=MCPServerStatus.ACTIVE,
        metadata={"owner": "ops"},
    )

    restored = MCPServerConfig.from_dict(config.to_dict())
    summary = restored.safe_summary()

    assert restored.auth_type is MCPAuthType.CUSTOM_HEADER
    assert restored.default_risk_tier is MCPRiskTier.T3
    assert summary["has_credentials"] is True
    assert "vault_key" not in summary


def test_registry_loads_from_vault_and_handles_missing_or_bad_payload(caplog):
    raw = json.dumps({"servers": [_config(server_id="loaded", status=MCPServerStatus.VALIDATED).to_dict()]})
    loaded = MCPServerRegistry(vault=_Vault(raw=raw))
    missing = MCPServerRegistry(vault=_Vault(raw=None))

    assert loaded.get("loaded").status is MCPServerStatus.VALIDATED
    assert missing.list_servers() == []

    with caplog.at_level("ERROR"):
        bad = MCPServerRegistry(vault=_Vault(raw="{bad json"))

    assert bad.list_servers() == []
    assert "Failed to load MCP registry from vault" in caplog.text


def test_register_validates_persists_grants_access_and_can_update_status():
    vault = _Vault(raw=None)
    registry = MCPServerRegistry(vault=vault)

    with pytest.raises(ValueError, match="server_id"):
        registry.register(_config(server_id=""))
    with pytest.raises(ValueError, match="endpoint"):
        registry.register(_config(endpoint=""))

    registry.register(_config(server_id="srv"))
    assert registry.get("srv").kill_switch_id == "MCP_SERVER_SRV"
    assert vault.access_policy.grants == [("mcp:srv", "secret-key")]
    assert vault.stored[-1][2] == "mcp_config"

    assert registry.set_status("missing", MCPServerStatus.ACTIVE) is False
    assert registry.set_status("srv", MCPServerStatus.VALIDATED) is True
    assert registry.get("srv").last_validated_at


def test_unregister_lists_domains_and_resolves_credentials(caplog):
    vault = _Vault(raw=None)
    registry = MCPServerRegistry(vault=vault)
    registry.register(_config(server_id="active", status=MCPServerStatus.ACTIVE))
    registry.register(_config(server_id="suspended", status=MCPServerStatus.SUSPENDED, network_domains=["blocked.example"]))
    registry.register(_config(server_id="no_secret", vault_key=""))

    assert [server.server_id for server in registry.list_active_servers()] == ["active"]
    assert registry.get_network_domains() == {"mcp.example.com"}
    assert registry.resolve_credential("active") == "secret-value"
    assert registry.resolve_credential("missing") is None
    assert registry.resolve_credential("no_secret") is None

    no_vault = MCPServerRegistry(vault=None)
    no_vault.register(_config(server_id="srv"))
    with caplog.at_level("WARNING"):
        assert no_vault.resolve_credential("srv") is None
    assert "no vault configured" in caplog.text

    assert registry.unregister("missing") is False
    assert registry.unregister("active") is True
    assert vault.access_policy.revokes == [("mcp:active", "secret-key")]


def test_save_errors_are_logged(caplog):
    registry = MCPServerRegistry(vault=_Vault(raw=None, fail_store=True))

    with caplog.at_level("ERROR"):
        registry.register(_config(server_id="srv"))

    assert "Failed to save MCP registry to vault" in caplog.text
