from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth, auth_api, skills_api
from src.core.operator_identity import OperatorIdentity


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
