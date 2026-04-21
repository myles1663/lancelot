"""
Credential Onboarding API — FastAPI endpoints for credential management.

Provides endpoints for storing, checking, and deleting credentials
during connector setup. Credentials get INTO the vault through these
endpoints (manual entry path).
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/connectors",
    tags=["connectors"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("connectors.admin")),
    ],
)

# Module-level references, set during app startup
_registry = None
_vault = None


def init_credential_api(registry, vault) -> None:
    """Initialize API with registry and vault references."""
    global _registry, _vault
    _registry = registry
    _vault = vault


def _resolve_connector_entry(connector_id: str):
    """Resolve a connector entry from runtime registry or config-backed lazy load.

    Credential onboarding must work before a connector is enabled in the live
    runtime. If the connector is absent from the runtime registry, lazily
    instantiate it from connectors.yaml so its manifest can drive credential
    onboarding and validation.
    """
    if _registry is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _registry.get(connector_id)
    if entry is not None:
        return entry

    config = getattr(_registry, "_config", {}) or {}
    connector_cfg = config.get("connectors", {}).get(connector_id)
    if connector_cfg is None:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")

    try:
        from src.core.connectors_api import register_connector_with_vault_access

        connector = register_connector_with_vault_access(
            _registry,
            _vault,
            connector_id,
            connector_cfg,
        )
        if connector is None:
            raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Credential API lazy connector registration failed for %s: %s", connector_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Connector '{connector_id}' could not be initialized for credential onboarding",
        )

    entry = _registry.get(connector_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")
    return entry


# ── Request/Response Models ───────────────────────────────────────

class StoreCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vault_key: str
    value: str
    type: str = "api_key"


class StoreCredentialResponse(BaseModel):
    stored: bool
    vault_key: str


class CredentialStatusItem(BaseModel):
    vault_key: str
    type: str
    required: bool
    present: bool


class CredentialStatusResponse(BaseModel):
    connector_id: str
    credentials: List[CredentialStatusItem]


class DeleteCredentialResponse(BaseModel):
    deleted: bool


class ValidateCredentialResponse(BaseModel):
    valid: bool
    missing: List[str] = []
    error: str = ""


# ── Endpoints ─────────────────────────────────────────────────────

@router.post(
    "/{connector_id}/credentials",
    response_model=StoreCredentialResponse,
)
def store_credential(connector_id: str, body: StoreCredentialRequest, request: Request):
    """Store a credential for a connector."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _resolve_connector_entry(connector_id)

    # Check vault_key is declared in manifest
    manifest = entry.manifest
    declared_keys = {spec.vault_key for spec in manifest.required_credentials}
    if body.vault_key not in declared_keys:
        raise HTTPException(
            status_code=400,
            detail=f"vault_key '{body.vault_key}' not declared in connector manifest",
        )

    # Store and grant access
    _vault.store(body.vault_key, body.value, type=body.type)
    _vault.grant_connector_access(connector_id, manifest)

    # Hot-swap: if this is the workspace path, update docker-compose and restart
    if body.vault_key == "shared_workspace.host_path":
        try:
            _apply_workspace_path(body.value)
        except Exception as exc:
            logger.warning("Workspace hot-swap failed: %s", exc)

    # Governance receipt
    from src.core.governance_receipts import emit_governance_receipt
    from src.shared.receipts import ActionType
    emit_governance_receipt(
        request,
        ActionType.CREDENTIAL_REGISTERED,
        action_name="store_credential",
        inputs={"connector_id": connector_id, "vault_key": body.vault_key, "type": body.type},
    )

    return StoreCredentialResponse(stored=True, vault_key=body.vault_key)


