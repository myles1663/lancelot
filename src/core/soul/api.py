# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""
Soul API endpoints for soul status, proposals, and activation.

Endpoints:
    GET  /soul/status                    — active version + pending proposals
    POST /soul/proposals/{id}/approve    — owner approves a proposal
    POST /soul/proposals/{id}/activate   — owner activates an approved proposal

All mutation endpoints require the Soul admin capability.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

import json
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import get_api_key_identity, request_has_capability, resolve_operator_identity

from src.core.soul.store import (
    Soul,
    SoulStoreError,
    _resolve_soul_dir,
    get_active_version,
    set_active_version,
    list_versions,
)
from src.core.soul.amendments import (
    ProposalStatus,
    create_proposal,
    list_proposals,
    get_proposal,
    save_proposals,
)
from src.core.soul.linter import lint, lint_or_raise
from src.core.soul.behavior import evaluate_soul_behavior

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/soul",
    tags=["soul"],
    dependencies=[Depends(require_authenticated_request)],
)

# Soul directory — configurable via env or default
_SOUL_DIR: Optional[str] = os.environ.get("SOUL_DIR", None)
_proposals_lock = threading.Lock()

# ActionCard factory set by gateway.py during startup
_actioncard_factory = None
_runtime_reload_callback = None


class ProposeAmendmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_yaml: str = ""


class EvaluateSoulRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    scope: str = "workspace"
    target: Optional[str] = None


class BehaviorContractCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    label: str
    capability: str
    scope: str = "workspace"
    target: Optional[str] = None
    expected: Literal["allowed", "requires_approval", "blocked"]


class SaveBehaviorContractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[BehaviorContractCase]


def init_soul_actioncards(factory) -> None:
    """Inject ActionCard factory for proposal notifications."""
    global _actioncard_factory
    _actioncard_factory = factory


def init_soul_runtime(reload_callback) -> None:
    """Inject a runtime callback for live Soul activation."""
    global _runtime_reload_callback
    _runtime_reload_callback = reload_callback


def _approve_proposal_direct(
    proposal_id: str,
    *,
    actor: str = "operator",
):
    """Approve a pending Soul proposal outside the HTTP request layer."""
    with _proposals_lock:
        proposals = list_proposals(_SOUL_DIR)
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise HTTPException(status_code=404, detail="Proposal not found")

        if target.status != ProposalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal status is '{target.status}', expected 'pending'",
            )

        target.status = ProposalStatus.APPROVED
        save_proposals(proposals, _SOUL_DIR)

    logger.info(
        "soul_approved: proposal=%s, version=%s, actor=%s",
        target.id,
        target.proposed_version,
        actor,
    )
    return {"status": "approved", "proposal_id": target.id}


def _reject_proposal_direct(
    proposal_id: str,
    *,
    actor: str = "operator",
):
    """Reject a pending Soul proposal outside the HTTP request layer."""
    with _proposals_lock:
        proposals = list_proposals(_SOUL_DIR)
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise HTTPException(status_code=404, detail="Proposal not found")

        if target.status != ProposalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal status is '{target.status}', expected 'pending'",
            )

        target.status = ProposalStatus.REJECTED
        save_proposals(proposals, _SOUL_DIR)

    logger.info(
        "soul_rejected: proposal=%s, version=%s, actor=%s",
        target.id,
        target.proposed_version,
        actor,
    )
    return {"status": "denied", "proposal_id": target.id}


def _set_soul_dir(soul_dir: str) -> None:
    """Set the soul directory (used in tests)."""
    global _SOUL_DIR
    _SOUL_DIR = soul_dir


