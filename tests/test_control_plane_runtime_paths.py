import json
from types import SimpleNamespace

import pytest

from src.core import control_plane as cp


def _body(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.fixture(autouse=True)
def reset_control_plane_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "_model_router", None, raising=False)
    monkeypatch.setattr(cp, "_usage_tracker", None, raising=False)
    monkeypatch.setattr(cp, "_usage_persistence", None, raising=False)
    monkeypatch.setattr(cp, "_token_store", None, raising=False)
    monkeypatch.setattr(cp, "_runtime_emergency_stop_handler", None, raising=False)
    cp.init_runtime_pause(str(tmp_path))
    cp._war_room_artifacts.clear()
    yield
    cp.resume_runtime(source="test-teardown")
    monkeypatch.setattr(cp, "_runtime_emergency_stop_handler", None, raising=False)


@pytest.mark.asyncio
async def test_router_usage_and_monthly_runtime_paths():
    decisions = [SimpleNamespace(to_dict=lambda i=i: {"decision": i}) for i in range(60)]
    router = SimpleNamespace(recent_decisions=decisions, stats={"fast": 7})
    tracker = SimpleNamespace(
        summary=lambda: {"tokens": 100},
        lane_breakdown=lambda: {"fast": 80},
        model_breakdown=lambda: {"gpt": 100},
        estimated_savings=lambda: {"usd": 3.25},
        reset=lambda: setattr(tracker, "reset_called", True),
    )
    persistence = SimpleNamespace(
        get_month=lambda month: {"month": month},
        get_current_month=lambda: {"month": "current"},
        get_available_months=lambda: ["2026-05", "2026-04"],
    )

    assert await cp.router_decisions() == {"decisions": [], "message": "Model router not initialised"}
    assert await cp.router_stats() == {"stats": {}, "message": "Model router not initialised"}
    assert await cp.usage_summary() == {"usage": {}, "message": "Usage tracker not initialised"}
    assert await cp.usage_lanes() == {"lanes": {}, "message": "Usage tracker not initialised"}
    assert await cp.usage_models() == {"models": {}, "message": "Usage tracker not initialised"}
    assert await cp.usage_savings() == {"savings": {}, "message": "Usage tracker not initialised"}
    assert await cp.usage_monthly() == {"monthly": {}, "message": "Usage persistence not initialised"}
    assert _body(await cp.usage_reset())["error"] == "Usage tracker not initialised"

    cp.set_model_router(router)
    cp.set_usage_tracker(tracker)
    cp.set_usage_persistence(persistence)

    assert len((await cp.router_decisions())["decisions"]) == 50
    assert await cp.router_stats() == {"stats": {"fast": 7}}
    assert await cp.usage_summary() == {"usage": {"tokens": 100}}
    assert await cp.usage_lanes() == {"lanes": {"fast": 80}}
    assert await cp.usage_models() == {"models": {"gpt": 100}}
    assert await cp.usage_savings() == {"savings": {"usd": 3.25}}
    assert (await cp.usage_monthly())["monthly"] == {"month": "current"}
    assert (await cp.usage_monthly("2026-05"))["monthly"] == {"month": "2026-05"}
    assert (await cp.usage_reset())["message"] == "Usage counters reset"
    assert tracker.reset_called is True


