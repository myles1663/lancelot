"""
APL (Approval Pattern Learning) API — /api/apl/*

Exposes the RuleEngine and DecisionLog for the War Room APL panel.
"""

import logging

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/apl",
    tags=["apl"],
    dependencies=[Depends(require_authenticated_request)],
)

_rule_engine = None
_decision_log = None


class RuleDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(
        default=None,
        description="Optional operator-supplied rule rejection reason.",
    )


def init_apl_api(rule_engine=None, decision_log=None) -> None:
    global _rule_engine, _decision_log
    _rule_engine = rule_engine
    _decision_log = decision_log
    logger.info("APL API initialised (rules=%s, decisions=%s)",
                _rule_engine is not None, _decision_log is not None)


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "status": status_code})


@router.get("/rules")
async def apl_rules(status: str = Query("", description="Filter by status")):
    """List automation rules."""
    try:
        if _rule_engine is None:
            return {"rules": [], "message": "Rule engine not initialised"}

        rules = _rule_engine.list_rules(status=status or None)
        return {
            "rules": [
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "pattern_type": r.pattern_type,
                    "status": r.status,
                    "conditions_summary": str(r.conditions),
                    "auto_decisions_today": r.auto_decisions_today,
                    "auto_decisions_total": r.auto_decisions_total,
                    "max_daily": r.max_auto_decisions_per_day,
                    "max_total": r.max_auto_decisions_total,
                    "activated_at": r.activated_at,
                    "created_at": r.created_at,
                }
                for r in rules
            ],
            "total": len(rules),
        }
    except Exception as exc:
        logger.error("apl_rules error: %s", exc)
        return _safe_error(500, "Failed to get APL rules")


@router.get("/proposals")
async def apl_proposals():
    """List proposed (pending) automation rules."""
    try:
        if _rule_engine is None:
            return {"proposals": [], "message": "Rule engine not initialised"}

        rules = _rule_engine.list_rules(status="proposed")
        return {
            "proposals": [
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "pattern_type": r.pattern_type,
                    "conditions": r.conditions,
                    "created_at": r.created_at,
                }
                for r in rules
            ],
            "total": len(rules),
        }
    except Exception as exc:
        logger.error("apl_proposals error: %s", exc)
        return _safe_error(500, "Failed to get APL proposals")


@router.get("/decisions")
async def apl_decisions(limit: int = Query(50, ge=1, le=200)):
    """Recent APL decisions."""
    try:
        if _decision_log is None:
            return {"decisions": [], "total": 0, "message": "Decision log not initialised"}

        records = _decision_log.get_recent(limit)
        return {
            "decisions": [
                {
                    "id": r.id,
                    "capability": r.context.capability,
                    "target": r.context.target,
                    "risk_tier": int(r.context.risk_tier),
                    "decision": r.decision,
                    "is_auto": bool(r.rule_id),
                    "rule_id": r.rule_id,
                    "reason": r.reason,
                    "recorded_at": r.recorded_at,
                }
                for r in records
            ],
            "total": _decision_log.total_decisions,
            "auto_approved": _decision_log.auto_approved_count,
        }
    except Exception as exc:
        logger.error("apl_decisions error: %s", exc)
        return _safe_error(500, "Failed to get APL decisions")


@router.get("/circuit-breakers")
async def apl_circuit_breakers():
    """Rules that have hit their daily limit."""
    try:
        if _rule_engine is None:
            return {"circuit_breakers": [], "message": "Rule engine not initialised"}

        triggered = _rule_engine.check_circuit_breakers()
        return {
            "circuit_breakers": [
                {
                    "id": r.id,
                    "name": r.name,
                    "daily_usage": r.auto_decisions_today,
                    "max_daily": r.max_auto_decisions_per_day,
                }
                for r in triggered
            ],
            "total": len(triggered),
        }
    except Exception as exc:
        logger.error("apl_circuit_breakers error: %s", exc)
        return _safe_error(500, "Failed to get circuit breakers")


@router.post("/rules/{rule_id}/pause")
async def pause_rule(
    rule_id: str,
    _authz: None = Depends(require_operator_capability("apl.admin")),
):
    """Pause an active rule."""
    try:
        if _rule_engine is None:
            return _safe_error(400, "Rule engine not initialised")
        _rule_engine.pause_rule(rule_id)
        return {"status": "paused", "rule_id": rule_id}
    except Exception as exc:
        logger.error("pause_rule error: %s", exc)
        return _safe_error(500, "Failed to pause rule")


@router.post("/rules/{rule_id}/resume")
async def resume_rule(
    rule_id: str,
    _authz: None = Depends(require_operator_capability("apl.admin")),
):
    """Resume a paused rule."""
    try:
        if _rule_engine is None:
            return _safe_error(400, "Rule engine not initialised")
        _rule_engine.resume_rule(rule_id)
        return {"status": "active", "rule_id": rule_id}
    except Exception as exc:
        logger.error("resume_rule error: %s", exc)
        return _safe_error(500, "Failed to resume rule")


@router.post("/rules/{rule_id}/revoke")
async def revoke_rule(
    rule_id: str,
    request: Request,
    body: RuleDecisionRequest | None = None,
    _authz: None = Depends(require_operator_capability("apl.admin")),
):
    """Revoke a rule permanently."""
    try:
        if _rule_engine is None:
            return _safe_error(400, "Rule engine not initialised")
        reason = body.reason if body and body.reason else "Revoked via War Room"
        _rule_engine.revoke_rule(rule_id, reason=reason)

        # Governance receipt
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request,
            ActionType.APL_RULE_REJECTED,
            action_name="revoke_rule",
            inputs={"rule_id": rule_id, "reason": reason},
        )

        return {"status": "revoked", "rule_id": rule_id}
    except Exception as exc:
        logger.error("revoke_rule error: %s", exc)
        return _safe_error(500, "Failed to revoke rule")


@router.post("/proposals/{rule_id}/activate")
async def activate_proposal(
    rule_id: str,
    request: Request,
    _authz: None = Depends(require_operator_capability("apl.admin")),
):
    """Activate a proposed rule."""
    try:
        if _rule_engine is None:
            return _safe_error(400, "Rule engine not initialised")
        _rule_engine.activate_rule(rule_id)

        # Governance receipt
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request,
            ActionType.APL_RULE_APPROVED,
            action_name="activate_proposal",
            inputs={"rule_id": rule_id},
        )

        return {"status": "active", "rule_id": rule_id}
    except Exception as exc:
        logger.error("activate_proposal error: %s", exc)
        return _safe_error(500, "Failed to activate rule")


@router.post("/proposals/{rule_id}/decline")
async def decline_proposal(
    rule_id: str,
    request: Request,
    body: RuleDecisionRequest | None = None,
    _authz: None = Depends(require_operator_capability("apl.admin")),
):
    """Decline a proposed rule."""
    try:
        if _rule_engine is None:
            return _safe_error(400, "Rule engine not initialised")
        reason = body.reason if body and body.reason else "Declined via War Room"
        _rule_engine.decline_rule(rule_id, reason=reason)

        # Governance receipt
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType
        emit_governance_receipt(
            request,
            ActionType.APL_RULE_REJECTED,
            action_name="decline_proposal",
            inputs={"rule_id": rule_id, "reason": reason},
        )

        return {"status": "declined", "rule_id": rule_id}
    except Exception as exc:
        logger.error("decline_proposal error: %s", exc)
        return _safe_error(500, "Failed to decline rule")
