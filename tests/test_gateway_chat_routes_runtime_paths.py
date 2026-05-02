import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

import gateway_chat_routes as routes
from chat_runs import ChatRunStore
from work_ledger import WorkLedgerStore


class FakeRequest:
    def __init__(self, payload=None, *, host="127.0.0.1", headers=None):
        self._payload = payload or {}
        self.client = SimpleNamespace(host=host)
        self.headers = headers or {}
        self.state = SimpleNamespace()

    async def json(self):
        return self._payload


class FakeLogger:
    def __init__(self):
        self.records = []

    def info(self, *args, **kwargs):
        self.records.append(("info", args, kwargs))

    def warning(self, *args, **kwargs):
        self.records.append(("warning", args, kwargs))

    def error(self, *args, **kwargs):
        self.records.append(("error", args, kwargs))


def _body(response):
    if isinstance(response, JSONResponse):
        return json.loads(response.body.decode("utf-8"))
    return response


def _error_response(status, message, request_id=None):
    return JSONResponse(
        status_code=status,
        content={"error": message, "request_id": request_id},
    )


@pytest.fixture
def route_runtime(tmp_path, monkeypatch):
    chat_store = ChatRunStore(str(tmp_path / "chat_runs.sqlite"))
    work_store = WorkLedgerStore(str(tmp_path / "work_ledger.sqlite"))
    identity = SimpleNamespace(
        operator_id="op-1",
        session_id="sess-1",
        display_name="Myles",
        auth_method="session",
    )
    events = []
    synced = []
    tracked_tasks = []
    archived_cards = []

    monkeypatch.setattr(
        "src.core.auth_api.resolve_authenticated_identity",
        lambda request: identity,
    )
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)
    monkeypatch.setattr(
        "src.core.runtime_pause.get_runtime_pause_status",
        lambda: {"reason": "operator pause"},
    )
    async def parse_request_model_or_error(request, model, request_id, error_response):
        return await request.json()

    monkeypatch.setattr(routes, "parse_request_model_or_error", parse_request_model_or_error)

    def chat_run_payload(run):
        payload = run.to_dict()
        payload["visible"] = True
        return payload

    async def execute_chat_turn(message, **kwargs):
        return f"reply:{message}"

    routes.bind_gateway_globals(
        make_request_id=lambda: "req-1",
        verify_token=lambda request: True,
        rate_limiter=SimpleNamespace(check=lambda ip: True),
        MAX_REQUEST_SIZE=4096,
        error_response=_error_response,
        logger=FakeLogger(),
        main_orchestrator=SimpleNamespace(
            context_env=SimpleNamespace(
                history=[
                    {"role": "user", "content": "old", "timestamp": 1},
                    {"role": "assistant", "content": "new", "timestamp": 2},
                ]
            )
        ),
        crusader_mode=SimpleNamespace(is_active=False),
        chat_run_store=chat_store,
        work_ledger_store=work_store,
        _execute_chat_turn=execute_chat_turn,
        _execute_async_chat_run=lambda run_id, **kwargs: asyncio.sleep(0),
        _track_async_chat_task=lambda task: tracked_tasks.append(task),
        _sync_work_ledger_from_chat_run=lambda run, event_type, metadata=None: (
            synced.append((run.run_id, event_type, metadata)),
            work_store.upsert_from_chat_run(run, event_type=event_type, metadata=metadata),
        )[-1],
        _emit_chat_run_event=lambda name, run: events.append((name, run.run_id)),
        _chat_run_payload=chat_run_payload,
        _can_access_chat_run=lambda run, ident: run.operator_id == ident.operator_id,
        _can_access_work_item=lambda item, ident: item.operator_id == ident.operator_id,
        _optional_json_body=lambda request: request.json(),
        _preview_text=lambda value, limit=500: str(value)[:limit],
        _archive_pending_actioncards_for_work=lambda quest_id, identity, reason: archived_cards.append(
            (quest_id, identity.operator_id, reason)
        )
        or 2,
        _try_handle_operational_report_command=lambda message, **kwargs: "Result: ok\nAll clear",
        _require_request_capability=lambda request, capability, request_id=None: None,
        ACTIVE_WORK_QUIET_CHECKPOINT_AFTER_SECONDS=30,
    )

    yield SimpleNamespace(
        chat_store=chat_store,
        work_store=work_store,
        identity=identity,
        events=events,
        synced=synced,
        tracked_tasks=tracked_tasks,
        archived_cards=archived_cards,
    )
    for task in tracked_tasks:
        if not task.done():
            task.cancel()
    chat_store.close()
    work_store.close()


