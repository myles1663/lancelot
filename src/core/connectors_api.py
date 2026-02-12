"""
Connectors Management API — War Room management endpoints.

Provides endpoints for listing, enabling/disabling, and configuring
connectors from the War Room UI. Works alongside credential_api.py
which handles per-credential store/delete/validate operations.

Router prefix: /api/connectors
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors-management"])

# Module-level references, set during app startup
_registry = None
_vault = None
_config_path = "config/connectors.yaml"
_config_lock = threading.Lock()


def init_connectors_api(registry, vault, config_path: str = "config/connectors.yaml") -> None:
    """Initialize API with registry and vault references."""
    global _registry, _vault, _config_path
    _registry = registry
    _vault = vault
    _config_path = config_path


# ── Connector Class Registry ────────────────────────────────────

# Maps connector IDs to (module_path, class_name, default_kwargs)
_CONNECTOR_CLASSES = {
    "email": ("src.connectors.connectors.email", "EmailConnector", {}),
    "slack": ("src.connectors.connectors.slack", "SlackConnector", {}),
    "teams": ("src.connectors.connectors.teams", "TeamsConnector", {}),
    "discord": ("src.connectors.connectors.discord", "DiscordConnector", {}),
    "whatsapp": ("src.connectors.connectors.whatsapp", "WhatsAppConnector", {"phone_number_id": ""}),
    "sms": ("src.connectors.connectors.sms", "SMSConnector", {"account_sid": ""}),
    "calendar": ("src.connectors.connectors.calendar", "CalendarConnector", {}),
}

_BACKEND_OPTIONS: Dict[str, List[str]] = {
    "email": ["gmail", "outlook", "smtp"],
}


def _instantiate_connector(connector_id: str, config: dict):
    """Instantiate a connector class to read its manifest."""
    if connector_id not in _CONNECTOR_CLASSES:
        return None
    module_path, class_name, defaults = _CONNECTOR_CLASSES[connector_id]
    try:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        kwargs = dict(defaults)
        # Apply backend setting if applicable
        if connector_id in _BACKEND_OPTIONS and "backend" in config:
            kwargs["backend"] = config["backend"]
        # Apply settings from config
        settings = config.get("settings", {})
        if connector_id == "whatsapp" and settings.get("phone_number_id"):
            kwargs["phone_number_id"] = settings["phone_number_id"]
        if connector_id == "sms":
            if settings.get("from_number"):
                kwargs["from_number"] = settings["from_number"]
            if settings.get("messaging_service_sid"):
                kwargs["messaging_service_sid"] = settings["messaging_service_sid"]
            kwargs["account_sid"] = kwargs.get("account_sid", "")
        return cls(**kwargs)
    except Exception as e:
        logger.warning("Failed to instantiate connector %s: %s", connector_id, e)
        return None


def _load_config() -> dict:
    """Load connectors.yaml."""
    path = Path(_config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_config(config: dict) -> None:
    """Write connectors.yaml with lock."""
    with _config_lock:
        path = Path(_config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)


# ── Response Models ──────────────────────────────────────────────

class CredentialInfoResponse(BaseModel):
    vault_key: str
    name: str
    type: str
    required: bool
    present: bool
    scopes: List[str]


class ConnectorInfoResponse(BaseModel):
    id: str
    name: str
    description: str
    version: str
    author: str
    source: str
    enabled: bool
    backend: Optional[str] = None
    available_backends: Optional[List[str]] = None
    target_domains: List[str]
    data_reads: List[str]
    data_writes: List[str]
    does_not_access: List[str]
    credentials: List[CredentialInfoResponse]
    operation_count: int


class ConnectorsListResponse(BaseModel):
    connectors: List[ConnectorInfoResponse]
    total: int
    enabled_count: int
    configured_count: int


class ConnectorToggleResponse(BaseModel):
    id: str
    enabled: bool


class BackendSetRequest(BaseModel):
    backend: str


class BackendSetResponse(BaseModel):
    connector_id: str
    backend: str


# ── Endpoints ────────────────────────────────────────────────────

@router.get("", response_model=ConnectorsListResponse)
def list_connectors():
    """List all available connectors with status and credential info."""
    full_config = _load_config()
    connectors_config = full_config.get("connectors", {})

    items = []
    enabled_count = 0
    configured_count = 0

    for cid in list(_CONNECTOR_CLASSES.keys()):
        ccfg = connectors_config.get(cid, {})
        enabled = ccfg.get("enabled", False)
        if enabled:
            enabled_count += 1

        # Instantiate to get manifest
        connector = _instantiate_connector(cid, ccfg)
        if connector is None:
            continue

        manifest = connector.manifest

        # Check credentials
        cred_items = []
        all_present = True
        any_present = False
        for spec in manifest.required_credentials:
            present = _vault.exists(spec.vault_key) if _vault else False
            if present:
                any_present = True
            else:
                all_present = False
            cred_items.append(CredentialInfoResponse(
                vault_key=spec.vault_key,
                name=spec.name,
                type=spec.type,
                required=spec.required,
                present=present,
                scopes=list(spec.scopes) if spec.scopes else [],
            ))

        if all_present and len(cred_items) > 0:
            configured_count += 1

        # Backend info
        backend = ccfg.get("backend") if cid in _BACKEND_OPTIONS else None
        available_backends = _BACKEND_OPTIONS.get(cid)

        items.append(ConnectorInfoResponse(
            id=cid,
            name=manifest.name,
            description=manifest.description,
            version=manifest.version,
            author=manifest.author,
            source=manifest.source,
            enabled=enabled,
            backend=backend,
            available_backends=available_backends,
            target_domains=list(manifest.target_domains),
            data_reads=list(manifest.data_reads),
            data_writes=list(manifest.data_writes),
            does_not_access=list(manifest.does_not_access),
            credentials=cred_items,
            operation_count=len(connector.get_operations()),
        ))

    return ConnectorsListResponse(
        connectors=items,
        total=len(items),
        enabled_count=enabled_count,
        configured_count=configured_count,
    )


@router.post("/{connector_id}/enable", response_model=ConnectorToggleResponse)
def enable_connector(connector_id: str):
    """Enable a connector."""
    if connector_id not in _CONNECTOR_CLASSES:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")

    full_config = _load_config()
    connectors_config = full_config.setdefault("connectors", {})
    ccfg = connectors_config.setdefault(connector_id, {})
    ccfg["enabled"] = True
    _save_config(full_config)

    # Register in registry if not already
    if _registry and _registry.get(connector_id) is None:
        try:
            connector = _instantiate_connector(connector_id, ccfg)
            if connector:
                _registry.register(connector)
                logger.info("Connector enabled and registered: %s", connector_id)
        except Exception as e:
            logger.warning("Failed to register connector %s: %s", connector_id, e)

    return ConnectorToggleResponse(id=connector_id, enabled=True)


@router.post("/{connector_id}/disable", response_model=ConnectorToggleResponse)
def disable_connector(connector_id: str):
    """Disable a connector."""
    if connector_id not in _CONNECTOR_CLASSES:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_id}")

    full_config = _load_config()
    connectors_config = full_config.setdefault("connectors", {})
    ccfg = connectors_config.setdefault(connector_id, {})
    ccfg["enabled"] = False
    _save_config(full_config)

    # Unregister from registry
    if _registry:
        _registry.unregister(connector_id)
        logger.info("Connector disabled and unregistered: %s", connector_id)

    return ConnectorToggleResponse(id=connector_id, enabled=False)


@router.post("/{connector_id}/backend", response_model=BackendSetResponse)
def set_backend(connector_id: str, body: BackendSetRequest):
    """Set the backend for a multi-backend connector."""
    if connector_id not in _BACKEND_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Connector '{connector_id}' does not support backend selection",
        )
    if body.backend not in _BACKEND_OPTIONS[connector_id]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid backend '{body.backend}'. Options: {_BACKEND_OPTIONS[connector_id]}",
        )

    full_config = _load_config()
    connectors_config = full_config.setdefault("connectors", {})
    ccfg = connectors_config.setdefault(connector_id, {})
    ccfg["backend"] = body.backend
    _save_config(full_config)

    # Re-register with new backend if currently registered
    if _registry and _registry.get(connector_id) is not None:
        _registry.unregister(connector_id)
        try:
            connector = _instantiate_connector(connector_id, ccfg)
            if connector:
                _registry.register(connector)
                logger.info("Connector %s re-registered with backend: %s", connector_id, body.backend)
        except Exception as e:
            logger.warning("Failed to re-register connector %s: %s", connector_id, e)

    return BackendSetResponse(connector_id=connector_id, backend=body.backend)