def _behavior_contracts_file() -> Path:
    from src.core.soul.store import _resolve_soul_dir

    d = _resolve_soul_dir(_SOUL_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / "behavior_contracts.json"


def _load_behavior_contracts() -> dict:
    path = _behavior_contracts_file()
    if not path.exists():
        return {"versions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("versions", {}), dict):
            return data
    except Exception as exc:
        logger.warning("Failed to load behavior contracts: %s", exc)
    return {"versions": {}}


def _save_behavior_contracts(data: dict) -> None:
    path = _behavior_contracts_file()
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _contract_for_version(version: str) -> dict:
    data = _load_behavior_contracts()
    versions = data.setdefault("versions", {})
    contract = versions.get(version)
    if not isinstance(contract, dict):
        contract = {"version": version, "cases": []}
    cases = contract.get("cases", [])
    if not isinstance(cases, list):
        cases = []
    return {"version": version, "cases": cases}


def _normalize_contract_cases(cases: list[BehaviorContractCase]) -> list[dict]:
    normalized = []
    seen = set()
    for case in cases:
        label = case.label.strip()
        capability = case.capability.strip()
        scope = case.scope.strip() or "workspace"
        target = case.target.strip() if case.target and case.target.strip() else None
        if not label:
            raise HTTPException(status_code=400, detail="Contract case label is required")
        if not capability:
            raise HTTPException(status_code=400, detail="Contract case capability is required")
        key = (capability, scope, target or "", case.expected)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "id": case.id.strip() or uuid4().hex[:12],
            "label": label,
            "capability": capability,
            "scope": scope,
            "target": target,
            "expected": case.expected,
        })
    return normalized


async def _parse_request_model(request: Request, model_cls: type[BaseModel]) -> BaseModel:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON") from exc
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _verify_owner(request: Request) -> bool:
    """Check that the request has the Soul admin capability."""
    return request_has_capability(request, "soul.admin")


def _get_active_overlays() -> list:
    """Return metadata about currently active soul overlays."""
    try:
        from src.core.soul.layers import load_overlays
        overlays = load_overlays(_SOUL_DIR)
        return [
            {
                "name": o.overlay_name,
                "feature_flag": o.feature_flag,
                "description": o.description,
                "risk_rules_count": len(o.risk_rules),
                "tone_invariants_count": len(o.tone_invariants),
                "memory_ethics_count": len(o.memory_ethics),
                "autonomy_additions": len(o.autonomy_posture.allowed_autonomous) + len(o.autonomy_posture.requires_approval),
            }
            for o in overlays
        ]
    except Exception as exc:
        logger.debug("Could not load overlays: %s", exc)
        return []


def _version_source_from_proposal(proposal) -> dict:
    author = proposal.author or ""
    if author.startswith("template:"):
        return {
            "kind": "template",
            "template_name": author.removeprefix("template:"),
            "proposal_id": proposal.id,
            "created_at": proposal.created_at,
        }
    return {
        "kind": "proposal",
        "author": author or "unknown",
        "proposal_id": proposal.id,
        "created_at": proposal.created_at,
    }


def _build_version_sources(proposals: list, versions: list[str]) -> dict:
    sources = {
        version: {"kind": "baseline"}
        for version in versions
    }
    for proposal in proposals:
        if proposal.status != ProposalStatus.ACTIVATED:
            continue
        if proposal.proposed_version not in sources:
            continue
        sources[proposal.proposed_version] = _version_source_from_proposal(proposal)
    return sources


def _validate_soul_version_for_activation(version: str) -> Soul:
    d = _resolve_soul_dir(_SOUL_DIR)
    version_file = d / "soul_versions" / f"soul_{version}.yaml"
    if not version_file.exists():
        raise HTTPException(status_code=404, detail=f"Soul version not found: {version}")

    try:
        proposed_dict = yaml.safe_load(version_file.read_text(encoding="utf-8"))
        soul = Soul(**proposed_dict)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Soul version validation failed for {version}: {exc}",
        ) from exc

    try:
        lint_or_raise(soul)
    except SoulStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return soul


def _emit_soul_version_receipt(
    request: Request,
    *,
    previous_version: str,
    active_version: str,
    source: str,
    soul: Optional[Soul] = None,
    proposal_id: Optional[str] = None,
) -> None:
    try:
        from src.core.governance_receipts import emit_governance_receipt
        from src.shared.receipts import ActionType

        soul_version_hash = active_version
        if soul is not None:
            try:
                from src.federation.soul_compat import hash_soul
                soul_version_hash = hash_soul(soul)
            except Exception as hash_exc:
                logger.debug("Failed to hash Soul version %s: %s", active_version, hash_exc)

        emit_governance_receipt(
            request,
            ActionType.SOUL_VERSION_PINNED,
            action_name="activate_soul_version",
            inputs={
                "previous_version": previous_version,
                "target_version": active_version,
                "soul_version_hash": soul_version_hash,
                "source": source,
                "proposal_id": proposal_id,
            },
            outputs={
                "active_version": active_version,
            },
            metadata={
                "soul_version_hash": soul_version_hash,
                "previous_version": previous_version,
                "source": source,
            },
        )
    except Exception as exc:
        logger.warning("Failed to emit Soul version activation receipt: %s", exc)