@pytest.mark.asyncio
async def test_chat_history_and_webhook_enforce_auth_rate_size_pause_and_execute(route_runtime, monkeypatch):
    history = await routes.chat_history(FakeRequest(), limit=1)
    assert history == {"messages": [{"role": "assistant", "content": "new", "timestamp": 2}], "total": 2}

    ok = await routes.chat_webhook(
        FakeRequest(SimpleNamespace(text="hello", user="Myles", channel="warroom"))
    )
    assert ok["response"] == "reply:hello"
    assert ok["request_id"] == "req-1"

    routes.bind_gateway_globals(verify_token=lambda request: False)
    assert (await routes.chat_webhook(FakeRequest())).status_code == 401

    routes.bind_gateway_globals(
        verify_token=lambda request: True,
        rate_limiter=SimpleNamespace(check=lambda ip: False),
    )
    assert (await routes.chat_webhook(FakeRequest())).status_code == 429

    routes.bind_gateway_globals(rate_limiter=SimpleNamespace(check=lambda ip: True))
    assert (
        await routes.chat_webhook(FakeRequest(headers={"content-length": "5000"}))
    ).status_code == 413

    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: True)
    assert (
        await routes.chat_webhook(
            FakeRequest(SimpleNamespace(text="pause", user="Myles", channel="warroom"))
        )
    ).status_code == 423


@pytest.mark.asyncio
async def test_async_chat_run_queue_list_lookup_cancel_and_retry(route_runtime, monkeypatch):
    queued = await routes.chat_async(
        FakeRequest(SimpleNamespace(text="compile memory", user="Myles", channel="warroom"))
    )
    assert queued["accepted"] is True
    assert queued["status"] == "queued"
    assert route_runtime.tracked_tasks
    assert route_runtime.synced[-1][1] == "chat_run_queued"
    run_id = queued["run_id"]

    listed = await routes.list_chat_runs(FakeRequest(), limit=200)
    assert listed["count"] == 1
    assert listed["runs"][0]["run_id"] == run_id

    fetched = await routes.get_chat_run(run_id, FakeRequest())
    assert fetched["run"]["message_preview"] == "compile memory"

    cancelled = await routes.cancel_chat_run(
        run_id,
        FakeRequest({"reason": "operator cancelled duplicate"}),
    )
    assert cancelled["cancelled"] is True
    assert cancelled["status"] == "cancelled"
    assert route_runtime.events[-1] == ("chat.run_cancelled", run_id)

    retry = await routes.retry_chat_run(run_id, FakeRequest())
    assert retry["accepted"] is True
    assert retry["run"]["retry_of_run_id"] == run_id
    assert retry["run"]["message_preview"] == "compile memory"

    assert (await routes.cancel_chat_run("missing", FakeRequest())).status_code == 404
    assert (await routes.get_chat_run("missing", FakeRequest())).status_code == 404

    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: True)
    assert (await routes.retry_chat_run(run_id, FakeRequest())).status_code == 423


@pytest.mark.asyncio
async def test_chat_run_retry_rejects_non_retryable_and_inaccessible_runs(route_runtime, monkeypatch):
    active = route_runtime.chat_store.create(
        request_id="req-active",
        user="Myles",
        channel="warroom",
        session_id="sess-1",
        operator_id="op-1",
        message="still running",
    )
    assert (await routes.retry_chat_run(active.run_id, FakeRequest())).status_code == 409

    foreign = route_runtime.chat_store.create(
        request_id="req-foreign",
        user="Other",
        channel="warroom",
        session_id="other",
        operator_id="op-2",
        message="private run",
    )
    route_runtime.chat_store.fail(foreign.run_id, "failed elsewhere")
    assert (await routes.get_chat_run(foreign.run_id, FakeRequest())).status_code == 404
    assert (await routes.retry_chat_run(foreign.run_id, FakeRequest())).status_code == 404

    failed = route_runtime.chat_store.create(
        request_id="req-failed",
        user="Myles",
        channel="warroom",
        session_id="sess-1",
        operator_id="op-1",
        message="retry value error",
    )
    route_runtime.chat_store.fail(failed.run_id, "provider failed")
    monkeypatch.setattr(
        route_runtime.chat_store,
        "create_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("retry already exists")),
    )
    assert (await routes.retry_chat_run(failed.run_id, FakeRequest())).status_code == 409


