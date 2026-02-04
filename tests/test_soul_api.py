"""
Tests for src.core.soul.api — Soul activation endpoints (Prompt 5 / A5).
"""

import os
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.soul.api import router, _set_soul_dir
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

    with patch.dict(os.environ, {"LANCELOT_API_TOKEN": OWNER_TOKEN}):
        # Re-import to pick up the env var
        import src.core.soul.api as api_mod
        api_mod._API_TOKEN = OWNER_TOKEN
        yield TestClient(app), soul_dir

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
        resp = c.get("/soul/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_version"] == "v1"

    def test_returns_available_versions(self, client):
        c, _ = client
        resp = c.get("/soul/status")
        data = resp.json()
        assert "v1" in data["available_versions"]

    def test_returns_pending_proposals(self, client):
        c, soul_dir = client
        # Create a proposal
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        create_proposal("v1", proposed_yaml, soul_dir=soul_dir)

        resp = c.get("/soul/status")
        data = resp.json()
        assert len(data["pending_proposals"]) == 1


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
        assert resp.status_code == 403

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
        assert resp.status_code == 403

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


# ===================================================================
# Auth enforcement
# ===================================================================

class TestAuthEnforcement:

    def test_no_token_rejects_approve(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        resp = c.post(f"/soul/proposals/{p.id}/approve")
        assert resp.status_code == 403

    def test_no_token_rejects_activate(self, client):
        c, soul_dir = client
        proposed_yaml = yaml.dump(_soul_dict("v2"))
        p = create_proposal("v1", proposed_yaml, soul_dir=soul_dir)
        resp = c.post(f"/soul/proposals/{p.id}/activate")
        assert resp.status_code == 403

    def test_status_does_not_require_auth(self, client):
        c, _ = client
        resp = c.get("/soul/status")
        assert resp.status_code == 200
