"""
Client REST API — CRUD endpoints for BAL client management.

Router prefix: /api/v1/clients
All endpoints are gated on FEATURE_BAL.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.core.bal.clients.models import (
    Client,
    ClientCreate,
    ClientStatus,
    ClientUpdate,
)
from src.core.bal.clients.events import (
    emit_client_onboarded,
    emit_client_paused,
    emit_client_churned,
    emit_client_preferences_updated,
    emit_client_status_changed,
)
from src.core.bal.clients.state_machine import ClientStateMachine, InvalidTransitionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/clients", tags=["bal-clients"])

# Module-level references, set during init
_repository = None
_state_machine = ClientStateMachine()


def init_client_api(repository) -> None:
    """Initialize the client API with a repository instance."""
    global _repository
    _repository = repository


def _check_bal_enabled() -> None:
    """Raise 503 if BAL is not enabled."""
    try:
        import feature_flags
        if not feature_flags.FEATURE_BAL:
            raise HTTPException(status_code=503, detail="BAL is not enabled")
    except ImportError:
        try:
            from src.core.feature_flags import FEATURE_BAL
            if not FEATURE_BAL:
                raise HTTPException(status_code=503, detail="BAL is not enabled")
        except ImportError:
            pass


def _get_repo():
    """Get repository, raising 503 if not initialized."""
    if _repository is None:
        raise HTTPException(status_code=503, detail="BAL client service not initialized")
    return _repository


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class PauseRequest(BaseModel):
    reason: str = ""


class ClientListResponse(BaseModel):
    clients: List[Client]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=201, response_model=Client)
def create_client(body: ClientCreate):
    """Create a new BAL client."""
    _check_bal_enabled()
    repo = _get_repo()

    # Check for duplicate email
    existing = repo.get_by_email(body.email)
    if existing:
        raise HTTPException(status_code=409, detail="A client with this email already exists")

    client = repo.create(body)

    try:
        emit_client_onboarded(client)
    except Exception as exc:
        logger.warning("Failed to emit client_onboarded receipt: %s", exc)

    return client


@router.get("", response_model=ClientListResponse)
def list_clients(status: Optional[str] = Query(None)):
    """List all clients, optionally filtered by status."""
    _check_bal_enabled()
    repo = _get_repo()

    status_filter = None
    if status:
        try:
            status_filter = ClientStatus(status.lower())
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status: {status}. Must be one of: {[s.value for s in ClientStatus]}",
            )

    clients = repo.list_all(status_filter)
    return ClientListResponse(clients=clients, total=len(clients))


@router.get("/{client_id}", response_model=Client)
def get_client(client_id: str):
    """Get a single client by ID."""
    _check_bal_enabled()
    repo = _get_repo()

    client = repo.get_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.patch("/{client_id}", response_model=Client)
def update_client(client_id: str, body: ClientUpdate):
    """Update a client's name, email, or preferences."""
    _check_bal_enabled()
    repo = _get_repo()

    client = repo.get_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    # Check for duplicate email if changing
    if body.email and body.email != client.email:
        existing = repo.get_by_email(body.email)
        if existing:
            raise HTTPException(status_code=409, detail="A client with this email already exists")

    updated = repo.update(client_id, body)

    if body.preferences is not None:
        try:
            changed = [f for f in body.preferences.model_fields_set]
            emit_client_preferences_updated(updated, changed)
        except Exception as exc:
            logger.warning("Failed to emit preferences_updated receipt: %s", exc)

    return updated


@router.post("/{client_id}/pause", response_model=Client)
def pause_client(client_id: str, body: PauseRequest = PauseRequest()):
    """Pause an active client."""
    _check_bal_enabled()
    repo = _get_repo()

    client = repo.get_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        old_status = client.status
        updated = _state_machine.transition(
            client_id, ClientStatus.PAUSED, repo, reason=body.reason
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        emit_client_paused(updated, body.reason)
        emit_client_status_changed(updated, old_status, ClientStatus.PAUSED, body.reason)
    except Exception as exc:
        logger.warning("Failed to emit pause receipts: %s", exc)

    return updated


@router.post("/{client_id}/resume", response_model=Client)
def resume_client(client_id: str):
    """Resume a paused client."""
    _check_bal_enabled()
    repo = _get_repo()

    client = repo.get_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        old_status = client.status
        updated = _state_machine.transition(
            client_id, ClientStatus.ACTIVE, repo
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        emit_client_status_changed(updated, old_status, ClientStatus.ACTIVE, "resumed")
    except Exception as exc:
        logger.warning("Failed to emit resume receipt: %s", exc)

    return updated


@router.post("/{client_id}/activate", response_model=Client)
def activate_client(client_id: str):
    """Activate a client from onboarding status."""
    _check_bal_enabled()
    repo = _get_repo()

    client = repo.get_by_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        old_status = client.status
        updated = _state_machine.transition(
            client_id, ClientStatus.ACTIVE, repo
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        emit_client_onboarded(updated)
        emit_client_status_changed(updated, old_status, ClientStatus.ACTIVE, "activated")
    except Exception as exc:
        logger.warning("Failed to emit activation receipts: %s", exc)

    return updated
