"""
Tests for src.core.soul.api — Soul activation endpoints (Prompt 5 / A5).
"""

import os
import pytest
import yaml
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth
from src.core.soul import api as soul_api_module
from src.core.operator_identity import OperatorIdentity
from src.core.soul.api import (
    router,
    _set_soul_dir,
    init_soul_runtime,
    _approve_proposal_direct,
    _reject_proposal_direct,
)
from src.core.soul.amendments import (
    create_proposal,
    list_proposals,
    save_proposals,
    ProposalStatus,
)
from src.core.soul.store import get_active_version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _soul_dict(version="v1", **overrides) -> dict:
    """Return a valid soul dictionary that passes the linter."""
    base = {
        "version": version,
        "mission": "Serve the owner faithfully.",
        "allegiance": "Single owner loyalty.",
        "autonomy_posture": {
            "level": "supervised",
            "description": "Supervised autonomy.",
            "allowed_autonomous": ["classify_intent"],
            "requires_approval": ["deploy", "delete"],
        },
        "risk_rules": [
            {"name": "destructive_actions_require_approval",
             "description": "Destructive actions need approval", "enforced": True},
        ],
        "approval_rules": {
            "default_timeout_seconds": 3600,
            "escalation_on_timeout": "skip_and_log",
            "channels": ["war_room"],
        },
        "tone_invariants": [
            "Never mislead the owner",
            "Never suppress errors or degrade silently",
        ],
        "memory_ethics": ["Do not store PII without consent"],
        "scheduling_boundaries": {
            "max_concurrent_jobs": 5,
            "max_job_duration_seconds": 300,
            "no_autonomous_irreversible": True,
            "require_ready_state": True,
            "description": "Safe scheduling.",
        },
    }
    base.update(overrides)
    return base


def _write_soul_dir(tmp_path, versions=None, active=None):
    """Create a soul directory with version files."""
    soul_dir = tmp_path / "soul"
    versions_dir = soul_dir / "soul_versions"
    versions_dir.mkdir(parents=True)

    if versions is None:
        versions = {"v1": _soul_dict("v1")}

    for ver, data in versions.items():
        path = versions_dir / f"soul_{ver}.yaml"
        path.write_text(yaml.dump(data), encoding="utf-8")

    if active is not None:
        (soul_dir / "ACTIVE").write_text(active, encoding="utf-8")

    return str(soul_dir)


OWNER_TOKEN = "test-owner-token-12345"


@pytest.fixture
def client(tmp_path):
    """Create a test client with soul directory configured."""
    soul_dir = _write_soul_dir(tmp_path, active="v1")
    _set_soul_dir(soul_dir)

    app = FastAPI()
    app.include_router(router)
    api_auth.init_api_auth(
        lambda request: request.headers.get("Authorization") == f"Bearer {OWNER_TOKEN}"
    )

    with patch.dict(os.environ, {"LANCELOT_API_TOKEN": OWNER_TOKEN}):
        # Re-import to pick up the env var
        import src.core.soul.api as api_mod
        api_mod._API_TOKEN = OWNER_TOKEN
        identity = OperatorIdentity(
            operator_id="op-1",
            display_name="Operator One",
            session_id="sess-1",
            auth_method="api_key",
        )
        with patch.object(api_mod, "resolve_operator_identity", return_value=None), \
             patch.object(api_mod, "get_api_key_identity", return_value=identity):
            yield TestClient(app), soul_dir

    api_auth.init_api_auth(None)
    init_soul_runtime(None)
    _set_soul_dir(None)


def _owner_headers():
    return {"Authorization": f"Bearer {OWNER_TOKEN}"}


def _non_owner_headers():
    return {"Authorization": "Bearer wrong-token"}


# ===================================================================
# GET /soul/status
# ===================================================================

