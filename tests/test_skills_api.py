from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api, skills_api
from src.core.operator_identity import OperatorIdentity
from src.core.skills.registry import SkillError


def _insert_session(token: str) -> None:
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-18T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": ["warroom.login", "skills.admin"],
        "groups": [],
    }


def _client() -> TestClient:
    auth_api._sessions.clear()
    _insert_session("skills-admin")
    api_auth.init_api_auth(lambda request: True)
    app = FastAPI()
    app.include_router(skills_api.router)
    client = TestClient(app)
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "skills-admin")
    return client


def test_skill_proposal_detail_includes_pipeline_contract_fields():
    proposal = SimpleNamespace(
        id="proposal-1",
        name="demo_skill",
        description="demo",
        permissions=["read_input"],
        risk="low",
        source="first-party",
        author="Arthur",
        target_domains=["api.example.com"],
        credentials=[{"vault_key": "service.token", "type": "bearer", "purpose": "API"}],
        credential_keys=["service.token"],
        approved_capabilities=["tool.read_input"],
        manifest_yaml="name: demo_skill\n",
        security_manifest_yaml="id: demo_skill\n",
        execute_code="def execute(context, inputs):\n    return {'result': 'ok'}\n",
        test_code="def test_ok():\n    assert True\n",
        tests_status="generated",
        status="pending",
        pipeline_passed=True,
        pipeline_failed_at_stage=None,
        pipeline_stage_results={"manifest": {"passed": True}, "owner_review": {"status": "pending"}},
        artifact_hashes={"skill.yaml": "abc123"},
        created_at="2026-04-18T00:00:00Z",
        approved_by=None,
        approved_at=None,
        rejected_reason=None,
        rejected_at=None,
        installed_at=None,
    )

    class FakeFactory:
        def get_proposal(self, proposal_id):
            return proposal if proposal_id == "proposal-1" else None

        def list_proposals(self):
            return [proposal]

    skills_api._skill_factory = FakeFactory()
    skills_api._skill_registry = SimpleNamespace(list_skills=lambda: [])
    skills_api._skill_executor = object()

    client = _client()
    response = client.get("/api/skills/proposals/proposal-1")

    assert response.status_code == 200
    body = response.json()
    assert body["security_manifest_yaml"] == "id: demo_skill\n"
    assert body["approved_capabilities"] == ["tool.read_input"]
    assert body["pipeline_stage_results"]["owner_review"]["status"] == "pending"
    assert body["artifact_hashes"]["skill.yaml"] == "abc123"


def test_install_response_includes_validated_capabilities():
    proposal = SimpleNamespace(approved_capabilities=["tool.read_input", "tool.write_output"])

    class FakeFactory:
        def install_proposal(self, proposal_id, registry):
            return SimpleNamespace(name="demo_skill")

        def get_proposal(self, proposal_id):
            return proposal

    skills_api._skill_factory = FakeFactory()
    skills_api._skill_registry = object()
    skills_api._skill_executor = object()

    client = _client()
    response = client.post("/api/skills/proposals/proposal-1/install")

    assert response.status_code == 200
    assert response.json()["validated_capabilities"] == ["tool.read_input", "tool.write_output"]


def test_list_proposals_and_skills_success_paths():
    proposal = SimpleNamespace(
        id="proposal-1",
        name="demo_skill",
        description="demo",
        permissions=["read"],
        risk="low",
        source="first-party",
        target_domains=[],
        credential_keys=[],
        approved_capabilities=[],
        status=SimpleNamespace(value="pending"),
        pipeline_passed=True,
        pipeline_failed_at_stage=None,
        created_at="now",
        approved_by=None,
    )
    skill = SimpleNamespace(
        name="demo_skill",
        version="1.0.0",
        enabled=True,
        ownership=SimpleNamespace(value="system"),
    )

    skills_api.init_skills_api(
        factory=SimpleNamespace(list_proposals=lambda: [proposal]),
        registry=SimpleNamespace(list_skills=lambda: [skill]),
        executor=object(),
    )

    client = _client()

    proposals = client.get("/api/skills/proposals").json()
    skills = client.get("/api/skills").json()

    assert proposals["total"] == 1
    assert proposals["proposals"][0]["status"] == "pending"
    assert skills == {
        "skills": [
            {
                "name": "demo_skill",
                "version": "1.0.0",
                "enabled": True,
                "ownership": "system",
            }
        ],
        "total": 1,
    }


