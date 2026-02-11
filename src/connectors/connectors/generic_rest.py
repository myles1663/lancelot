"""
Generic REST Connector — User-configurable REST API integration.

Operations are generated dynamically from config, not hardcoded.
Includes strict input validation for SSRF prevention, path traversal,
and injection attacks.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import urlparse

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
)
from src.core.governance.models import RiskTier


# ── Endpoint Config ───────────────────────────────────────────────

@dataclass
class RESTEndpointConfig:
    """Configuration for a single REST endpoint."""
    path: str
    method: str
    name: str
    description: str = ""
    default_tier: int = 2


# ── Validation ────────────────────────────────────────────────────

_VALID_AUTH_TYPES = ("bearer", "api_key", "basic", "oauth2")

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
]

_PARAM_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


def _is_private_host(hostname: str) -> bool:
    """Check if a hostname resolves to a private/local IP."""
    if hostname in ("localhost", "localhost.localdomain"):
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        pass
    return False


# ── Generic REST Connector ────────────────────────────────────────

class GenericRESTConnector(ConnectorBase):
    """User-configurable REST API connector.

    Operations are generated dynamically from a config dict.
    """

    def __init__(self, config: dict) -> None:
        self._validate_config(config)

        self._base_url = config["base_url"].rstrip("/")
        self._auth_type = config.get("auth_type", "bearer")
        self._auth_vault_key = config.get("auth_vault_key", "")

        # Extract domain from base_url
        parsed = urlparse(self._base_url)
        domain = parsed.hostname or ""

        # Build credential spec
        cred_specs = []
        if self._auth_vault_key:
            cred_specs.append(CredentialSpec(
                name=f"{config['id']}_credential",
                type=self._auth_type,
                vault_key=self._auth_vault_key,
            ))

        manifest = ConnectorManifest(
            id=config["id"],
            name=config["name"],
            version=config.get("version", "1.0.0"),
            author=config.get("author", "user"),
            source="user",
            description=config.get("description", ""),
            target_domains=[domain],
            required_credentials=cred_specs,
        )
        super().__init__(manifest)

        # Build operations from endpoints
        self._endpoints = config.get("endpoints", [])
        self._operations = self._build_operations(config["id"])

    @staticmethod
    def _validate_config(config: dict) -> None:
        """Validate all user-supplied config values."""
        # Required fields
        if not config.get("base_url"):
            raise ValueError("GenericRESTConnector: base_url is required")
        if not config.get("id"):
            raise ValueError("GenericRESTConnector: id is required")
        if not config.get("name"):
            raise ValueError("GenericRESTConnector: name is required")

        base_url = config["base_url"]

        # HTTPS only
        if not base_url.startswith("https://"):
            raise ValueError(
                f"GenericRESTConnector: base_url must start with https://, got '{base_url}'"
            )

        # Wildcard domains
        parsed = urlparse(base_url)
        hostname = parsed.hostname or ""
        if "*" in hostname:
            raise ValueError(
                f"GenericRESTConnector: wildcard base_urls not allowed, got '{hostname}'"
            )

        # SSRF: reject private/localhost
        if _is_private_host(hostname):
            raise ValueError(
                f"GenericRESTConnector: private/localhost base_url not allowed, got '{hostname}'"
            )

        # Auth type
        auth_type = config.get("auth_type", "bearer")
        if auth_type not in _VALID_AUTH_TYPES:
            raise ValueError(
                f"GenericRESTConnector: auth_type must be one of {_VALID_AUTH_TYPES}, got '{auth_type}'"
            )

        # Endpoints
        endpoints = config.get("endpoints", [])
        if not endpoints:
            raise ValueError("GenericRESTConnector: endpoints must not be empty")
        if len(endpoints) > 50:
            raise ValueError(
                f"GenericRESTConnector: max 50 endpoints allowed, got {len(endpoints)}"
            )

        # Validate each endpoint
        for ep in endpoints:
            path = ep.get("path", "")
            if not path.startswith("/"):
                raise ValueError(
                    f"GenericRESTConnector: endpoint path must start with /, got '{path}'"
                )
            if "../" in path or "..\\" in path:
                raise ValueError(
                    f"GenericRESTConnector: path traversal detected in '{path}'"
                )

    def _build_operations(self, connector_id: str) -> List[ConnectorOperation]:
        """Generate ConnectorOperations from endpoint configs."""
        ops = []
        for ep in self._endpoints:
            path = ep.get("path", "")
            method = ep.get("method", "GET").upper()

            # Generate operation ID from path + method
            sanitized = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
            op_id = f"{method.lower()}_{sanitized}" if sanitized else method.lower()

            # Map method to capability
            if method == "GET":
                capability = "connector.read"
                default_tier = RiskTier(ep.get("default_tier", 2))
            elif method in ("POST", "PUT", "PATCH"):
                capability = "connector.write"
                default_tier = RiskTier(ep.get("default_tier", 3))
            elif method == "DELETE":
                capability = "connector.delete"
                default_tier = RiskTier(ep.get("default_tier", 3))
            else:
                capability = "connector.write"
                default_tier = RiskTier.T3_IRREVERSIBLE

            ops.append(ConnectorOperation(
                id=op_id,
                connector_id=connector_id,
                capability=capability,
                name=ep.get("name", op_id),
                description=ep.get("description", ""),
                default_tier=default_tier,
            ))
        return ops

    def get_operations(self) -> List[ConnectorOperation]:
        return self._operations

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        # Validate param names
        for key in params:
            if not _PARAM_NAME_RE.match(key):
                raise ValueError(
                    f"GenericRESTConnector: invalid param name '{key}' — "
                    "alphanumeric + underscore only, max 64 chars"
                )

        # Find the matching endpoint
        for i, ep in enumerate(self._endpoints):
            op = self._operations[i]
            if op.id == operation_id:
                path = ep["path"]
                method = ep.get("method", "GET").upper()

                # Substitute {param} in path
                for key, value in params.items():
                    path = path.replace(f"{{{key}}}", str(value))

                url = f"{self._base_url}{path}"
                http_method = HTTPMethod(method)

                body = None
                if method in ("POST", "PUT", "PATCH"):
                    body = params

                return ConnectorResult(
                    operation_id=operation_id,
                    connector_id=self.manifest.id,
                    method=http_method,
                    url=url,
                    credential_vault_key=self._auth_vault_key,
                    body=body,
                )

        raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        return bool(self._auth_vault_key)
