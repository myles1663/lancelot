"""
Credential Onboarding API — FastAPI endpoints for credential management.

Provides endpoints for storing, checking, and deleting credentials
during connector setup. Credentials get INTO the vault through these
endpoints (manual entry path).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])

# Module-level references, set during app startup
_registry = None
_vault = None


def init_credential_api(registry, vault) -> None:
    """Initialize API with registry and vault references."""
    global _registry, _vault
    _registry = registry
    _vault = vault


# ── Request/Response Models ───────────────────────────────────────

class StoreCredentialRequest(BaseModel):
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
def store_credential(connector_id: str, body: StoreCredentialRequest):
    """Store a credential for a connector."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    # Check connector exists
    entry = _registry.get(connector_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")

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

    return StoreCredentialResponse(stored=True, vault_key=body.vault_key)


@router.get(
    "/{connector_id}/credentials/status",
    response_model=CredentialStatusResponse,
)
def credential_status(connector_id: str):
    """Check which required credentials are present vs missing."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _registry.get(connector_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")

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
def delete_credential(connector_id: str, vault_key: str):
    """Delete a credential from the vault."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _registry.get(connector_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")

    _vault.delete(vault_key)
    _vault.access_policy.revoke(connector_id, vault_key)

    return DeleteCredentialResponse(deleted=True)


@router.post(
    "/{connector_id}/credentials/validate",
    response_model=ValidateCredentialResponse,
)
def validate_credentials(connector_id: str):
    """Validate all required credentials are present and test connectivity."""
    if _registry is None or _vault is None:
        raise HTTPException(status_code=500, detail="Credential API not initialized")

    entry = _registry.get(connector_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_id}' not found")

    manifest = entry.manifest
    missing = [
        spec.vault_key
        for spec in manifest.required_credentials
        if spec.required and not _vault.exists(spec.vault_key)
    ]

    if missing:
        return ValidateCredentialResponse(valid=False, missing=missing)

    # Try connector's own validation
    try:
        valid = entry.connector.validate_credentials()
        return ValidateCredentialResponse(valid=valid)
    except Exception as e:
        return ValidateCredentialResponse(valid=False, error=str(e))