def test_installed_skill_detail_and_toggle_paths():
    skill = SimpleNamespace(
        name="demo_skill",
        version="1.0.0",
        enabled=True,
        ownership=SimpleNamespace(value="user"),
        signature_state=SimpleNamespace(value="verified"),
        installed_at="2026-04-18T00:00:00Z",
        manifest_path="/skills/demo/skill.yaml",
        manifest=SimpleNamespace(model_dump=lambda mode="json": {"name": "demo_skill", "version": "1.0.0"}),
    )

    class Registry:
        def __init__(self):
            self.skill = skill

        def list_skills(self):
            return [self.skill]

        def get_skill(self, name):
            return self.skill if name == "demo_skill" else None

        def enable_skill(self, name):
            if name != "demo_skill":
                raise SkillError("missing")
            self.skill.enabled = True

        def disable_skill(self, name):
            if name != "demo_skill":
                raise SkillError("missing")
            self.skill.enabled = False

    skills_api.init_skills_api(factory=SimpleNamespace(list_proposals=lambda: []), registry=Registry(), executor=object())
    client = _client()

    detail = client.get("/api/skills/demo_skill")
    disabled = client.post("/api/skills/demo_skill/disable")
    enabled = client.post("/api/skills/demo_skill/enable")

    assert detail.status_code == 200
    assert detail.json()["manifest"] == {"name": "demo_skill", "version": "1.0.0"}
    assert detail.json()["manifest_source"] == "registry"
    assert detail.json()["signature_state"] == "verified"
    assert disabled.json()["skill"]["enabled"] is False
    assert enabled.json()["skill"]["enabled"] is True


def test_installed_skill_detail_uses_builtin_manifest_when_registry_manifest_missing():
    skill = SimpleNamespace(
        name="health_check",
        version="1.0.0",
        enabled=True,
        ownership=SimpleNamespace(value="system"),
        signature_state=SimpleNamespace(value="verified"),
        installed_at="2026-04-18T00:00:00Z",
        manifest_path="",
        manifest=None,
    )

    class Registry:
        def get_skill(self, name):
            return skill if name == "health_check" else None

    skills_api.init_skills_api(factory=object(), registry=Registry(), executor=object())
    client = _client()

    detail = client.get("/api/skills/health_check")

    assert detail.status_code == 200
    body = detail.json()
    assert body["manifest_source"] == "builtin"
    assert body["manifest"]["name"] == "health_check"
    assert body["description"] == "Run a system health sweep and return the current health snapshot"
    assert body["permissions"] == []
    assert body["inputs"] == []


def test_approve_reject_and_install_error_paths(monkeypatch):
    class Factory:
        def approve_proposal(self, proposal_id, approved_by):
            assert approved_by == "Arthur"
            return SimpleNamespace(id=proposal_id, name="demo", approved_by=approved_by, approved_at="now")

        def reject_proposal(self, proposal_id, reason=None):
            assert reason == "unsafe"
            return SimpleNamespace(id=proposal_id, name="demo", rejected_reason=reason)

        def install_proposal(self, proposal_id, registry):
            raise RuntimeError("install failed")

    skills_api.init_skills_api(factory=Factory(), registry=object(), executor=object())
    client = _client()

    approved = client.post("/api/skills/proposals/proposal-1/approve", json={})
    rejected = client.post("/api/skills/proposals/proposal-1/reject", json={"reason": "unsafe"})
    install_failed = client.post("/api/skills/proposals/proposal-1/install")

    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "Arthur"
    assert rejected.status_code == 200
    assert rejected.json()["rejected_reason"] == "unsafe"
    assert install_failed.status_code == 400
    assert "install failed" in install_failed.json()["detail"]


def test_uninitialized_and_not_found_paths():
    client = _client()
    skills_api.init_skills_api(factory=None, registry=None, executor=None)

    assert client.get("/api/skills/proposals").status_code == 503
    assert client.get("/api/skills/proposals/missing").status_code == 503
    assert client.post("/api/skills/proposals/missing/approve").status_code == 503
    assert client.post("/api/skills/proposals/missing/reject").status_code == 503
    assert client.post("/api/skills/proposals/missing/install").status_code == 503
    assert client.get("/api/skills").status_code == 503

    skills_api.init_skills_api(
        factory=SimpleNamespace(
            get_proposal=lambda proposal_id: None,
            approve_proposal=lambda proposal_id, approved_by: (_ for _ in ()).throw(RuntimeError("bad approve")),
            reject_proposal=lambda proposal_id, reason=None: (_ for _ in ()).throw(RuntimeError("bad reject")),
        ),
        registry=SimpleNamespace(list_skills=lambda: []),
        executor=object(),
    )

    assert client.get("/api/skills/proposals/missing").status_code == 404
    assert client.post("/api/skills/proposals/missing/approve").status_code == 400
    assert client.post("/api/skills/proposals/missing/reject").status_code == 400
