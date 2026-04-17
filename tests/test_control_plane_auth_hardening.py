from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth
from src.core import auth_api
from src.core import control_plane
from src.core.operator_identity import OperatorIdentity


def _build_client(tmp_path, verify_request):
    api_auth.init_api_auth(verify_request)
    control_plane.init_control_plane(str(tmp_path))
    control_plane.set_runtime_control_hooks(
        emergency_stop_handler=lambda **kwargs: {
            "stopped_hive_agents": 0,
            "stopped_agent_ids": [],
            "execution_state": "emergency_stopped",
        }
    )
    app = FastAPI()
    app.include_router(control_plane.router)
    return TestClient(app)


def _insert_session(token, capabilities):
    identity = OperatorIdentity(
        operator_id="op-123",
        display_name="Arthur",
        session_id="session-1",
        session_started_at="2026-04-10T00:00:00Z",
        auth_method="local",
        ip_address="127.0.0.1",
    )
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": identity,
        "capabilities": sorted(capabilities),
        "groups": [],
    }


def test_control_plane_sensitive_routes_require_auth(tmp_path):
    client = _build_client(tmp_path, verify_request=lambda request: False)

    for path in (
        "/system/status",
        "/system/pause",
        "/router/decisions",
        "/usage/summary",
        "/warroom/artifacts",
        "/tokens",
    ):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["detail"] == "Unauthorized"


def test_control_plane_onboarding_routes_remain_available_without_auth(tmp_path):
    client = _build_client(tmp_path, verify_request=lambda request: False)

    response = client.get("/onboarding/status")

    assert response.status_code == 200
    assert "state" in response.json()


def test_control_plane_onboarding_mutation_routes_require_auth(tmp_path):
    client = _build_client(tmp_path, verify_request=lambda request: False)

    for path, payload in (
        ("/onboarding/reset", None),
        ("/onboarding/back", None),
        ("/onboarding/restart-step", None),
        ("/onboarding/resend-code", None),
        ("/onboarding/command", {"command": "RESET ONBOARDING"}),
    ):
        if payload is None:
            response = client.post(path)
        else:
            response = client.post(path, json=payload)
        assert response.status_code == 401, path


def test_control_plane_onboarding_mutation_routes_require_admin_capability(tmp_path):
    client = _build_client(tmp_path, verify_request=lambda request: True)
    token = "limited-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})

    response = client.post(
        "/onboarding/reset",
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: onboarding.admin"


def test_control_plane_platform_admin_routes_require_platform_admin(tmp_path):
    client = _build_client(tmp_path, verify_request=lambda request: True)
    token = "limited-platform-session"
    auth_api._sessions.clear()
    _insert_session(token, {"warroom.login"})

    response = client.get(
        "/tokens",
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"

    response = client.post(
        "/usage/reset",
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"

    response = client.post(
        "/system/pause",
        json={"reason": "maintenance"},
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"

    response = client.post(
        "/system/resume",
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"

    response = client.post(
        "/system/emergency-stop",
        json={"reason": "maintenance"},
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"

    response = client.post(
        "/warroom/artifacts",
        json={"type": "TOOL_TRACE", "content": {"message": "x"}},
        cookies={auth_api.get_warroom_session_cookie_name(): token},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing capability: platform.admin"