def _load_merged_active_soul() -> Soul:
    """Load the active Soul and apply any currently enabled overlays."""
    from src.core.soul.store import load_active_soul
    from src.core.soul.layers import load_overlays, merge_soul

    active_soul = load_active_soul(_SOUL_DIR)
    overlays = load_overlays(_SOUL_DIR)
    if overlays:
        return merge_soul(active_soul, overlays)
    return active_soul


# ---------------------------------------------------------------------------
# GET /soul/status
# ---------------------------------------------------------------------------

@router.get("/status")
async def soul_status():
    """Return the active soul version, pending proposals, and active overlays."""
    try:
        version = get_active_version(_SOUL_DIR)
        versions = list_versions(_SOUL_DIR)
        proposals = list_proposals(_SOUL_DIR)
        version_sources = _build_version_sources(proposals, versions)
        actionable = [
            p.model_dump()
            for p in proposals
            if p.status in {ProposalStatus.PENDING, ProposalStatus.APPROVED}
        ]

        # Load active overlays
        active_overlays = _get_active_overlays()

        return {
            "active_version": version,
            "available_versions": versions,
            "active_source": version_sources.get(version, {"kind": "unknown"}),
            "version_sources": version_sources,
            "pending_proposals": actionable,
            "active_overlays": active_overlays,
        }
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# GET /soul/content — full parsed soul document
# ---------------------------------------------------------------------------

@router.get("/content")
async def soul_content():
    """Return the full active soul document (with overlays merged) as structured JSON."""
    try:
        from src.core.soul.store import load_active_soul, _resolve_soul_dir
        from src.core.soul.layers import load_overlays

        base_soul = load_active_soul(_SOUL_DIR)
        d = _resolve_soul_dir(_SOUL_DIR)
        version_file = d / "soul_versions" / f"soul_{base_soul.version}.yaml"
        raw_yaml = version_file.read_text(encoding="utf-8") if version_file.exists() else ""

        # Apply overlays to show the actual merged soul
        overlays = load_overlays(_SOUL_DIR)
        active_overlays = _get_active_overlays()

        if overlays:
            merged = _load_merged_active_soul()
            return {
                "soul": merged.model_dump(),
                "raw_yaml": raw_yaml,
                "active_overlays": active_overlays,
            }

        return {
            "soul": base_soul.model_dump(),
            "raw_yaml": raw_yaml,
            "active_overlays": [],
        }
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# POST /soul/evaluate - explain active Soul behavior for one capability
# ---------------------------------------------------------------------------

@router.post("/evaluate")
async def evaluate_soul(request: Request):
    """Evaluate a capability against the active merged Soul.

    This is a read-only diagnostic endpoint for War Room smoke tests and
    operator review. It does not execute the requested capability.
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    try:
        body = await _parse_request_model(request, EvaluateSoulRequest)
        if not body.capability.strip():
            raise HTTPException(status_code=400, detail="capability is required")

        soul = _load_merged_active_soul()
        decision = evaluate_soul_behavior(
            soul,
            body.capability,
            scope=body.scope,
            target=body.target,
        )
        return decision.to_dict()
    except HTTPException:
        raise
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    except Exception as exc:
        logger.error("evaluate_soul error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# POST /soul/propose — create amendment proposal from edited YAML
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Behavior contracts - operator-managed expected behavior for active Soul
# ---------------------------------------------------------------------------

@router.get("/behavior-contract")
async def get_behavior_contract(request: Request):
    """Return the behavior contract for the active Soul version."""
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    try:
        version = get_active_version(_SOUL_DIR)
        return _contract_for_version(version)
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.put("/behavior-contract")
async def save_behavior_contract(request: Request):
    """Save expected behavior cases for the active Soul version.

    This updates operator QA expectations only. It does not mutate or activate
    the Soul constitution.
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    try:
        body = await _parse_request_model(request, SaveBehaviorContractRequest)
        version = get_active_version(_SOUL_DIR)
        data = _load_behavior_contracts()
        versions = data.setdefault("versions", {})
        contract = {
            "version": version,
            "cases": _normalize_contract_cases(body.cases),
        }
        versions[version] = contract
        _save_behavior_contracts(data)
        return contract
    except HTTPException:
        raise
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    except Exception as exc:
        logger.error("save_behavior_contract error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/behavior-contract/run")