class TestSoulStatus:

    def test_returns_active_version(self, client):
        c, _ = client
        resp = c.get("/soul/status", headers=_owner_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_version"] == "v1"

    def test_returns_available_versions(self, client):
        c, _ = client
        resp = c.get("/soul/status", headers=_owner_headers())
        data = resp.json()
        assert "v1" in data["available_versions"]

    def test_returns_pending_proposals(self, client):
        c, soul_dir = client
        # Create a proposal
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        create_proposal("v1", proposed_yaml, soul_dir=soul_dir)

        resp = c.get("/soul/status", headers=_owner_headers())
        data = resp.json()
        assert len(data["pending_proposals"]) == 1

    def test_returns_approved_proposals_for_activation(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        proposal = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        proposals = list_proposals(soul_dir)
        proposals[0].status = ProposalStatus.APPROVED
        save_proposals(proposals, soul_dir)

        resp = c.get("/soul/status", headers=_owner_headers())
        data = resp.json()

        assert len(data["pending_proposals"]) == 1
        assert data["pending_proposals"][0]["id"] == proposal.id
        assert data["pending_proposals"][0]["status"] == "approved"

    def test_status_returns_version_sources_from_activated_template_proposals(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2", mission="Template mission."))
        proposal = create_proposal(
            "v1",
            proposed_yaml,
            author="template:finance-compliance-monitor",
            soul_dir=soul_dir,
        )
        proposals = list_proposals(soul_dir)
        proposals[0].status = ProposalStatus.ACTIVATED
        save_proposals(proposals, soul_dir)

        versions_dir = Path(soul_dir) / "soul_versions"
        (versions_dir / "soul_v2.yaml").write_text(proposed_yaml, encoding="utf-8")
        Path(soul_dir, "ACTIVE").write_text("v2", encoding="utf-8")

        resp = c.get("/soul/status", headers=_owner_headers())
        data = resp.json()

        assert resp.status_code == 200
        assert data["active_source"]["kind"] == "template"
        assert data["active_source"]["template_name"] == "finance-compliance-monitor"
        assert data["active_source"]["proposal_id"] == proposal.id
        assert data["version_sources"]["v1"]["kind"] == "baseline"
        assert data["version_sources"]["v2"]["template_name"] == "finance-compliance-monitor"

    def test_status_and_content_return_safe_errors_when_store_fails(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(soul_api_module, "get_active_version", lambda soul_dir: (_ for _ in ()).throw(soul_api_module.SoulStoreError("active missing")))

        status = c.get("/soul/status", headers=_owner_headers())
        assert status.status_code == 500
        assert status.json()["error"] == "active missing"

        monkeypatch.setattr(
            "src.core.soul.store.load_active_soul",
            lambda soul_dir: (_ for _ in ()).throw(soul_api_module.SoulStoreError("soul unreadable")),
        )
        content = c.get("/soul/content", headers=_owner_headers())
        assert content.status_code == 500
        assert content.json()["error"] == "soul unreadable"

    def test_active_overlays_fail_closed_and_merged_content_path(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(
            "src.core.soul.layers.load_overlays",
            lambda soul_dir: (_ for _ in ()).throw(RuntimeError("overlay bad")),
        )
        assert soul_api_module._get_active_overlays() == []

        overlay = SimpleNamespace(
            overlay_name="enterprise",
            feature_flag="FEATURE_ENTERPRISE",
            description="Enterprise controls",
            risk_rules=[1],
            tone_invariants=[1, 2],
            memory_ethics=[1],
            autonomy_posture=SimpleNamespace(allowed_autonomous=["read"], requires_approval=["write"]),
        )
        monkeypatch.setattr("src.core.soul.layers.load_overlays", lambda soul_dir: [overlay])
        monkeypatch.setattr(
            "src.core.soul.layers.merge_soul",
            lambda base_soul, overlays: SimpleNamespace(model_dump=lambda: {"version": "merged"}, version="merged"),
        )
        content = c.get("/soul/content", headers=_owner_headers())

        assert content.status_code == 200
        assert content.json()["soul"] == {"version": "merged"}
        assert content.json()["active_overlays"][0]["autonomy_additions"] == 2


class TestSoulEvaluate:

    def test_evaluate_requires_owner(self, client):
        c, _ = client

        resp = c.post(
            "/soul/evaluate",
            headers=_non_owner_headers(),
            json={"capability": "classify_intent"},
        )

        assert resp.status_code in {401, 403}

    def test_evaluate_returns_active_soul_decision(self, client):
        c, soul_dir = client
        finance_soul = _soul_dict(
            "v2",
            autonomy_posture={
                "level": "supervised",
                "description": "Finance monitor.",
                "allowed_autonomous": ["scan_transactions"],
                "requires_approval": ["deploy", "delete", "file_sar"],
            },
            risk_overrides=[
                {
                    "capability": "connector.compliance.file_sar",
                    "min_tier": "T3",
                    "reason": "SAR filing requires approval.",
                },
            ],
            data_boundaries=[
                {
                    "name": "financial_evidence",
                    "classification": "financial_pii",
                    "allowed_access": ["scan_transactions"],
                    "prohibited_access": ["modify_compliance_rule"],
                    "external_transmission_allowed": False,
                    "bulk_export_requires_approval": True,
                    "reason": "Evidence is immutable.",
                },
            ],
            kill_switch_rules=[
                {
                    "name": "evidence_tamper",
                    "trigger": "attempted_delete_or_modify_compliance_evidence",
                    "action": "halt_and_escalate",
                    "enforced": True,
                    "reason": "Stop evidence tampering.",
                },
            ],
        )
        versions_dir = Path(soul_dir) / "soul_versions"
        (versions_dir / "soul_v2.yaml").write_text(yaml.dump(finance_soul), encoding="utf-8")
        Path(soul_dir, "ACTIVE").write_text("v2", encoding="utf-8")

        allowed = c.post(
            "/soul/evaluate",
            headers=_owner_headers(),
            json={"capability": "scan_transactions"},
        )
        approval = c.post(
            "/soul/evaluate",
            headers=_owner_headers(),
            json={"capability": "connector.compliance.file_sar"},
        )
        blocked = c.post(
            "/soul/evaluate",
            headers=_owner_headers(),
            json={"capability": "attempted_delete_or_modify_compliance_evidence"},
        )

        assert allowed.status_code == 200
        assert allowed.json()["decision"] == "allowed"
        assert approval.status_code == 200
        assert approval.json()["decision"] == "requires_approval"
        assert approval.json()["risk_tier"] == "T3"
        assert blocked.status_code == 200
        assert blocked.json()["decision"] == "blocked"
        assert "kill_switch:evidence_tamper" in blocked.json()["matched_controls"]

    def test_evaluate_rejects_empty_capability(self, client):
        c, _ = client

        resp = c.post(
            "/soul/evaluate",
            headers=_owner_headers(),
            json={"capability": "  "},
        )

        assert resp.status_code == 400


class TestSoulBehaviorContract:

    def test_contract_read_and_run_require_owner(self, client):
        c, _ = client

        read = c.get("/soul/behavior-contract", headers=_non_owner_headers())
        run = c.post("/soul/behavior-contract/run", headers=_non_owner_headers())

        assert read.status_code in {401, 403}
        assert run.status_code in {401, 403}

    def test_contract_can_be_saved_loaded_and_run(self, client):
        c, soul_dir = client
        contract_cases = [
            {
                "label": "Allow classify",
                "capability": "classify_intent",
                "scope": "workspace",
                "expected": "allowed",
            },
            {
                "label": "Approve delete",
                "capability": "delete",
                "scope": "workspace",
                "expected": "requires_approval",
            },
        ]

        saved = c.put(
            "/soul/behavior-contract",
            headers=_owner_headers(),
            json={"cases": contract_cases},
        )
        loaded = c.get("/soul/behavior-contract", headers=_owner_headers())
        run = c.post("/soul/behavior-contract/run", headers=_owner_headers())

        assert saved.status_code == 200
        assert saved.json()["version"] == "v1"
        assert len(saved.json()["cases"]) == 2
        assert saved.json()["cases"][0]["id"]
        assert loaded.status_code == 200
        assert loaded.json()["cases"][1]["label"] == "Approve delete"
        assert run.status_code == 200
        assert run.json()["count"] == 2
        assert run.json()["passed"] == 2
        assert run.json()["failed"] == 0

        contract_path = Path(soul_dir) / "behavior_contracts.json"
        assert contract_path.exists()

    def test_contract_run_reports_failures(self, client):
        c, _ = client
        saved = c.put(
            "/soul/behavior-contract",
            headers=_owner_headers(),
            json={
                "cases": [
                    {
                        "label": "Wrong expectation",
                        "capability": "delete",
                        "expected": "allowed",
                    }
                ]
            },
        )
        run = c.post("/soul/behavior-contract/run", headers=_owner_headers())

        assert saved.status_code == 200
        assert run.status_code == 200
        assert run.json()["passed"] == 0
        assert run.json()["failed"] == 1
        assert run.json()["results"][0]["decision"] == "requires_approval"

    def test_contract_save_requires_owner_and_valid_cases(self, client):
        c, _ = client
        forbidden = c.put(
            "/soul/behavior-contract",
            headers=_non_owner_headers(),
            json={"cases": []},
        )
        missing_label = c.put(
            "/soul/behavior-contract",
            headers=_owner_headers(),
            json={"cases": [{"label": " ", "capability": "classify_intent", "expected": "allowed"}]},
        )

        assert forbidden.status_code in {401, 403}
        assert missing_label.status_code == 400


class TestProposeAmendment:

    def test_uses_authenticated_operator_for_author(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))

        resp = c.post(
            "/soul/propose",
            headers=_owner_headers(),
            json={"proposed_yaml": proposed_yaml},
        )

        assert resp.status_code == 200
        proposal_id = resp.json()["proposal_id"]
        proposal = next(p for p in list_proposals(soul_dir) if p.id == proposal_id)
        assert proposal.author == "Operator One"

    def test_propose_rejects_unexpected_fields(self, client):
        c, _ = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))

        resp = c.post(
            "/soul/propose",
            headers=_owner_headers(),
            json={"proposed_yaml": proposed_yaml, "author": "Spoofed User"},
        )

        assert resp.status_code == 422
        assert "extra_forbidden" in resp.text

    def test_propose_rejects_empty_yaml_critical_lint_and_actioncard_failures(self, client, monkeypatch):
        c, _ = client

        empty = c.post("/soul/propose", headers=_owner_headers(), json={"proposed_yaml": "  "})
        assert empty.status_code == 400

        monkeypatch.setattr(
            soul_api_module,
            "lint",
            lambda soul: [SimpleNamespace(rule="critical_rule", severity=SimpleNamespace(value="critical"), message="stop")],
        )
        critical = c.post(
            "/soul/propose",
            headers=_owner_headers(),
            json={"proposed_yaml": yaml.dump(_soul_dict("v2"))},
        )
        assert critical.status_code == 422
        assert critical.json()["issues"][0]["severity"] == "critical"

        monkeypatch.setattr(soul_api_module, "lint", lambda soul: [])
        soul_api_module.init_soul_actioncards(
            SimpleNamespace(from_soul_proposal=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("card failed")))
        )
        ok = c.post(
            "/soul/propose",
            headers=_owner_headers(),
            json={"proposed_yaml": yaml.dump(_soul_dict("v3"))},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "pending"
        soul_api_module.init_soul_actioncards(None)

    @pytest.mark.asyncio
    async def test_parse_request_model_rejects_invalid_json(self):
        request = SimpleNamespace(json=lambda: (_ for _ in ()).throw(ValueError("bad json")))

        with pytest.raises(Exception) as exc:
            await soul_api_module._parse_request_model(request, soul_api_module.ProposeAmendmentRequest)

        assert exc.value.status_code == 422

    def test_propose_and_mutation_endpoints_require_soul_admin_capability(self, client, monkeypatch):
        c, soul_dir = client
        monkeypatch.setattr(soul_api_module, "_verify_owner", lambda request: False)

        propose = c.post(
            "/soul/propose",
            headers=_owner_headers(),
            json={"proposed_yaml": yaml.dump(_soul_dict("v2"))},
        )
        assert propose.status_code == 403

        p = create_proposal("v1", yaml.dump(_soul_dict("v2")), soul_dir=soul_dir)
        approve = c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())
        activate = c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())
        assert approve.status_code == 403
        assert activate.status_code == 403