@router.get(
    "/{connector_id}/credentials/status",
    response_model=CredentialStatusResponse,
)
def credential_status(connector_id: str):
    """Check which required credentials are present vs missing."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _resolve_connector_entry(connector_id)
    manifest = entry.manifest
    items = []
    for spec in manifest.required_credentials:
        items.append(CredentialStatusItem(
            vault_key=spec.vault_key,
            type=spec.type,
            required=spec.required,
            present=_vault.exists(spec.vault_key),
        ))

    return CredentialStatusResponse(connector_id=connector_id, credentials=items)


@router.delete(
    "/{connector_id}/credentials/{vault_key}",
    response_model=DeleteCredentialResponse,
)
def delete_credential(connector_id: str, vault_key: str, request: Request):
    """Delete a credential from the vault."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _resolve_connector_entry(connector_id)

    _vault.delete(vault_key)
    _vault.access_policy.revoke(connector_id, vault_key)

    # Governance receipt
    from src.core.governance_receipts import emit_governance_receipt
    from src.shared.receipts import ActionType
    emit_governance_receipt(
        request,
        ActionType.CREDENTIAL_REVOKED,
        action_name="delete_credential",
        inputs={"connector_id": connector_id, "vault_key": vault_key},
    )

    return DeleteCredentialResponse(deleted=True)


@router.post(
    "/{connector_id}/credentials/validate",
    response_model=ValidateCredentialResponse,
)
def validate_credentials(connector_id: str):
    """Validate all required credentials are present and test connectivity."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _resolve_connector_entry(connector_id)

    manifest = entry.manifest
    missing = [
        spec.vault_key
        for spec in manifest.required_credentials
        if spec.required and not _vault.exists(spec.vault_key)
    ]

    if missing:
        return ValidateCredentialResponse(valid=False, missing=missing)

    # Inject vault into connector if not already set, so validate_credentials() works
    if hasattr(entry.connector, "_vault") and entry.connector._vault is None:
        entry.connector._vault = _vault

    # Try connector's own validation
    try:
        valid = entry.connector.validate_credentials()
        return ValidateCredentialResponse(valid=valid)
    except Exception as e:
        logger.warning(
            "Credential validation failed for connector %s: %s",
            connector_id,
            e,
        )
        return ValidateCredentialResponse(valid=False, error=str(e))


# ── Workspace Hot-Swap ───────────────────────────────────────────

_COMPOSE_PATH = Path("/home/lancelot/app/docker-compose.yml")
_WORKSPACE_MOUNT_PATTERN = re.compile(
    r'^(\s*-\s*["\']?)(.+?)(:/home/lancelot/workspace["\']?\s*)$'
)


def _apply_workspace_path(host_path: str) -> None:
    """Update docker-compose.yml with the new workspace path and restart.

    Reads the compose file, replaces the workspace volume mount line,
    saves, then runs `docker compose up -d` to recreate the container
    with the new mount. The container replaces itself.
    """
    if not _COMPOSE_PATH.exists():
        logger.warning("docker-compose.yml not found at %s", _COMPOSE_PATH)
        return

    content = _COMPOSE_PATH.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    updated = False

    for i, line in enumerate(lines):
        match = _WORKSPACE_MOUNT_PATTERN.match(line)
        if match:
            prefix, _old_path, suffix = match.groups()
            lines[i] = f'{prefix}{host_path}{suffix}'
            if not lines[i].endswith('\n'):
                lines[i] += '\n'
            updated = True
            logger.info("Workspace mount updated: %s -> %s", _old_path, host_path)
            break

    if not updated:
        logger.warning("Could not find workspace mount line in docker-compose.yml")
        return

    _COMPOSE_PATH.write_text("".join(lines), encoding="utf-8")

    # Schedule container recreation after response is sent
    def _recreate():
        try:
            logger.info("Hot-swapping workspace — running docker compose up -d")
            subprocess.run(
                ["docker", "compose", "-f", str(_COMPOSE_PATH), "up", "-d", "lancelot-core"],
                capture_output=True, text=True, timeout=120,
            )
        except Exception as exc:
            logger.error("Workspace hot-swap restart failed: %s", exc)

    threading.Timer(1.0, _recreate).start()
