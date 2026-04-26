# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
MCP Server Registry — Vault-backed configuration store for MCP servers.

Every MCP server that Lancelot can connect to must be registered here.
The registry stores connection parameters, maps to Credential Vault keys
(never raw secrets), and tracks lifecycle state.

Credentials are NEVER stored in the registry itself. The registry holds
a vault_key reference that the proxy resolves at invocation time through
the CredentialVault with scoped access policy checks. This ensures:

    1. Credentials are encrypted at rest (Fernet/PBKDF2)
    2. Credentials are never in the agent's response path
    3. Access is policy-scoped per MCP server ID
    4. Vault audit trail tracks every credential retrieval

Transport restriction:
    Current transport support is HTTP+SSE only. No stdio process spawning.
    See client.py header comment for the security rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from src.mcp.permissions import MCPRiskTier

logger = logging.getLogger(__name__)


class MCPTransport(str, Enum):
    """Supported MCP transport protocols.

    HTTP+SSE only. Stdio is explicitly excluded because
    spawning host processes inside a governed container is an
    unacceptable attack surface.
    """
    HTTP_SSE = "http_sse"
    # STDIO = "stdio"  — intentionally excluded. See module docstring.


class MCPAuthType(str, Enum):
    """Authentication methods for MCP server connections."""
    NONE = "none"
    API_KEY = "api_key"         # Bearer token or X-API-Key header
    OAUTH2 = "oauth2"          # OAuth 2.0 client credentials or auth code
    BASIC = "basic"            # HTTP Basic Auth (user:pass)
    CUSTOM_HEADER = "custom_header"  # Arbitrary header name + vault value