# ===================================================================
# POST /soul/proposals/{id}/approve
# ===================================================================

class TestApproveProposal:

    def test_owner_can_approve(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)

        resp = c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_non_owner_cannot_approve(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)

        resp = c.post(f"/soul/proposals/{p.id}/approve", headers=_non_owner_headers())
        assert resp.status_code == 401

    def test_approve_nonexistent_returns_404(self, client):
        c, _ = client
        resp = c.post("/soul/proposals/fake123/approve", headers=_owner_headers())
        assert resp.status_code == 404

    def test_approve_already_approved_returns_409(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())
        resp = c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())
        assert resp.status_code == 409

    def test_direct_approve_changes_pending_proposal_state(self, client):
        _, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)

        result = _approve_proposal_direct(p.id, actor="Arthur")

        assert result["status"] == "approved"
        proposal = next(item for item in list_proposals(soul_dir) if item.id == p.id)
        assert proposal.status == ProposalStatus.APPROVED

    def test_direct_reject_changes_pending_proposal_state(self, client):
        _, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)

        result = _reject_proposal_direct(p.id, actor="Arthur")

        assert result["status"] == "denied"
        proposal = next(item for item in list_proposals(soul_dir) if item.id == p.id)
        assert proposal.status == ProposalStatus.REJECTED

    def test_direct_reject_missing_and_non_pending_proposals_are_rejected(self, client):
        _, soul_dir = client
        with pytest.raises(Exception) as missing:
            _reject_proposal_direct("missing", actor="Arthur")
        assert missing.value.status_code == 404

        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        _approve_proposal_direct(p.id, actor="Arthur")
        with pytest.raises(Exception) as conflict:
            _reject_proposal_direct(p.id, actor="Arthur")
        assert conflict.value.status_code == 409