@pytest.mark.asyncio
async def test_work_routes_filter_checkpoint_resume_and_archive(route_runtime):
    failed = route_runtime.chat_store.create(
        request_id="req-failed",
        user="Myles",
        channel="warroom",
        session_id="sess-1",
        operator_id="op-1",
        message="finish migration",
    )
    route_runtime.chat_store.fail(failed.run_id, "provider failed")
    item = route_runtime.work_store.upsert_work(
        quest_id="quest-1",
        objective="finish migration",
        session_id="sess-1",
        operator_id="op-1",
        status="failed",
        phase="execution",
        blocker="provider failed",
        last_chat_run_id=failed.run_id,
    )
    route_runtime.work_store.append_event(
        quest_id=item.quest_id,
        event_type="failure",
        summary="provider failed",
        status="failed",
    )

    active = await routes.list_active_work(FakeRequest(), limit=250)
    assert active["count"] == 0

    details = await routes.get_work_item("quest-1", FakeRequest())
    assert details["item"]["objective"] == "finish migration"
    assert details["events"][0]["summary"] == "provider failed"

    checkpoint = await routes.checkpoint_work_item(
        "quest-1",
        FakeRequest({"reason": "before retry"}),
    )
    assert checkpoint["quest_id"] == "quest-1"
    assert checkpoint["checkpoint"]["reason"] == "before retry"

    resumed = await routes.resume_work_item("quest-1", FakeRequest())
    assert resumed["accepted"] is True
    assert resumed["run"]["retry_of_run_id"] == failed.run_id
    assert route_runtime.events[-1][0] == "chat.run_queued"

    active_source = route_runtime.chat_store.create(
        request_id="req-active",
        user="Myles",
        channel="warroom",
        session_id="sess-1",
        operator_id="op-1",
        message="archive active work",
    )
    route_runtime.work_store.upsert_work(
        quest_id="quest-active",
        objective="archive active work",
        session_id="sess-1",
        operator_id="op-1",
        status="active",
        phase="execution",
        last_chat_run_id=active_source.run_id,
    )
    archived = await routes.archive_work_item(
        "quest-active",
        FakeRequest({"reason": "operator cleanup"}),
    )
    assert archived["archived"] is True
    assert archived["archived_actioncards"] == 2
    assert route_runtime.chat_store.get(active_source.run_id).status == "cancelled"
    assert route_runtime.archived_cards == [("quest-active", "op-1", "operator cleanup")]

    assert (await routes.get_work_item("missing", FakeRequest())).status_code == 404
    assert (await routes.resume_work_item("missing", FakeRequest())).status_code == 404
    assert (await routes.archive_work_item("missing", FakeRequest())).status_code == 404


@pytest.mark.asyncio
async def test_work_resume_and_archive_reject_invalid_states_and_capabilities(route_runtime):
    succeeded = route_runtime.chat_store.create(
        request_id="req-ok",
        user="Myles",
        channel="warroom",
        session_id="sess-1",
        operator_id="op-1",
        message="already done",
    )
    route_runtime.chat_store.complete(succeeded.run_id, status="succeeded", response="done")
    route_runtime.work_store.upsert_work(
        quest_id="quest-ok",
        objective="already done",
        session_id="sess-1",
        operator_id="op-1",
        status="completed",
        last_chat_run_id=succeeded.run_id,
    )
    assert (await routes.resume_work_item("quest-ok", FakeRequest())).status_code == 409

    routes.bind_gateway_globals(
        _require_request_capability=lambda request, capability, request_id=None: JSONResponse(
            status_code=403, content={"error": "denied", "request_id": request_id}
        )
    )
    assert (await routes.archive_work_item("quest-ok", FakeRequest())).status_code == 403
    assert (await routes.operator_smoke_report(FakeRequest())).status_code == 403


@pytest.mark.asyncio
async def test_operator_smoke_report_success_degraded_unrecognized_and_failure(route_runtime):
    ok = await routes.operator_smoke_report(FakeRequest())
    assert ok["ok"] is True
    assert ok["source"] == "live_gateway"

    routes.bind_gateway_globals(
        _try_handle_operational_report_command=lambda *args, **kwargs: "Result: degraded\nQueue backed up"
    )
    degraded = await routes.operator_smoke_report(FakeRequest())
    assert degraded["ok"] is False

    routes.bind_gateway_globals(_try_handle_operational_report_command=lambda *args, **kwargs: None)
    assert (await routes.operator_smoke_report(FakeRequest())).status_code == 500

    routes.bind_gateway_globals(
        _try_handle_operational_report_command=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("snapshot failed")
        )
    )
    assert (await routes.operator_smoke_report(FakeRequest())).status_code == 500


