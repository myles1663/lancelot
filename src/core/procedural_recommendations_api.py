"""War Room API for proactive procedural recommendations."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability, resolve_authenticated_identity

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/procedural-recommendations",
    tags=["procedural-recommendations"],
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("platform.admin")),
    ],
)

_recommendation_store = None
_actioncard_store = None


def init_procedural_recommendations_api(recommendation_store) -> None:
    global _recommendation_store
    _recommendation_store = recommendation_store
    logger.info("Procedural recommendations API initialized")


def bind_procedural_recommendations_actioncard_store(actioncard_store) -> None:
    global _actioncard_store
    _actioncard_store = actioncard_store
    logger.info("Procedural recommendations ActionCard store bound")


def shutdown_procedural_recommendations_api() -> None:
    global _recommendation_store, _actioncard_store
    _recommendation_store = None
    _actioncard_store = None
    logger.info("Procedural recommendations API shutdown complete")


class RecommendationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = ""
    snooze_hours: int = 24


def _store():
    if _recommendation_store is None:
        raise HTTPException(503, "Procedural recommendation store not initialized")
    return _recommendation_store


def _is_visible_to_operator(record, operator_id: str) -> bool:
    return not record.operator_id or not operator_id or record.operator_id == operator_id


def _get_visible_record(recommendation_id: str, operator_id: str):
    record = _store().get(recommendation_id)
    if record is None or not _is_visible_to_operator(record, operator_id):
        raise HTTPException(404, f"Recommendation not found: {recommendation_id}")
    return record


def _resolve_linked_actioncard(record, action: str) -> None:
    if _actioncard_store is None or not getattr(record, "actioncard_id", ""):
        return
    try:
        _actioncard_store.resolve(
            record.actioncard_id,
            action,
            "procedural_recommendations_panel",
        )
    except Exception as exc:
        logger.warning(
            "procedural_recommendation_actioncard_resolve_failed",
            extra={"error": str(exc), "actioncard_id": record.actioncard_id},
        )


@router.get("/")
async def list_recommendations(
    request: Request,
    status: str = "pending",
    category: Optional[str] = None,
    limit: int = 50,
):
    identity = resolve_authenticated_identity(request)
    store = _store()
    records = store.list(
        status=status,
        category=category,
        operator_id=identity.operator_id,
        limit=limit,
    )
    return {"recommendations": [record.to_dict() for record in records], "count": len(records)}


@router.get("/stats")
async def recommendation_stats(request: Request):
    identity = resolve_authenticated_identity(request)
    return {"stats": _store().stats(operator_id=identity.operator_id)}


@router.get("/{recommendation_id}")
async def get_recommendation(recommendation_id: str, request: Request):
    identity = resolve_authenticated_identity(request)
    record = _get_visible_record(recommendation_id, identity.operator_id)
    return {"recommendation": record.to_dict()}


@router.post("/{recommendation_id}/accept")
async def accept_recommendation(
    recommendation_id: str,
    request: Request,
    body: RecommendationActionRequest | None = None,
):
    identity = resolve_authenticated_identity(request)
    _get_visible_record(recommendation_id, identity.operator_id)
    try:
        record = _store().record_action(
            recommendation_id,
            "accept",
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            actor=identity.display_name or identity.operator_id,
            channel="warroom",
            reason=(body.reason if body else ""),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    _resolve_linked_actioncard(record, "accept")
    return {"status": record.status, "recommendation": record.to_dict()}


@router.post("/{recommendation_id}/dismiss")
async def dismiss_recommendation(
    recommendation_id: str,
    request: Request,
    body: RecommendationActionRequest | None = None,
):
    identity = resolve_authenticated_identity(request)
    _get_visible_record(recommendation_id, identity.operator_id)
    try:
        record = _store().record_action(
            recommendation_id,
            "dismiss",
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            actor=identity.display_name or identity.operator_id,
            channel="warroom",
            reason=(body.reason if body else ""),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    _resolve_linked_actioncard(record, "dismiss")
    return {"status": record.status, "recommendation": record.to_dict()}


@router.post("/{recommendation_id}/snooze")
async def snooze_recommendation(
    recommendation_id: str,
    request: Request,
    body: RecommendationActionRequest | None = None,
):
    identity = resolve_authenticated_identity(request)
    _get_visible_record(recommendation_id, identity.operator_id)
    hours = max(1, min(int(body.snooze_hours if body else 24), 24 * 30))
    try:
        record = _store().record_action(
            recommendation_id,
            "snooze",
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            actor=identity.display_name or identity.operator_id,
            channel="warroom",
            reason=(body.reason if body else ""),
            snooze_seconds=hours * 3600,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    _resolve_linked_actioncard(record, "snooze")
    return {"status": record.status, "snoozed_until": record.snoozed_until, "recommendation": record.to_dict()}


@router.post("/{recommendation_id}/convert-to-sop")
async def convert_recommendation_to_sop(
    recommendation_id: str,
    request: Request,
):
    identity = resolve_authenticated_identity(request)
    _get_visible_record(recommendation_id, identity.operator_id)
    try:
        record = _store().convert_to_sop_draft(
            recommendation_id,
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            actor=identity.display_name or identity.operator_id,
            channel="warroom",
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    _resolve_linked_actioncard(record, "make_sop")
    return {
        "status": record.status,
        "sop_draft_path": record.sop_draft_path,
        "recommendation": record.to_dict(),
    }


def resolve_recommendation_action(
    recommendation_id: str,
    button_id: str,
    *,
    operator_id: str = "",
    session_id: str = "",
    actor: str = "",
    channel: str = "actioncard",
) -> dict:
    """ActionCard handler for recommendation lifecycle buttons."""
    try:
        record = _store().get(recommendation_id)
        if record is None:
            raise KeyError(f"Recommendation not found: {recommendation_id}")
        if not _is_visible_to_operator(record, operator_id):
            return {"status": "error", "message": "Recommendation not found"}
        if button_id == "make_sop":
            record = _store().convert_to_sop_draft(
                recommendation_id,
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
                channel=channel,
            )
            return {
                "status": "converted_to_sop",
                "message": "SOP draft created.",
                "sop_draft_path": record.sop_draft_path,
                "recommendation": record.to_dict(),
            }
        if button_id == "snooze":
            record = _store().record_action(
                recommendation_id,
                "snooze",
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
                channel=channel,
                snooze_seconds=24 * 3600,
            )
            return {"status": "snoozed", "message": "Recommendation snoozed.", "recommendation": record.to_dict()}
        if button_id in {"accept", "useful"}:
            record = _store().record_action(
                recommendation_id,
                "accept",
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
                channel=channel,
            )
            return {"status": "accepted", "message": "Recommendation marked useful.", "recommendation": record.to_dict()}
        if button_id == "dismiss":
            record = _store().record_action(
                recommendation_id,
                "dismiss",
                operator_id=operator_id,
                session_id=session_id,
                actor=actor,
                channel=channel,
            )
            return {"status": "dismissed", "message": "Recommendation dismissed.", "recommendation": record.to_dict()}
        return {"status": "error", "message": f"Unknown button: {button_id}"}
    except KeyError as exc:
        return {"status": "error", "message": str(exc)}