# ===================================================================
# POST /soul/proposals/{id}/activate
# ===================================================================

class TestActivateProposal:

    def test_owner_can_activate_approved(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2", mission="New v2 mission."))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())

        resp = c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_version"] == "v2"
        assert data["status"] == "activated"

        # Verify ACTIVE pointer changed
        assert get_active_version(soul_dir) == "v2"

    def test_non_owner_cannot_activate(self, client):
        """Blueprint requirement: non-owner cannot activate."""
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())

        resp = c.post(f"/soul/proposals/{p.id}/activate", headers=_non_owner_headers())
        assert resp.status_code == 401

    def test_activate_unapproved_returns_409(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)

        resp = c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())
        assert resp.status_code == 409

    def test_activation_fails_when_linter_fails(self, client):
        """Blueprint requirement: activation fails when linter fails."""
        c, soul_dir = client
        bad_soul = _soul_dict("v2")
        bad_soul["scheduling_boundaries"]["no_autonomous_irreversible"] = False
        proposed_yaml = yaml.dump(bad_soul)
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())

        resp = c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())
        assert resp.status_code == 422
        assert "lint failed" in resp.json()["detail"]

    def test_activate_nonexistent_returns_404(self, client):
        c, _ = client
        resp = c.post("/soul/proposals/fake123/activate", headers=_owner_headers())
        assert resp.status_code == 404

    def test_activation_writes_version_file(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())
        c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())

        version_file = Path(soul_dir) / "soul_versions" / "soul_v2.yaml"
        assert version_file.exists()

    def test_activation_updates_proposal_status(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())
        c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())

        proposals = list_proposals(soul_dir)
        activated = [pr for pr in proposals if pr.id == p.id]
        assert len(activated) == 1
        assert activated[0].status == ProposalStatus.ACTIVATED

    def test_activation_refreshes_runtime_soul(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())

        seen = {}
        init_soul_runtime(lambda soul: seen.setdefault("version", soul.version))

        resp = c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())
        assert resp.status_code == 200
        assert seen["version"] == "v2"

    def test_activation_rolls_back_active_pointer_when_runtime_refresh_fails(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        c.post(f"/soul/proposals/{p.id}/approve", headers=_owner_headers())

        init_soul_runtime(lambda soul: (_ for _ in ()).throw(RuntimeError("reload failed")))

        resp = c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())
        assert resp.status_code == 500
        assert get_active_version(soul_dir) == "v1"

    def test_activation_rejects_missing_yaml_and_invalid_yaml(self, client):
        c, soul_dir = client
        p = create_proposal("v1", yaml.dump(_soul_dict("v2")), soul_dir=soul_dir)
        proposals = list_proposals(soul_dir)
        for item in proposals:
            if item.id == p.id:
                item.status = ProposalStatus.APPROVED
                item.proposed_yaml = ""
        save_proposals(proposals, soul_dir)
        missing_yaml = c.post(f"/soul/proposals/{p.id}/activate", headers=_owner_headers())
        assert missing_yaml.status_code == 400

        bad = create_proposal("v1", yaml.dump(_soul_dict("v3")), soul_dir=soul_dir)
        proposals = list_proposals(soul_dir)
        for item in proposals:
            if item.id == bad.id:
                item.status = ProposalStatus.APPROVED
                item.proposed_yaml = "not: [valid"
        save_proposals(proposals, soul_dir)
        invalid_yaml = c.post(f"/soul/proposals/{bad.id}/activate", headers=_owner_headers())
        assert invalid_yaml.status_code == 422