@pytest.mark.asyncio
async def test_chat_and_work_routes_fail_closed_for_auth_rate_pause_payload_and_store_errors(route_runtime, monkeypatch):
    routes.bind_gateway_globals(verify_token=lambda request: False)
    assert (await routes.chat_history(FakeRequest())).status_code == 401
    assert (await routes.chat_async(FakeRequest())).status_code == 401
    assert (await routes.list_chat_runs(FakeRequest())).status_code == 401
    assert (await routes.get_chat_run("run-1", FakeRequest())).status_code == 401
    assert (await routes.cancel_chat_run("run-1", FakeRequest())).status_code == 401
    assert (await routes.retry_chat_run("run-1", FakeRequest())).status_code == 401
    assert (await routes.list_active_work(FakeRequest())).status_code == 401
    assert (await routes.get_work_item("quest-1", FakeRequest())).status_code == 401
    assert (await routes.checkpoint_work_item("quest-1", FakeRequest())).status_code == 401
    assert (await routes.resume_work_item("quest-1", FakeRequest())).status_code == 401

    routes.bind_gateway_globals(
        verify_token=lambda request: True,
        rate_limiter=SimpleNamespace(check=lambda ip: False),
    )
    assert (await routes.chat_async(FakeRequest())).status_code == 429
    assert (await routes.cancel_chat_run("run-1", FakeRequest())).status_code == 429
    assert (await routes.retry_chat_run("run-1", FakeRequest())).status_code == 429
    assert (await routes.resume_work_item("quest-1", FakeRequest())).status_code == 429

    routes.bind_gateway_globals(rate_limiter=SimpleNamespace(check=lambda ip: True))
    assert (
        await routes.chat_async(FakeRequest(headers={"content-length": "5000"}))
    ).status_code == 413

    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: True)
    assert (await routes.chat_async(FakeRequest())).status_code == 423
    assert (await routes.resume_work_item("quest-1", FakeRequest())).status_code == 423
    monkeypatch.setattr("src.core.runtime_pause.is_runtime_paused", lambda: False)

    async def bad_payload(request, model, request_id, error_response):
        return error_response(400, "bad payload", request_id=request_id)

    routes.bind_gateway_globals(parse_request_model_or_error=bad_payload)
    assert (await routes.chat_webhook(FakeRequest())).status_code == 400
    assert (await routes.chat_async(FakeRequest())).status_code == 400

    async def raising_turn(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    routes.bind_gateway_globals(
        parse_request_model_or_error=lambda request, model, request_id, error_response: request.json(),
        _execute_chat_turn=raising_turn,
    )
    assert (
        await routes.chat_webhook(FakeRequest(SimpleNamespace(text="hello", user="Myles", channel="warroom")))
    ).status_code == 500

    class BrokenChatStore:
        def create(self, *args, **kwargs):
            raise RuntimeError("sqlite locked")

    routes.bind_gateway_globals(chat_run_store=BrokenChatStore())
    assert (
        await routes.chat_async(FakeRequest(SimpleNamespace(text="hello", user="Myles", channel="warroom")))
    ).status_code == 500


@pytest.mark.asyncio
async def test_chat_run_and_work_route_exception_paths_return_500(route_runtime, monkeypatch):
    broken_identity = RuntimeError("identity unavailable")
    monkeypatch.setattr(
        "src.core.auth_api.resolve_authenticated_identity",
        lambda request: (_ for _ in ()).throw(broken_identity),
    )

    assert (await routes.list_chat_runs(FakeRequest())).status_code == 500
    assert (await routes.get_chat_run("run-1", FakeRequest())).status_code == 500
    assert (await routes.cancel_chat_run("run-1", FakeRequest())).status_code == 500
    assert (await routes.retry_chat_run("run-1", FakeRequest())).status_code == 500
    assert (await routes.list_active_work(FakeRequest())).status_code == 500
    assert (await routes.get_work_item("quest-1", FakeRequest())).status_code == 500
    assert (await routes.checkpoint_work_item("quest-1", FakeRequest())).status_code == 500
    assert (await routes.resume_work_item("quest-1", FakeRequest())).status_code == 500
    assert (await routes.archive_work_item("quest-1", FakeRequest())).status_code == 500


@pytest.mark.asyncio
async def test_work_resume_handles_missing_source_run_retry_conflict_and_retry_missing(route_runtime, monkeypatch):
    route_runtime.work_store.upsert_work(
        quest_id="quest-missing-run",
        objective="resume missing source",
        session_id="sess-1",
        operator_id="op-1",
        status="failed",
        last_chat_run_id="missing-run",
    )
    assert (await routes.resume_work_item("quest-missing-run", FakeRequest())).status_code == 404

    failed = route_runtime.chat_store.create(
        request_id="req-failed",
        user="Myles",
        channel="warroom",
        session_id="sess-1",
        operator_id="op-1",
        message="retry conflict",
    )
    route_runtime.chat_store.fail(failed.run_id, "provider failed")
    route_runtime.work_store.upsert_work(
        quest_id="quest-conflict",
        objective="retry conflict",
        session_id="sess-1",
        operator_id="op-1",
        status="failed",
        last_chat_run_id=failed.run_id,
    )

    monkeypatch.setattr(
        route_runtime.chat_store,
        "create_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("retry already queued")),
    )
    assert (await routes.resume_work_item("quest-conflict", FakeRequest())).status_code == 409

    monkeypatch.setattr(route_runtime.chat_store, "create_retry", lambda *args, **kwargs: None)
    assert (await routes.resume_work_item("quest-conflict", FakeRequest())).status_code == 404
