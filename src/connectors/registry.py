"""
Connector Registry — Central registration and lookup for connectors.

The registry holds all registered connectors, validates their manifests,
checks feature flags, and provides lookup by ID and operation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.connectors.base import ConnectorBase, ConnectorManifest, ConnectorStatus
from src.core.feature_flags import FEATURE_CONNECTORS


# ── Registry Entry ─────────────────────────────────────────────────

@dataclass
class ConnectorEntry:
    """A registered connector with metadata."""
    connector: ConnectorBase
    manifest: ConnectorManifest
    registered_at: str
    config: Dict[str, Any] = field(default_factory=dict)


# ── Connector Registry ────────────────────────────────────────────

class ConnectorRegistry:
    """Central registry for all connector instances.

    Loads configuration from connectors.yaml and manages connector
    lifecycle: registration, lookup, status updates, and operation queries.
    """

    def __init__(self, config_path: str = "config/connectors.yaml") -> None:
        self._lock = threading.Lock()
        self._connectors: Dict[str, ConnectorEntry] = {}
        self._config: Dict[str, Any] = {}
        self._load_config(config_path)

    def _load_config(self, config_path: str) -> None:
        """Load connector configuration from YAML."""
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}

    @property
    def settings(self) -> Dict[str, Any]:
        """Global connector settings from config."""
        return self._config.get("settings", {})

    @property
    def rate_limits(self) -> Dict[str, Any]:
        """Rate limit configuration from config."""
        return self._config.get("rate_limits", {})

    def register(self, connector: ConnectorBase) -> ConnectorEntry:
        """Register a connector. Validates manifest and checks feature flag.

        Raises:
            RuntimeError: If FEATURE_CONNECTORS is disabled
            ValueError: If connector is already registered
        """
        # Import at call time to pick up reloaded flag values
        from src.core import feature_flags
        if not feature_flags.FEATURE_CONNECTORS:
            raise RuntimeError(
                "Cannot register connector: FEATURE_CONNECTORS is disabled"
            )

        manifest = connector.manifest
        with self._lock:
            if manifest.id in self._connectors:
                raise ValueError(
                    f"Connector '{manifest.id}' is already registered"
                )

            # Load connector-specific config from YAML
            connector_config = (
                self._config.get("connectors", {}).get(manifest.id, {})
            )

            entry = ConnectorEntry(
                connector=connector,
                manifest=manifest,
                registered_at=datetime.now(timezone.utc).isoformat(),
                config=connector_config,
            )
            self._connectors[manifest.id] = entry
            return entry

    def unregister(self, connector_id: str) -> bool:
        """Remove a connector. Returns True if found and removed."""
        with self._lock:
            if connector_id in self._connectors:
                del self._connectors[connector_id]
                return True
            return False

    def get(self, connector_id: str) -> Optional[ConnectorEntry]:
        """Get a connector entry by ID, or None if not found."""
        with self._lock:
            return self._connectors.get(connector_id)

    def list_connectors(self) -> List[ConnectorEntry]:
        """Return all registered connector entries."""
        with self._lock:
            return list(self._connectors.values())

    def list_active(self) -> List[ConnectorEntry]:
        """Return only connectors with ACTIVE status."""
        with self._lock:
            return [
                entry for entry in self._connectors.values()
                if entry.connector.status == ConnectorStatus.ACTIVE
            ]

    def get_operations(self, connector_id: str) -> list:
        """Get all operations for a connector. Raises KeyError if not found."""
        with self._lock:
            entry = self._connectors.get(connector_id)
            if entry is None:
                raise KeyError(f"Connector '{connector_id}' not found")
            return entry.connector.get_operations()

    def get_operation(self, connector_id: str, operation_id: str) -> Any:
        """Get a specific operation. Raises KeyError if not found."""
        ops = self.get_operations(connector_id)
        for op in ops:
            op_id = op.id if hasattr(op, "id") else op.get("id")
            if op_id == operation_id:
                return op
        raise KeyError(
            f"Operation '{operation_id}' not found in connector '{connector_id}'"
        )

    def update_status(self, connector_id: str, status: ConnectorStatus) -> None:
        """Update a connector's status. Raises KeyError if not found."""
        with self._lock:
            entry = self._connectors.get(connector_id)
            if entry is None:
                raise KeyError(f"Connector '{connector_id}' not found")
            entry.connector.set_status(status)
