# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under AGPL-3.0. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
ActionCard REST API — endpoints for listing and resolving ActionCards.

Mounted at /api/actioncards in the gateway.
War Room frontend calls these endpoints; Telegram routes through the resolver directly.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/actioncards", tags=["actioncards"])

# These are set by gateway.py during startup
_card_store = None
_card_resolver = None


def init_actioncard_api(card_store, card_resolver) -> None:
    """Initialize the API with store and resolver references."""
    global _card_store, _card_resolver
    _card_store = card_store
    _card_resolver = card_resolver
    logger.info("ActionCard API initialized")


class ResolveRequest(BaseModel):
    """Optional body for resolve endpoint."""
    reason: str = ""


@router.get("/")
async def list_actioncards(
    status: str = "pending",
    source_system: Optional[str] = None,
    limit: int = 50,
):
    """List ActionCards, optionally filtered by status and source."""
    if not _card_store:
        raise HTTPException(503, "ActionCard store not initialized")

    if status == "pending":
        cards = _card_store.list_pending(source_system=source_system, limit=limit)
    elif status == "all":
        cards = _card_store.list_all(limit=limit, include_resolved=True)
    else:
        cards = _card_store.list_all(limit=limit, include_resolved=False)

    return {"cards": [c.to_dict() for c in cards], "count": len(cards)}


@router.get("/{card_id}")
async def get_actioncard(card_id: str):
    """Get a single ActionCard by ID."""
    if not _card_store:
        raise HTTPException(503, "ActionCard store not initialized")

    card = _card_store.get(card_id)
    if card is None:
        card = _card_store.get_by_prefix(card_id)
    if card is None:
        raise HTTPException(404, f"ActionCard not found: {card_id}")

    return card.to_dict()


@router.post("/{card_id}/resolve/{button_id}")
async def resolve_actioncard(
    card_id: str,
    button_id: str,
    request: Request,
    body: Optional[ResolveRequest] = None,
):
    """Resolve an ActionCard by clicking a button.

    Called by the War Room frontend. Telegram callback_query
    routes directly through the resolver, not this endpoint.
    """
    if not _card_resolver:
        raise HTTPException(503, "ActionCard resolver not initialized")

    result = _card_resolver.resolve(card_id, button_id, channel="warroom")

    if result.get("status") == "error":
        raise HTTPException(400, result.get("message", "Resolution failed"))

    return result


@router.post("/cleanup")
async def cleanup_actioncards():
    """Delete expired and old resolved cards."""
    if not _card_store:
        raise HTTPException(503, "ActionCard store not initialized")

    deleted = _card_store.cleanup_expired()
    return {"deleted": deleted}
