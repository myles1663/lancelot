"""
Connector Base Classes — Manifests, Credentials, and Abstract Connector.

Every connector declares a ConnectorManifest describing what it accesses,
what credentials it needs, and what domains it talks to. ConnectorBase
is the abstract class all connectors inherit from.

Connectors NEVER make network calls directly — they produce request specs
that ConnectorProxy sends through the governance pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List


# ── Imports from governance ────────────────────────────────────────
from src.core.governance.models import RiskTier  # noqa: F401 — re-export for convenience


# ── Connector Status ───────────────────────────────────────────────

class ConnectorStatus(str, Enum):
    """Lifecycle status of a connector instance."""
    REGISTERED = "registered"
    CONFIGURED = "configured"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ERROR = "error"


# ── Credential Spec ────────────────────────────────────────────────

@dataclass(frozen=True)
class CredentialSpec:
    """Specification for a credential required by a connector.

    Attributes:
        name: Human-readable name (e.g., "slack_bot_token")
        type: Credential type (e.g., "oauth_token", "api_key", "basic_auth")
        vault_key: Key used to retrieve from CredentialVault
        required: Whether the connector cannot function without it
        scopes: OAuth scopes or permission scopes needed
    """
    name: str
    type: str
    vault_key: str
    required: bool = True
    scopes: List[str] = field(default_factory=list)


# ── Connector Manifest ─────────────────────────────────────────────

@dataclass(frozen=True)
class ConnectorManifest:
    """Immutable declaration of what a connector does and needs.

    Every connector must provide a manifest at registration time.
    The manifest is used by the governance pipeline to evaluate
    risk and enforce policy.

    Attributes:
        id: Unique connector identifier (e.g., "slack")
        name: Display name (e.g., "Slack Integration")
        version: Semantic version string
        author: Author or organization
        source: Origin trust level — "first-party", "community", or "user"
        description: Human-readable description
        target_domains: Domains this connector communicates with
        required_credentials: Credentials needed to operate
        data_reads: Data types this connector reads
        data_writes: Data types this connector writes
        does_not_access: Explicit negative declarations
    """
    id: str
    name: str
    version: str
    author: str
    source: str
    description: str = ""
    target_domains: List[str] = field(default_factory=list)
    required_credentials: List[CredentialSpec] = field(default_factory=list)
    data_reads: List[str] = field(default_factory=list)
    data_writes: List[str] = field(default_factory=list)
    does_not_access: List[str] = field(default_factory=list)

    def validate(self) -> None:
        """Validate manifest fields. Raises ValueError on invalid data."""
        if not self.id:
            raise ValueError("ConnectorManifest.id must not be empty")
        if not self.name:
            raise ValueError("ConnectorManifest.name must not be empty")
        if not self.version:
            raise ValueError("ConnectorManifest.version must not be empty")
        if self.source not in ("first-party", "community", "user"):
            raise ValueError(
                f"ConnectorManifest.source must be 'first-party', 'community', or 'user', "
                f"got '{self.source}'"
            )
        if not self.target_domains:
            raise ValueError(
                "ConnectorManifest.target_domains must not be empty — "
                "connectors must declare where they talk"
            )


# ── Connector Base ─────────────────────────────────────────────────

class ConnectorBase(ABC):
    """Abstract base class for all connectors.

    Subclasses must implement:
    - get_operations(): Return the list of operations this connector supports
    - execute(): Produce a request spec for a given operation (sync)
    - validate_credentials(): Check if required credentials are available
    """

    def __init__(self, manifest: ConnectorManifest) -> None:
        manifest.validate()
        self._manifest = manifest
        self._status = ConnectorStatus.REGISTERED
        self._created_at = datetime.now(timezone.utc).isoformat()

    @property
    def manifest(self) -> ConnectorManifest:
        """The connector's immutable manifest."""
        return self._manifest

    @property
    def id(self) -> str:
        """Shortcut to manifest.id."""
        return self._manifest.id

    @property
    def status(self) -> ConnectorStatus:
        """Current connector status."""
        return self._status

    def set_status(self, status: ConnectorStatus) -> None:
        """Update connector status."""
        self._status = status

    @abstractmethod
    def get_operations(self) -> list:
        """Return the list of ConnectorOperations this connector supports."""
        ...

    @abstractmethod
    def execute(self, operation_id: str, params: dict) -> Any:
        """Produce a request spec for the given operation.

        Connectors do NOT make network calls — they return a dict
        describing the HTTP request for ConnectorProxy to execute.
        """
        ...

    @abstractmethod
    def validate_credentials(self) -> bool:
        """Check if required credentials are available in the vault."""
        ...