class MCPServerStatus(str, Enum):
    """Lifecycle status of a registered MCP server."""
    REGISTERED = "registered"   # Config stored, not yet validated
    VALIDATED = "validated"     # Credentials and endpoint verified
    ACTIVE = "active"           # Ready for tool invocations
    SUSPENDED = "suspended"     # Operator or kill-switch disabled
    ERROR = "error"             # Connection or auth failure


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server.

    Credentials are referenced by vault_key, never stored directly.
    The proxy resolves credentials through the Credential Vault at
    invocation time with access policy enforcement.
    """
    server_id: str
    name: str
    endpoint: str               # Base URL (e.g., "https://mcp.example.com/sse")
    transport: MCPTransport = MCPTransport.HTTP_SSE
    auth_type: MCPAuthType = MCPAuthType.NONE
    vault_key: str = ""         # Credential Vault key for auth secret
    auth_header: str = ""       # Custom header name (for CUSTOM_HEADER auth_type)
    default_risk_tier: MCPRiskTier = MCPRiskTier.T2
    kill_switch_id: str = ""    # Per-server kill switch identifier
    network_domains: List[str] = field(default_factory=list)  # Domains for allowlist
    status: MCPServerStatus = MCPServerStatus.REGISTERED
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_validated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for storage. NEVER includes raw credentials."""
        return {
            "server_id": self.server_id,
            "name": self.name,
            "endpoint": self.endpoint,
            "transport": self.transport.value,
            "auth_type": self.auth_type.value,
            "vault_key": self.vault_key,
            "auth_header": self.auth_header,
            "default_risk_tier": self.default_risk_tier.value,
            "kill_switch_id": self.kill_switch_id,
            "network_domains": self.network_domains,
            "status": self.status.value,
            "registered_at": self.registered_at,
            "last_validated_at": self.last_validated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MCPServerConfig:
        """Deserialize from stored config."""
        return cls(
            server_id=data["server_id"],
            name=data.get("name", data["server_id"]),
            endpoint=data.get("endpoint", ""),
            transport=MCPTransport(data.get("transport", "http_sse")),
            auth_type=MCPAuthType(data.get("auth_type", "none")),
            vault_key=data.get("vault_key", ""),
            auth_header=data.get("auth_header", ""),
            default_risk_tier=MCPRiskTier(data.get("default_risk_tier", "T2")),
            kill_switch_id=data.get("kill_switch_id", ""),
            network_domains=data.get("network_domains", []),
            status=MCPServerStatus(data.get("status", "registered")),
            registered_at=data.get("registered_at", ""),
            last_validated_at=data.get("last_validated_at", ""),
            metadata=data.get("metadata", {}),
        )

    def safe_summary(self) -> Dict[str, Any]:
        """Summary for API responses — no vault keys or credentials."""
        return {
            "server_id": self.server_id,
            "name": self.name,
            "endpoint": self.endpoint,
            "transport": self.transport.value,
            "auth_type": self.auth_type.value,
            "has_credentials": bool(self.vault_key),
            "default_risk_tier": self.default_risk_tier.value,
            "status": self.status.value,
            "network_domains": self.network_domains,
        }


class MCPServerRegistry:
    """Registry of MCP server configurations.

    Stores server connection parameters and references to Credential Vault
    keys. The registry itself never holds raw secrets — it holds vault_key
    references that are resolved through the CredentialVault at invocation
    time with access policy enforcement.

    The registry is backed by the Credential Vault for persistence:
    server configs are stored as a single encrypted vault entry.
    """

    # Vault key where registry config blob is stored
    _REGISTRY_VAULT_KEY = "mcp_server_registry"
    _REGISTRY_VAULT_TYPE = "mcp_config"

    def __init__(self, vault=None):
        """
        Args:
            vault: CredentialVault instance for encrypted persistence
                   and credential resolution. If None, registry is
                   in-memory only (for testing).
        """
        self._vault = vault
        self._servers: Dict[str, MCPServerConfig] = {}
        self._load()

    def _load(self) -> None:
        """Load server configs from Credential Vault."""
        if not self._vault:
            return
        try:
            import json
            raw = self._vault.retrieve(self._REGISTRY_VAULT_KEY)
            data = json.loads(raw)
            for server_data in data.get("servers", []):
                config = MCPServerConfig.from_dict(server_data)
                self._servers[config.server_id] = config
            logger.info(
                "MCP registry loaded: %d server(s) from vault",
                len(self._servers),
            )
        except KeyError:
            # No registry stored yet — normal on first run
            pass
        except Exception as e:
            logger.error("Failed to load MCP registry from vault: %s", e)

    def _save(self) -> None:
        """Persist server configs to Credential Vault."""
        if not self._vault:
            return
        try:
            import json
            data = {
                "servers": [s.to_dict() for s in self._servers.values()],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._vault.store(
                key=self._REGISTRY_VAULT_KEY,
                value=json.dumps(data),
                type=self._REGISTRY_VAULT_TYPE,
            )
        except Exception as e:
            logger.error("Failed to save MCP registry to vault: %s", e)

    def register(self, config: MCPServerConfig) -> None:
        """Register or update an MCP server configuration.

        If a vault_key is specified, grants access policy so the MCP
        proxy can retrieve credentials at invocation time.

        Raises:
            ValueError: If server_id is empty or endpoint is missing.
        """
        if not config.server_id:
            raise ValueError("server_id is required")
        if not config.endpoint:
            raise ValueError("endpoint is required for HTTP+SSE transport")

        # Auto-generate kill switch ID if not set
        if not config.kill_switch_id:
            config.kill_switch_id = f"MCP_SERVER_{config.server_id.upper()}"

        self._servers[config.server_id] = config
        self._save()

        # Grant vault access policy for the MCP proxy accessor
        if self._vault and config.vault_key:
            self._vault.access_policy.grant(
                f"mcp:{config.server_id}", config.vault_key
            )

        logger.info(
            "MCP server registered: %s (%s, auth=%s, tier=%s)",
            config.server_id, config.endpoint,
            config.auth_type.value, config.default_risk_tier.value,
        )

    def unregister(self, server_id: str) -> bool:
        """Remove an MCP server. Returns True if found and removed."""
        config = self._servers.pop(server_id, None)
        if config is None:
            return False

        # Revoke vault access
        if self._vault and config.vault_key:
            self._vault.access_policy.revoke(
                f"mcp:{server_id}", config.vault_key
            )

        self._save()
        logger.info("MCP server unregistered: %s", server_id)
        return True

    def get(self, server_id: str) -> Optional[MCPServerConfig]:
        """Get a server config by ID. Returns None if not found."""
        return self._servers.get(server_id)

    def list_servers(self) -> List[MCPServerConfig]:
        """List all registered servers."""
        return list(self._servers.values())

    def list_active_servers(self) -> List[MCPServerConfig]:
        """List servers in ACTIVE or VALIDATED status."""
        return [
            s for s in self._servers.values()
            if s.status in (MCPServerStatus.ACTIVE, MCPServerStatus.VALIDATED)
        ]

    def set_status(
        self, server_id: str, status: MCPServerStatus
    ) -> bool:
        """Update a server's lifecycle status. Returns False if not found."""
        config = self._servers.get(server_id)
        if config is None:
            return False
        config.status = status
        if status == MCPServerStatus.VALIDATED:
            config.last_validated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return True

    def get_network_domains(self) -> Set[str]:
        """Collect all network domains from active MCP servers.

        These domains must be in the NetworkInterceptor allowlist for
        MCP connections to succeed.
        """
        domains: Set[str] = set()
        for server in self._servers.values():
            if server.status != MCPServerStatus.SUSPENDED:
                domains.update(server.network_domains)
        return domains

    def resolve_credential(
        self, server_id: str,
    ) -> Optional[str]:
        """Resolve the credential for an MCP server from the Vault.

        Uses scoped access policy: each server's credential is only
        accessible with the accessor ID "mcp:<server_id>".

        Returns None if no vault_key configured or vault unavailable.
        Raises PermissionError if access policy denies retrieval.
        Raises KeyError if vault_key doesn't exist in vault.
        """
        config = self._servers.get(server_id)
        if config is None or not config.vault_key:
            return None
        if not self._vault:
            logger.warning(
                "Cannot resolve credential for %s: no vault configured",
                server_id,
            )
            return None
        return self._vault.retrieve(
            config.vault_key, accessor_id=f"mcp:{server_id}"
        )
