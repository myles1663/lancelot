"""Command Center chat-run and work-ledger HTTP routes."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from chat_runs import ChatRun
from gateway_request_models import ChatMessage, parse_request_model_or_error

router = APIRouter()


def bind_gateway_globals(**kwargs):
    globals().update(kwargs)

@router.get("/api/chat/history")
async def chat_history(request: Request, limit: int = 50):
    """Return recent conversation history for War Room persistence."""
    if not verify_token(request):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    history = main_orchestrator.context_env.history or []
    recent = history[-limit:] if limit < len(history) else history
    messages = [
        {
            "role": h.get("role", "user"),
            "content": h.get("content", ""),
            "timestamp": h.get("timestamp", 0),
        }
        for h in recent
    ]
    return {"messages": messages, "total": len(history)}


@router.post("/chat")
async def chat_webhook(request: Request):
    """
    Receives JSON payload from Google Chat.
    Routes to Onboarding if identity not bonded or keys missing.
    Intercepts Crusader Mode triggers before routing to orchestrator.
    """
    request_id = make_request_id()

    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)

    # S11: Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return error_response(429, "Rate limit exceeded. Try again later.", request_id=request_id)

    # S11: Request size check
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE:
        return error_response(413, "Request body too large.", request_id=request_id)

    try:
        from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused
        from src.core.auth_api import resolve_authenticated_identity

        if is_runtime_paused():
            pause_state = get_runtime_pause_status()
            return error_response(
                423,
                pause_state.get("reason") or "Runtime paused by operator",
                request_id=request_id,
            )

        identity = resolve_authenticated_identity(request)
        body = await parse_request_model_or_error(
            request,
            ChatMessage,
            request_id,
            error_response=error_response,
        )
        if isinstance(body, JSONResponse):
            return body
        message = body.text
        user = body.user
        # Preserve the caller's delivery channel so response limits stay channel-aware.
        req_channel = body.channel

        logger.info(f"[{request_id}] Message from {user}: {message[:50]}...")

        response_text = await _execute_chat_turn(
            message,
            user=user,
            channel=req_channel,
            identity=identity,
        )

        return {
            "response": response_text,
            "crusader_mode": crusader_mode.is_active,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error(f"[{request_id}] Chat error: {e}")
        return error_response(500, "Internal server error", request_id=request_id)


@router.post("/chat/async")
async def chat_async(request: Request):
    """Queue a Command Center chat turn for background execution.

    This endpoint is used by War Room so long-running governed work does not
    hold the browser's HTTP request open. The legacy `/chat` endpoint remains
    synchronous for API compatibility.
    """
    request_id = make_request_id()

    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)

    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return error_response(429, "Rate limit exceeded. Try again later.", request_id=request_id)

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE:
        return error_response(413, "Request body too large.", request_id=request_id)

    try:
        from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused
        from src.core.auth_api import resolve_authenticated_identity

        if is_runtime_paused():
            pause_state = get_runtime_pause_status()
            return error_response(
                423,
                pause_state.get("reason") or "Runtime paused by operator",
                request_id=request_id,
            )

        identity = resolve_authenticated_identity(request)
        body = await parse_request_model_or_error(
            request,
            ChatMessage,
            request_id,
            error_response=error_response,
        )
        if isinstance(body, JSONResponse):
            return body

        message = body.text
        user = body.user
        req_channel = body.channel
        logger.info("[%s] Async chat queued from %s: %s...", request_id, user, message[:50])

        run = chat_run_store.create(
            request_id=request_id,
            user=user,
            channel=req_channel,
            session_id=getattr(identity, "session_id", ""),
            operator_id=getattr(identity, "operator_id", ""),
            message=message,
        )
        _sync_work_ledger_from_chat_run(run, event_type="chat_run_queued")
        _emit_chat_run_event("chat.run_queued", run)

        task = asyncio.create_task(
            _execute_async_chat_run(
                run.run_id,
                message=message,
                user=user,
                channel=req_channel,
                identity=identity,
            )
        )
        _track_async_chat_task(task)

        return {
            "accepted": True,
            "response": "Queued for governed execution.",
            "status": run.status,
            "run_id": run.run_id,
            "run": _chat_run_payload(run),
            "crusader_mode": crusader_mode.is_active,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Async chat queue error: %s", request_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@router.get("/api/chat/runs")
async def list_chat_runs(request: Request, limit: int = 25):
    """Return recent async chat runs visible to the authenticated operator."""
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        safe_limit = max(1, min(int(limit), 100))
        runs = chat_run_store.list_recent(
            limit=safe_limit,
            session_id=getattr(identity, "session_id", ""),
            operator_id=getattr(identity, "operator_id", ""),
        )
        return {"runs": [_chat_run_payload(run) for run in runs], "count": len(runs)}
    except Exception as e:
        logger.error("Async chat run list failed: %s", e)
        return error_response(500, "Internal server error")


@router.get("/api/chat/runs/{run_id}")
async def get_chat_run(run_id: str, request: Request):
    """Return one async chat run by ID."""
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        run = chat_run_store.get(run_id)
        if run is None:
            return error_response(404, f"Chat run not found: {run_id}")

        if not _can_access_chat_run(run, identity):
            return error_response(404, f"Chat run not found: {run_id}")

        return {"run": _chat_run_payload(run)}
    except Exception as e:
        logger.error("Async chat run lookup failed for %s: %s", run_id, e)
        return error_response(500, "Internal server error")


@router.post("/api/chat/runs/{run_id}/cancel")
async def cancel_chat_run(run_id: str, request: Request):
    """Mark an async Command Center run cancelled.

    Cancellation is cooperative. A run that has entered a blocking provider or
    tool call may finish in the worker thread later, but the persisted run stays
    cancelled and late completion cannot overwrite the operator-visible state.
    """
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return error_response(429, "Rate limit exceeded. Try again later.", request_id=request_id)
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        run = chat_run_store.get(run_id)
        if run is None or not _can_access_chat_run(run, identity):
            return error_response(404, f"Chat run not found: {run_id}", request_id=request_id)
        if run.status in {"succeeded", "failed"}:
            return error_response(
                409,
                f"Chat run is already {run.status}; cancellation was not applied.",
                request_id=request_id,
            )

        body = await _optional_json_body(request)
        reason = str(body.get("reason") or "Cancelled by operator from Command Center.")
        cancelled = chat_run_store.request_cancel(run_id, reason=reason)
        if cancelled is None:
            return error_response(404, f"Chat run not found: {run_id}", request_id=request_id)
        _sync_work_ledger_from_chat_run(cancelled, event_type="chat_run_cancelled")
        _emit_chat_run_event("chat.run_cancelled", cancelled)
        return {
            "cancelled": cancelled.status == "cancelled",
            "status": cancelled.status,
            "run_id": cancelled.run_id,
            "run": _chat_run_payload(cancelled),
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Async chat run cancellation failed for %s: %s", request_id, run_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@router.post("/api/chat/runs/{run_id}/retry")
async def retry_chat_run(run_id: str, request: Request):
    """Replay a failed, cancelled, or blocked async Command Center run as a new run."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return error_response(429, "Rate limit exceeded. Try again later.", request_id=request_id)
    try:
        from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused
        from src.core.auth_api import resolve_authenticated_identity

        if is_runtime_paused():
            pause_state = get_runtime_pause_status()
            return error_response(
                423,
                pause_state.get("reason") or "Runtime paused by operator",
                request_id=request_id,
            )

        identity = resolve_authenticated_identity(request)
        original = chat_run_store.get(run_id)
        if original is None or not _can_access_chat_run(original, identity):
            return error_response(404, f"Chat run not found: {run_id}", request_id=request_id)
        if original.status not in {"failed", "cancelled", "blocked"}:
            return error_response(
                409,
                "Only failed, cancelled, or blocked chat runs can be retried; "
                f"current status is {original.status}.",
                request_id=request_id,
            )

        try:
            retry = chat_run_store.create_retry(
                run_id,
                request_id=request_id,
                session_id=getattr(identity, "session_id", ""),
                operator_id=getattr(identity, "operator_id", ""),
            )
        except ValueError as exc:
            return error_response(409, str(exc), request_id=request_id)
        if retry is None:
            return error_response(404, f"Chat run not found: {run_id}", request_id=request_id)

        _sync_work_ledger_from_chat_run(retry, event_type="chat_run_retry_queued")
        _emit_chat_run_event("chat.run_queued", retry)
        task = asyncio.create_task(
            _execute_async_chat_run(
                retry.run_id,
                message=retry.message_text,
                user=retry.user,
                channel=retry.channel,
                identity=identity,
            )
        )
        _track_async_chat_task(task)

        return {
            "accepted": True,
            "response": "Retry queued for governed execution.",
            "status": retry.status,
            "run_id": retry.run_id,
            "run": _chat_run_payload(retry),
            "crusader_mode": crusader_mode.is_active,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Async chat run retry failed for %s: %s", request_id, run_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@router.get("/api/work/active")
async def list_active_work(request: Request, limit: int = 25):
    """Return active work ledger items for the authenticated operator session."""
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        if getattr(identity, "auth_method", "") == "api_key":
            session_id = ""
            operator_id = ""
        else:
            session_id = getattr(identity, "session_id", "")
            operator_id = getattr(identity, "operator_id", "")
        safe_limit = max(1, min(int(limit), 100))
        work_ledger_store.checkpoint_quiet_work(
            max_quiet_seconds=ACTIVE_WORK_QUIET_CHECKPOINT_AFTER_SECONDS,
            reason="quiet_phase",
            session_id=session_id,
            operator_id=operator_id,
            limit=safe_limit,
        )
        items = work_ledger_store.list_work(
            session_id=session_id,
            operator_id=operator_id,
            include_terminal=False,
            limit=safe_limit,
        )
        return {
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }
    except Exception as e:
        logger.error("Active work list failed: %s", e)
        return error_response(500, "Internal server error")


@router.get("/api/work/{quest_id}")
async def get_work_item(quest_id: str, request: Request):
    """Return one active work item with recent events and checkpoints."""
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        item = work_ledger_store.get_work(quest_id)
        if item is None or not _can_access_work_item(item, identity):
            return error_response(404, f"Work item not found: {quest_id}")

        return {
            "item": item.to_dict(),
            "events": [event.to_dict() for event in work_ledger_store.list_events(quest_id, limit=50)],
            "checkpoints": work_ledger_store.list_checkpoints(quest_id, limit=10),
        }
    except Exception as e:
        logger.error("Work item lookup failed for %s: %s", quest_id, e)
        return error_response(500, "Internal server error")


@router.post("/api/work/{quest_id}/checkpoint")
async def checkpoint_work_item(quest_id: str, request: Request):
    """Create a durable checkpoint for an active work item."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        item = work_ledger_store.get_work(quest_id)
        if item is None or not _can_access_work_item(item, identity):
            return error_response(404, f"Work item not found: {quest_id}", request_id=request_id)

        body = await _optional_json_body(request)
        reason = str(body.get("reason") or "operator_checkpoint")
        checkpoint = work_ledger_store.create_checkpoint(quest_id, reason=reason)
        if checkpoint is None:
            return error_response(404, f"Work item not found: {quest_id}", request_id=request_id)
        return {
            "checkpoint": checkpoint,
            "quest_id": quest_id,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Work checkpoint failed for %s: %s", request_id, quest_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@router.post("/api/work/{quest_id}/resume")
async def resume_work_item(quest_id: str, request: Request):
    """Resume a blocked, failed, or cancelled work item by replaying its retained chat run."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return error_response(429, "Rate limit exceeded. Try again later.", request_id=request_id)
    try:
        from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused
        from src.core.auth_api import resolve_authenticated_identity

        if is_runtime_paused():
            pause_state = get_runtime_pause_status()
            return error_response(
                423,
                pause_state.get("reason") or "Runtime paused by operator",
                request_id=request_id,
            )

        identity = resolve_authenticated_identity(request)
        item = work_ledger_store.get_work(quest_id)
        if item is None or not _can_access_work_item(item, identity):
            return error_response(404, f"Work item not found: {quest_id}", request_id=request_id)

        source_run_id = item.last_chat_run_id or item.quest_id
        original = chat_run_store.get(source_run_id)
        if original is None or not _can_access_chat_run(original, identity):
            return error_response(404, f"Chat run not found: {source_run_id}", request_id=request_id)
        if original.status not in {"failed", "cancelled", "blocked"}:
            return error_response(
                409,
                "Only failed, cancelled, or blocked work can be resumed; "
                f"current chat run status is {original.status}.",
                request_id=request_id,
            )

        try:
            retry = chat_run_store.create_retry(
                source_run_id,
                request_id=request_id,
                session_id=getattr(identity, "session_id", ""),
                operator_id=getattr(identity, "operator_id", ""),
            )
        except ValueError as exc:
            return error_response(409, str(exc), request_id=request_id)
        if retry is None:
            return error_response(404, f"Chat run not found: {source_run_id}", request_id=request_id)

        work_ledger_store.append_event(
            quest_id=item.quest_id,
            event_type="work_resume_requested",
            summary=f"Resume queued as chat run {retry.run_id}.",
            phase=item.phase,
            status=item.status,
            metadata={"source_run_id": source_run_id, "retry_run_id": retry.run_id},
        )
        _sync_work_ledger_from_chat_run(retry, event_type="chat_run_resume_queued")
        _emit_chat_run_event("chat.run_queued", retry)

        task = asyncio.create_task(
            _execute_async_chat_run(
                retry.run_id,
                message=retry.message_text,
                user=retry.user,
                channel=retry.channel,
                identity=identity,
            )
        )
        _track_async_chat_task(task)

        return {
            "accepted": True,
            "response": "Resume queued for governed execution.",
            "status": retry.status,
            "run_id": retry.run_id,
            "run": _chat_run_payload(retry),
            "source_quest_id": quest_id,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Work resume failed for %s: %s", request_id, quest_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@router.post("/api/work/{quest_id}/archive")
async def archive_work_item(quest_id: str, request: Request):
    """Archive an operator-visible work item without deleting its ledger history."""
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request,
        "platform.admin",
        request_id=request_id,
    )
    if authz_error is not None:
        return authz_error
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        item = work_ledger_store.get_work(quest_id)
        if item is None:
            return error_response(404, f"Work item not found: {quest_id}", request_id=request_id)

        body = await _optional_json_body(request)
        reason = _preview_text(
            str(body.get("reason") or "Archived by operator from Command Center."),
            limit=500,
        )
        source_run_id = item.last_chat_run_id or item.quest_id
        source_run = chat_run_store.get(source_run_id)
        if (
            source_run is not None
            and source_run.status not in {"succeeded", "failed", "cancelled"}
        ):
            cancelled = chat_run_store.request_cancel(source_run_id, reason=reason)
            if cancelled is not None:
                _sync_work_ledger_from_chat_run(
                    cancelled,
                    event_type="chat_run_archived_cancelled",
                    metadata={"archive_reason": reason},
                )
                _emit_chat_run_event("chat.run_cancelled", cancelled)

        archived = work_ledger_store.archive_work(
            quest_id,
            reason=reason,
            archived_by_run_id=source_run_id,
            archived_by_operator_id=getattr(identity, "operator_id", ""),
            archived_by_session_id=getattr(identity, "session_id", ""),
        )
        if archived is None:
            return error_response(404, f"Work item not found: {quest_id}", request_id=request_id)
        archived_actioncards = _archive_pending_actioncards_for_work(
            quest_id,
            identity=identity,
            reason=reason,
        )

        return {
            "archived": True,
            "item": archived.to_dict(),
            "archived_actioncards": archived_actioncards,
            "events": [event.to_dict() for event in work_ledger_store.list_events(quest_id, limit=10)],
            "checkpoints": work_ledger_store.list_checkpoints(quest_id, limit=3),
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Work archive failed for %s: %s", request_id, quest_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@router.get("/api/operator/smoke")
async def operator_smoke_report(request: Request):
    """Run a read-only operator smoke report from the live gateway process."""
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request,
        "platform.admin",
        request_id=request_id,
    )
    if authz_error is not None:
        return authz_error
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        report = _try_handle_operational_report_command(
            "Please produce a read-only operational smoke report for this Lancelot instance.",
            quest_id=f"operator-smoke-{request_id}",
            identity=identity,
            channel="warroom",
        )
        if report is None:
            return error_response(
                500,
                "Operational smoke report command was not recognized.",
                request_id=request_id,
            )

        degraded = "Result: degraded" in report
        return {
            "ok": not degraded,
            "source": "live_gateway",
            "report": report,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Operator smoke report failed: %s", request_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