async def run_behavior_contract(request: Request):
    """Evaluate all saved contract cases against the active merged Soul."""
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    try:
        version = get_active_version(_SOUL_DIR)
        contract = _contract_for_version(version)
        soul = _load_merged_active_soul()
        results = []
        for case in contract["cases"]:
            decision = evaluate_soul_behavior(
                soul,
                case["capability"],
                scope=case.get("scope") or "workspace",
                target=case.get("target"),
            )
            result = decision.to_dict()
            result.update({
                "id": case["id"],
                "label": case["label"],
                "expected": case["expected"],
                "passed": result["decision"] == case["expected"],
            })
            results.append(result)

        return {
            "version": version,
            "count": len(results),
            "passed": sum(1 for result in results if result["passed"]),
            "failed": sum(1 for result in results if not result["passed"]),
            "results": results,
        }
    except SoulStoreError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    except Exception as exc:
        logger.error("run_behavior_contract error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/propose")
async def propose_amendment(request: Request):
    """Create a soul amendment proposal from edited YAML.

    Body: {"proposed_yaml": "<full yaml text>"}
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    try:
        body = await _parse_request_model(request, ProposeAmendmentRequest)
        proposed_yaml = body.proposed_yaml
        identity = resolve_operator_identity(request)
        if identity is None:
            identity = get_api_key_identity(request)
        author = identity.display_name or identity.operator_id or "owner"

        if not proposed_yaml.strip():
            raise HTTPException(status_code=400, detail="proposed_yaml is required")

        # Validate the YAML parses and passes schema
        proposed_dict = yaml.safe_load(proposed_yaml)
        soul = Soul(**proposed_dict)

        # Run linter — return warnings but don't block
        issues = lint(soul)
        warnings = [{"rule": i.rule, "severity": i.severity.value, "message": i.message}
                    for i in issues]

        # Check for critical issues
        critical = [w for w in warnings if w["severity"] == "critical"]
        if critical:
            return JSONResponse(status_code=422, content={
                "error": "Critical linter issues found",
                "issues": warnings,
            })

        # Create proposal
        current_version = get_active_version(_SOUL_DIR)
        proposal = create_proposal(
            from_version=current_version,
            proposed_yaml_text=proposed_yaml,
            author=author,
            soul_dir=_SOUL_DIR,
        )

        # Emit ActionCard for cross-channel approval notification
        if _actioncard_factory:
            try:
                _actioncard_factory.from_soul_proposal(
                    proposal_id=proposal.id,
                    version=proposal.proposed_version,
                    diff_summary=proposal.diff_summary or [],
                )
            except Exception as _ac_exc:
                logger.warning("Failed to create ActionCard for soul proposal: %s", _ac_exc)

        return {
            "proposal_id": proposal.id,
            "proposed_version": proposal.proposed_version,
            "diff_summary": proposal.diff_summary,
            "warnings": warnings,
            "status": proposal.status.value,
        }
    except HTTPException:
        raise
    except SoulStoreError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    except Exception as exc:
        logger.error("propose_amendment error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# POST /soul/proposals/{id}/approve
# ---------------------------------------------------------------------------

@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, request: Request):
    """Approve a pending soul amendment proposal. Owner only."""
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")
    identity = resolve_operator_identity(request) or get_api_key_identity(request)
    actor = identity.display_name or identity.operator_id or "operator"
    return _approve_proposal_direct(proposal_id, actor=actor)


# ---------------------------------------------------------------------------
# POST /soul/proposals/{id}/activate
# ---------------------------------------------------------------------------

@router.post("/proposals/{proposal_id}/activate")
async def activate_proposal(proposal_id: str, request: Request):
    """Activate an approved soul amendment proposal. Owner only.

    Steps:
    1. Verify owner identity
    2. Check proposal is approved
    3. Write proposed YAML to soul_versions/
    4. Validate with Pydantic + linter
    5. Set ACTIVE pointer
    6. Log receipt
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    with _proposals_lock:
        proposals = list_proposals(_SOUL_DIR)
        target = None
        for p in proposals:
            if p.id == proposal_id:
                target = p
                break

        if target is None:
            raise HTTPException(status_code=404, detail="Proposal not found")

        if target.status != ProposalStatus.APPROVED:
            raise HTTPException(
                status_code=409,
                detail=f"Proposal must be approved first (status='{target.status}')",
            )

        if not target.proposed_yaml:
            raise HTTPException(status_code=400, detail="Proposal has no YAML content")

        # Parse and validate
        try:
            proposed_dict = yaml.safe_load(target.proposed_yaml)
            soul = Soul(**proposed_dict)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Proposed soul validation failed: {exc}",
            )

        # Run linter
        try:
            lint_or_raise(soul)
        except SoulStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        # Write version file
        from src.core.soul.store import _resolve_soul_dir
        d = _resolve_soul_dir(_SOUL_DIR)
        version_file = d / "soul_versions" / f"soul_{soul.version}.yaml"
        version_file.write_text(target.proposed_yaml, encoding="utf-8")

        previous_version = get_active_version(_SOUL_DIR)

        # Set active pointer
        set_active_version(soul.version, _SOUL_DIR)

        # Refresh the live runtime before reporting success.
        if _runtime_reload_callback is not None:
            try:
                runtime_soul = _load_merged_active_soul()
                _runtime_reload_callback(runtime_soul)
            except Exception as exc:
                set_active_version(previous_version, _SOUL_DIR)
                raise HTTPException(
                    status_code=500,
                    detail=f"Activated Soul version {soul.version} on disk but failed to refresh runtime: {exc}",
                )

        # Update proposal status
        target.status = ProposalStatus.ACTIVATED
        save_proposals(proposals, _SOUL_DIR)

    logger.info("soul_activated: proposal=%s, version=%s",
                target.id, soul.version)
    _emit_soul_version_receipt(
        request,
        previous_version=previous_version,
        active_version=soul.version,
        source="proposal",
        soul=soul,
        proposal_id=target.id,
    )
    return {
        "status": "activated",
        "proposal_id": target.id,
        "active_version": soul.version,
    }