@pytest.mark.asyncio
async def test_warroom_artifact_runtime_paths(monkeypatch):
    identity = SimpleNamespace(operator_id="", display_name="Myles Hamilton", session_id="session-1")
    monkeypatch.setattr(cp, "resolve_authenticated_identity", lambda request: identity)
    monkeypatch.setattr(cp, "resolve_operator_id", lambda display_name: "myles-hamilton")

    assert _body(await cp.warroom_store_artifact(SimpleNamespace(), None))["error"] == "Missing artifact data"
    invalid = cp.WarRoomArtifactRequest(type="not-real", content={})
    assert _body(await cp.warroom_store_artifact(SimpleNamespace(), invalid))["status"] == 400

    body = cp.WarRoomArtifactRequest(
        type="TOOL_TRACE",
        content={"message": "reviewed"},
        id="client-id",
        session_id="client-session",
        operator_id="client-operator",
        source="client",
    )
    stored = await cp.warroom_store_artifact(SimpleNamespace(), body)
    listed = await cp.warroom_list_artifacts(session_id="session-1")
    artifact = listed["artifacts"][0]

    assert stored == {"status": "stored", "artifact_count": 1}
    assert artifact["source"] == "api"
    assert artifact["session_id"] == "session-1"
    assert artifact["operator_id"] == "myles-hamilton"
    assert (await cp.warroom_get_artifact(artifact["id"]))["artifact"]["content"] == {"message": "reviewed"}
    assert _body(await cp.warroom_get_artifact("missing"))["status"] == 404


@pytest.mark.asyncio
async def test_token_runtime_paths():
    token = SimpleNamespace(to_dict=lambda: {"id": "tok-1", "status": "active"})
    store = SimpleNamespace(
        list_tokens=lambda limit, status=None: [token],
        get=lambda token_id: token if token_id == "tok-1" else None,
        revoke=lambda token_id, reason: token_id == "tok-1",
    )

    assert (await cp.tokens_list())["message"] == "Token store not initialised"
    assert _body(await cp.tokens_get("tok-1"))["error"] == "Token store not initialised"
    assert _body(await cp.tokens_revoke("tok-1", SimpleNamespace()))["error"] == "Token store not initialised"

    cp._token_store = store

    assert await cp.tokens_list(status="active", limit=5) == {
        "tokens": [{"id": "tok-1", "status": "active"}],
        "total": 1,
    }
    assert await cp.tokens_get("tok-1") == {"token": {"id": "tok-1", "status": "active"}}
    assert _body(await cp.tokens_get("missing"))["status"] == 404
    assert await cp.tokens_revoke(
        "tok-1",
        SimpleNamespace(),
        cp.TokenRevokeRequest(reason="operator requested"),
    ) == {"status": "revoked", "token_id": "tok-1", "reason": "operator requested"}
    assert _body(await cp.tokens_revoke("missing", SimpleNamespace()))["status"] == 400


def test_artifact_normalization_and_snapshot_guard_paths(monkeypatch):
    with pytest.raises(ValueError, match="dict"):
        cp._normalize_war_room_artifact("bad")
    with pytest.raises(ValueError, match="Unsupported artifact type"):
        cp._normalize_war_room_artifact({"type": "bad", "content": {}})
    with pytest.raises(ValueError, match="content"):
        cp._normalize_war_room_artifact({"type": "TOOL_TRACE", "content": "bad"})

    artifact = cp.WarRoomArtifact(type="DIAGNOSTIC", content={"ok": True}, session_id="client")
    normalized = cp._normalize_war_room_artifact(
        artifact,
        operator_id="op",
        session_id="trusted-session",
        source="api",
    )
    assert normalized["session_id"] == "trusted-session"
    assert normalized["operator_id"] == "op"
    assert normalized["source"] == "api"

    monkeypatch.setattr(cp, "_snapshot", None, raising=False)
    with pytest.raises(RuntimeError, match="not initialised"):
        cp.get_snapshot()


