"""
Connector Operation and Result Models.

Defines the data structures that flow through the connector pipeline:
- ConnectorOperation: what a connector can do
- ConnectorResult: the HTTP request spec a connector produces
- ConnectorResponse: the response after ConnectorProxy executes the request
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

from src.core.governance.models import RiskTier


# ── Parameter Spec ─────────────────────────────────────────────────

@dataclass(frozen=True)
class ParameterSpec:
    """Specification for a parameter accepted by a connector operation.

    Attributes:
        name: Parameter name
        type: Type hint string ("str", "int", "bool", "list[str]")
        required: Whether the parameter must be provided
        description: Human-readable description
        default: Default value when not required
    """
    name: str
    type: str
    required: bool = True
    description: str = ""
    default: Any = None


# ── Connector Operation ───────────────────────────────────────────

_VALID_CAPABILITIES = ("connector.read", "connector.write", "connector.delete")


@dataclass(frozen=True)
class ConnectorOperation:
    """Declaration of a single operation a connector supports.

    Attributes:
        id: Operation identifier (e.g., "read_messages")
        connector_id: Parent connector ID (e.g., "slack")
        capability: One of connector.read, connector.write, connector.delete
        name: Human-readable name
        description: What this operation does
        default_tier: Default risk tier for governance
        parameters: Parameters this operation accepts
        idempotent: Whether repeating the operation is safe
        reversible: Whether the operation can be undone
        rollback_operation_id: Operation to call for rollback
    """
    id: str
    connector_id: str
    capability: str
    name: str
    description: str = ""
    default_tier: RiskTier = RiskTier.T2_CONTROLLED
    parameters: List[ParameterSpec] = field(default_factory=list)
    idempotent: bool = False
    reversible: bool = False
    rollback_operation_id: str = ""

    @property
    def full_capability_id(self) -> str:
        """Fully qualified capability ID: connector.{connector_id}.{id}."""
        return f"connector.{self.connector_id}.{self.id}"

    def validate(self) -> None:
        """Validate operation fields. Raises ValueError on invalid data."""
        if not self.id:
            raise ValueError("ConnectorOperation.id must not be empty")
        if not self.connector_id:
            raise ValueError("ConnectorOperation.connector_id must not be empty")
        if self.capability not in _VALID_CAPABILITIES:
            raise ValueError(
                f"ConnectorOperation.capability must be one of {_VALID_CAPABILITIES}, "
                f"got '{self.capability}'"
            )


# ── HTTP Method ────────────────────────────────────────────────────

class HTTPMethod(str, Enum):
    """HTTP methods supported by connector operations."""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


# ── Connector Result (Request Spec) ───────────────────────────────

@dataclass
class ConnectorResult:
    """HTTP request specification produced by a connector's execute() method.

    This is NOT the HTTP response — it's the request that ConnectorProxy
    will send through the governance pipeline and then execute.

    Attributes:
        operation_id: Which operation produced this request
        connector_id: Which connector produced this request
        method: HTTP method
        url: Full URL to call
        headers: Request headers (credentials injected by proxy)
        body: Request body (must be None for GET/DELETE)
        timeout_seconds: Request timeout
        credential_vault_key: Key to retrieve auth credentials from vault
        metadata: Additional metadata for auditing
    """
    operation_id: str
    connector_id: str
    method: HTTPMethod
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    timeout_seconds: int = 30
    credential_vault_key: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate request spec. Raises ValueError on invalid data."""
        if not self.url:
            raise ValueError("ConnectorResult.url must not be empty")
        if self.method in (HTTPMethod.GET, HTTPMethod.DELETE) and self.body is not None:
            raise ValueError(
                f"ConnectorResult.body must be None for {self.method.value} requests"
            )
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"ConnectorResult.timeout_seconds must be > 0, got {self.timeout_seconds}"
            )


# ── Connector Response ────────────────────────────────────────────

@dataclass
class ConnectorResponse:
    """Response after ConnectorProxy executes a ConnectorResult.

    Attributes:
        operation_id: Which operation this response is for
        connector_id: Which connector this response is for
        status_code: HTTP status code
        headers: Response headers
        body: Response body (parsed JSON or raw text)
        elapsed_ms: Request duration in milliseconds
        success: Whether the operation succeeded
        error: Error message if failed
        receipt_id: Governance receipt ID for auditing
    """
    operation_id: str
    connector_id: str
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    elapsed_ms: float = 0.0
    success: bool = True
    error: str = ""
    receipt_id: str = ""

    @property
    def is_error(self) -> bool:
        """True if the response represents an error."""
        return not self.success or self.status_code >= 400
