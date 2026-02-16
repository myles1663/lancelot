"""
Governance API — /api/governance/*

Exposes governance pipeline stats, decisions, and approval queue
for the War Room Governance Dashboard.

Includes MCP Sentry T3 action approvals alongside trust graduation
proposals and APL rule proposals.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/governance", tags=["governance"])

_trust_ledger = None
_rule_engine = None
_decision_log = None
_mcp_sentry = None


def init_governance_api(trust_ledger=None, rule_engine=None, decision_log=None, mcp_sentry=None) -> None:
    """Wire governance subsystem instances."""
    global _trust_ledger, _rule_engine, _decision_log, _mcp_sentry
    _trust_ledger = trust_ledger
    _rule_engine = rule_engine
    _decision_log = decision_log
    _mcp_sentry = mcp_sentry
    logger.info("Governance API initialised (trust=%s, rules=%s, decisions=%s, sentry=%s)",
                _trust_ledger is not None, _rule_engine is not None,
                _decision_log is not None, _mcp_sentry is not None)


def _safe_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "status": status_code})


def _log_sentry_decision(approval_id: str, capability: str, target: str, decision: str, reason: str) -> None:
    """Record a sentry approval/denial in the decision log for the Recent Decisions panel."""
    if _decision_log is None:
        return
    try:
        from governance.approval_learning.models import DecisionContext, RiskTier
        context = DecisionContext.from_action(
            capability=capability,
            target=target,
            risk_tier=RiskTier.T3_IRREVERSIBLE,
        )
        _decision_log.record(
            context=context,
            decision=decision,
            reason=reason,
        )
    except Exception as exc:
        logger.warning("Failed to log sentry decision: %s", exc)


@router.get("/stats")
async def governance_stats():
    """Overall governance pipeline statistics."""
    try:
        from governance.war_room_panel import render_trust_panel

        stats: dict = {"trust": {}, "apl": {}}

        if _trust_ledger:
            trust_data = render_trust_panel(_trust_ledger)
            stats["trust"] = trust_data.get("summary", {})

        if _rule_engine and _decision_log:
            from governance.approval_learning.war_room_panel import render_apl_panel
            apl_data = render_apl_panel(_rule_engine, _decision_log)
            stats["apl"] = apl_data.get("summary", {})

        return {"stats": stats}
    except Exception as exc:
        logger.error("governance_stats error: %s", exc)
        return _safe_error(500, "Failed to get governance stats")


@router.get("/decisions")
async def governance_decisions(
    limit: int = Query(50, ge=1, le=200),
    capability: Optional[str] = Query(None),
):
    """Recent governance decisions from the decision log."""
    try:
        if _decision_log is None:
            return {"decisions": [], "total": 0, "message": "Decision log not initialised"}

        if capability:
            records = _decision_log.get_by_capability(capability)[:limit]
        else:
            records = _decision_log.get_recent(limit)

        return {
            "decisions": [
                {
                    "id": r.id,
                    "capability": r.context.capability,
                    "target": r.context.target,
                    "risk_tier": int(r.context.risk_tier),
                    "decision": r.decision,
                    "reason": r.reason,
                    "rule_id": r.rule_id,
                    "is_auto": bool(r.rule_id),
                    "recorded_at": r.recorded_at,
                }
                for r in records
            ],
            "total": _decision_log.total_decisions,
        }
    except Exception as exc:
        logger.error("governance_decisions error: %s", exc)
        return _safe_error(500, "Failed to get decisions")


@router.get("/approvals")
async def governance_approvals():
    """Pending T3 action approvals, graduation proposals, and APL proposals."""
    try:
        pending = []

        # MCP Sentry T3 action approvals
        if _mcp_sentry:
            _mcp_sentry._cleanup_expired()
            for req_id, req in _mcp_sentry.pending_requests.items():
                if req.get("status") == "PENDING":
                    pending.append({
                        "id": req_id,
                        "type": "sentry",
                        "capability": req.get("tool", "unknown"),
                        "params": req.get("params", {}),
                        "status": "pending",
                        "created_at": req.get("timestamp", ""),
                    })

        # Trust graduation proposals
        if _trust_ledger:
            for p in _trust_ledger.pending_proposals():
                pending.append({
                    "id": p.id,
                    "type": "graduation",
                    "capability": p.capability,
                    "scope": p.scope,
                    "current_tier": int(p.current_tier),
                    "proposed_tier": int(p.proposed_tier),
                    "consecutive_successes": p.consecutive_successes,
                    "status": p.status,
                    "created_at": p.created_at,
                })

        # APL rule proposals
        if _rule_engine:
            for rule in _rule_engine.list_rules(status="proposed"):
                pending.append({
                    "id": rule.id,
                    "type": "apl_rule",
                    "name": rule.name,
                    "description": rule.description,
                    "pattern_type": rule.pattern_type,
                    "status": rule.status,
                    "created_at": rule.created_at,
                })

        return {"approvals": pending, "total": len(pending)}
    except Exception as exc:
        logger.error("governance_approvals error: %s", exc)
        return _safe_error(500, "Failed to get approvals")


@router.post("/approvals/{approval_id}/approve")
async def approve_item(approval_id: str, request: Request):
    """Approve a T3 action, graduation proposal, or APL rule."""
    try:
        data = await request.json() if request.headers.get("content-type") else {}
        reason = data.get("reason", "Approved via War Room")

        # Try MCP Sentry T3 action first
        if _mcp_sentry and approval_id in _mcp_sentry.pending_requests:
            req = _mcp_sentry.pending_requests[approval_id]
            if _mcp_sentry.approve_request(approval_id):
                logger.info("Sentry approval granted via War Room: %s (reason: %s)", approval_id, reason)
                _log_sentry_decision(
                    approval_id,
                    capability=req.get("tool", "unknown"),
                    target=str(req.get("params", {})),
                    decision="approved",
                    reason=reason,
                )
                return {"status": "approved", "id": approval_id, "type": "sentry"}

        # Try graduation proposal
        if _trust_ledger:
            for p in _trust_ledger.pending_proposals():
                if p.id == approval_id:
                    _trust_ledger.apply_graduation(approval_id, approved=True, reason=reason)
                    return {"status": "approved", "id": approval_id, "type": "graduation"}

        # Try APL rule
        if _rule_engine:
            for rule in _rule_engine.list_rules(status="proposed"):
                if rule.id == approval_id:
                    _rule_engine.activate_rule(approval_id)
                    return {"status": "approved", "id": approval_id, "type": "apl_rule"}

        return _safe_error(404, f"Approval item {approval_id} not found")
    except Exception as exc:
        logger.error("approve_item error: %s", exc)
        return _safe_error(500, "Failed to approve item")


@router.post("/approvals/{approval_id}/deny")
async def deny_item(approval_id: str, request: Request):
    """Deny a T3 action, graduation proposal, or APL rule."""
    try:
        data = await request.json() if request.headers.get("content-type") else {}
        reason = data.get("reason", "Denied via War Room")

        # Try MCP Sentry T3 action first
        if _mcp_sentry and approval_id in _mcp_sentry.pending_requests:
            req = _mcp_sentry.pending_requests[approval_id]
            if _mcp_sentry.deny_request(approval_id):
                logger.info("Sentry approval denied via War Room: %s (reason: %s)", approval_id, reason)
                _log_sentry_decision(
                    approval_id,
                    capability=req.get("tool", "unknown"),
                    target=str(req.get("params", {})),
                    decision="denied",
                    reason=reason,
                )
                return {"status": "denied", "id": approval_id, "type": "sentry"}

        # Try graduation proposal
        if _trust_ledger:
            for p in _trust_ledger.pending_proposals():
                if p.id == approval_id:
                    _trust_ledger.apply_graduation(approval_id, approved=False, reason=reason)
                    return {"status": "denied", "id": approval_id, "type": "graduation"}

        # Try APL rule
        if _rule_engine:
            for rule in _rule_engine.list_rules(status="proposed"):
                if rule.id == approval_id:
                    _rule_engine.decline_rule(approval_id, reason=reason)
                    return {"status": "denied", "id": approval_id, "type": "apl_rule"}

        return _safe_error(404, f"Approval item {approval_id} not found")
    except Exception as exc:
        logger.error("deny_item error: %s", exc)
        return _safe_error(500, "Failed to deny item")