@pytest.mark.asyncio
async def test_control_plane_system_pause_policy_and_artifact_error_paths(monkeypatch):
    monkeypatch.setattr(cp, "get_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("snapshot failed")))
    assert _body(await cp.system_status())["error"] == "Failed to retrieve system status"

    monkeypatch.setattr(cp, "get_runtime_pause_status", lambda: (_ for _ in ()).throw(RuntimeError("pause failed")))
    assert _body(await cp.runtime_pause_status())["error"] == "Failed to retrieve runtime pause state"

    identity = SimpleNamespace(operator_id="op-1", display_name="Arthur", session_id="s1")
    monkeypatch.setattr(cp, "resolve_authenticated_identity", lambda request: identity)
    assert _body(await cp.runtime_pause(SimpleNamespace(), cp.RuntimePauseRequest(reason="")))["status"] == 400
    assert _body(await cp.runtime_emergency_stop(SimpleNamespace(), cp.RuntimePauseRequest(reason="stop")))["status"] == 503

    stop_calls = []
    cp.set_runtime_control_hooks(
        emergency_stop_handler=lambda **kwargs: stop_calls.append(kwargs) or {"stopped": ["job-1"]}
    )
    stopped = await cp.runtime_emergency_stop(SimpleNamespace(), cp.RuntimePauseRequest(reason="operator stop"))
    assert stopped["stopped"] == ["job-1"]
    assert stop_calls[0]["operator_id"] == "op-1"

    assert _body(await cp.update_system_model_policy(SimpleNamespace(), cp.UpdateModelUsagePolicyRequest()))["status"] == 400
    monkeypatch.setattr(cp, "update_model_usage_policy", lambda **kwargs: (_ for _ in ()).throw(ValueError("bad mode")))
    assert _body(await cp.update_system_model_policy(
        SimpleNamespace(),
        cp.UpdateModelUsagePolicyRequest(local_execution_mode="bad"),
    ))["error"] == "bad mode"

    monkeypatch.setattr(cp._war_room_artifacts, "list", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("list failed")))
    assert _body(await cp.warroom_list_artifacts())["error"] == "Failed to list artifacts"
    monkeypatch.setattr(cp._war_room_artifacts, "get", lambda artifact_id: (_ for _ in ()).throw(RuntimeError("get failed")))
    assert _body(await cp.warroom_get_artifact("a1"))["error"] == "Failed to retrieve artifact"


@pytest.mark.asyncio
async def test_control_plane_safe_errors_for_runtime_dependencies():
    class FailingRouter:
        @property
        def recent_decisions(self):
            raise RuntimeError("router failed")

        @property
        def stats(self):
            raise RuntimeError("stats failed")

    failing_router = FailingRouter()
    failing_tracker = SimpleNamespace(
        summary=lambda: (_ for _ in ()).throw(RuntimeError("summary failed")),
        lane_breakdown=lambda: (_ for _ in ()).throw(RuntimeError("lanes failed")),
        model_breakdown=lambda: (_ for _ in ()).throw(RuntimeError("models failed")),
        estimated_savings=lambda: (_ for _ in ()).throw(RuntimeError("savings failed")),
        reset=lambda: (_ for _ in ()).throw(RuntimeError("reset failed")),
    )
    failing_persistence = SimpleNamespace(
        get_current_month=lambda: (_ for _ in ()).throw(RuntimeError("monthly failed")),
        get_available_months=lambda: [],
    )
    failing_store = SimpleNamespace(
        list_tokens=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("list failed")),
        get=lambda token_id: (_ for _ in ()).throw(RuntimeError("get failed")),
        revoke=lambda token_id, reason: (_ for _ in ()).throw(RuntimeError("revoke failed")),
    )

    cp.set_model_router(failing_router)
    cp.set_usage_tracker(failing_tracker)
    cp.set_usage_persistence(failing_persistence)
    cp._token_store = failing_store

    assert _body(await cp.router_decisions())["error"] == "Failed to retrieve router decisions"
    assert _body(await cp.usage_summary())["error"] == "Failed to retrieve usage summary"
    assert _body(await cp.usage_lanes())["error"] == "Failed to retrieve lane usage"
    assert _body(await cp.usage_models())["error"] == "Failed to retrieve model usage"
    assert _body(await cp.usage_savings())["error"] == "Failed to retrieve savings data"
    assert _body(await cp.usage_monthly())["error"] == "Failed to retrieve monthly usage"
    assert _body(await cp.usage_reset())["error"] == "Failed to reset usage counters"
    assert _body(await cp.tokens_list())["error"] == "Failed to list tokens"
    assert _body(await cp.tokens_get("tok-1"))["error"] == "Failed to retrieve token"
    assert _body(await cp.tokens_revoke("tok-1", SimpleNamespace()))["error"] == "Failed to revoke token"