# ---------------------------------------------------------------------------
# POST /soul/versions/{version}/activate
# ---------------------------------------------------------------------------

@router.post("/versions/{version}/activate")
async def activate_existing_version(version: str, request: Request):
    """Activate an existing Soul version after owner confirmation.

    This is the governed rollback/switch path for already-retained versions.
    It validates the target Soul with the linter, updates the ACTIVE pointer,
    and refreshes live runtime subscribers. If runtime refresh fails, the
    ACTIVE pointer is rolled back to the prior version.
    """
    if not _verify_owner(request):
        raise HTTPException(status_code=403, detail="Owner identity required")

    with _proposals_lock:
        previous_version = get_active_version(_SOUL_DIR)
        if version == previous_version:
            return {
                "status": "unchanged",
                "proposal_id": None,
                "active_version": version,
                "previous_version": previous_version,
            }

        soul = _validate_soul_version_for_activation(version)
        set_active_version(soul.version, _SOUL_DIR)

        if _runtime_reload_callback is not None:
            try:
                runtime_soul = _load_merged_active_soul()
                _runtime_reload_callback(runtime_soul)
            except Exception as exc:
                set_active_version(previous_version, _SOUL_DIR)
                raise HTTPException(
                    status_code=500,
                    detail=f"Activated Soul version {soul.version} on disk but failed to refresh runtime: {exc}",
                ) from exc

    logger.info("soul_version_activated: version=%s previous=%s", soul.version, previous_version)
    _emit_soul_version_receipt(
        request,
        previous_version=previous_version,
        active_version=soul.version,
        source="version_history",
        soul=soul,
    )
    return {
        "status": "activated",
        "proposal_id": None,
        "active_version": soul.version,
        "previous_version": previous_version,
    }