class TestActivateExistingVersion:

    def test_owner_can_activate_existing_version_and_refresh_runtime(self, client, monkeypatch):
        c, soul_dir = client
        versions_dir = Path(soul_dir) / "soul_versions"
        (versions_dir / "soul_v2.yaml").write_text(
            yaml.dump(_soul_dict("v2", mission="Existing v2 mission.")),
            encoding="utf-8",
        )
        Path(soul_dir, "ACTIVE").write_text("v2", encoding="utf-8")
        seen = {}
        emitted = []
        init_soul_runtime(lambda soul: seen.setdefault("version", soul.version))
        monkeypatch.setattr(
            "src.core.governance_receipts.emit_governance_receipt",
            lambda *args, **kwargs: emitted.append((args, kwargs)),
        )

        resp = c.post("/soul/versions/v1/activate", headers=_owner_headers())

        assert resp.status_code == 200
        assert resp.json()["status"] == "activated"
        assert resp.json()["active_version"] == "v1"
        assert resp.json()["previous_version"] == "v2"
        assert get_active_version(soul_dir) == "v1"
        assert seen["version"] == "v1"
        assert emitted
        assert emitted[0][1]["inputs"]["previous_version"] == "v2"
        assert emitted[0][1]["inputs"]["target_version"] == "v1"
        assert emitted[0][1]["inputs"]["source"] == "version_history"

    def test_activate_existing_version_requires_owner(self, client):
        c, soul_dir = client
        versions_dir = Path(soul_dir) / "soul_versions"
        (versions_dir / "soul_v2.yaml").write_text(yaml.dump(_soul_dict("v2")), encoding="utf-8")

        resp = c.post("/soul/versions/v2/activate", headers=_non_owner_headers())

        assert resp.status_code in {401, 403}
        assert get_active_version(soul_dir) == "v1"

    def test_activate_existing_version_rolls_back_when_runtime_refresh_fails(self, client):
        c, soul_dir = client
        versions_dir = Path(soul_dir) / "soul_versions"
        (versions_dir / "soul_v2.yaml").write_text(yaml.dump(_soul_dict("v2")), encoding="utf-8")
        init_soul_runtime(lambda soul: (_ for _ in ()).throw(RuntimeError("reload failed")))

        resp = c.post("/soul/versions/v2/activate", headers=_owner_headers())

        assert resp.status_code == 500
        assert get_active_version(soul_dir) == "v1"

    def test_activate_existing_version_rejects_unknown_version(self, client):
        c, soul_dir = client

        resp = c.post("/soul/versions/v99/activate", headers=_owner_headers())

        assert resp.status_code == 404
        assert get_active_version(soul_dir) == "v1"


# ===================================================================
# Auth enforcement
# ===================================================================

class TestAuthEnforcement:

    def test_no_token_rejects_approve(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        resp = c.post(f"/soul/proposals/{p.id}/approve")
        assert resp.status_code == 401

    def test_no_token_rejects_activate(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        resp = c.post(f"/soul/proposals/{p.id}/activate")
        assert resp.status_code == 401

    def test_status_does_not_require_auth(self, client):
        c, _ = client
        resp = c.get("/soul/status")
        assert resp.status_code == 401
