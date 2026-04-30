"""Operator-facing gateway routes outside the chat-run ledger."""

from __future__ import annotations

import asyncio
import hmac
import json
import os

from fastapi import APIRouter, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from gateway_request_models import MfaSubmitRequest, parse_request_model_or_error

router = APIRouter()


def bind_gateway_globals(**kwargs):
    globals().update(kwargs)


@router.post("/chat/upload")
async def chat_with_files(
    request: Request,
    text: str = Form(""),
    user: str = Form("Commander"),
    files: list[UploadFile] = File(default=[]),
    save_to_workspace: bool = Form(default=False),
):
    """Accept chat text plus uploaded files and run the normal chat path."""
    request_id = make_request_id()

    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)

    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return error_response(429, "Rate limit exceeded.", request_id=request_id)

    try:
        from orchestrator import ChatAttachment
        from src.core.auth_api import resolve_authenticated_identity
        from src.core.runtime_pause import get_runtime_pause_status, is_runtime_paused

        if is_runtime_paused():
            pause_state = get_runtime_pause_status()
            return error_response(
                423,
                pause_state.get("reason") or "Runtime paused by operator",
                request_id=request_id,
            )

        identity = resolve_authenticated_identity(request)
        resolved_user = _resolve_audit_user(request)

        attachments = []
        for upload in files:
            file_bytes = await upload.read()
            mime = upload.content_type or "application/octet-stream"
            attachments.append(ChatAttachment(
                filename=upload.filename or "unknown",
                mime_type=mime,
                data=file_bytes,
            ))

            if save_to_workspace:
                workspace_path = "/home/lancelot/workspace"
                os.makedirs(workspace_path, exist_ok=True)
                safe_name = os.path.basename(upload.filename or "upload")
                save_path = os.path.join(workspace_path, safe_name)
                with open(save_path, "wb") as workspace_file:
                    workspace_file.write(file_bytes)
                logger.info("[%s] Saved upload to workspace: %s", request_id, save_path)

        logger.info(
            "[%s] Upload from %s: text=%s... files=%d",
            request_id,
            resolved_user,
            text[:50],
            len(attachments),
        )

        response_text = await _execute_chat_turn(
            text,
            user=resolved_user,
            channel="warroom",
            identity=identity,
            attachments=attachments,
        )

        return {
            "response": response_text,
            "crusader_mode": crusader_mode.is_active,
            "request_id": request_id,
            "files_received": len(attachments),
        }
    except Exception as exc:
        logger.exception(
            "[%s] Upload chat failed while processing text_len=%d file_count=%d: %s",
            request_id,
            len(text or ""),
            len(files or []),
            exc,
        )
        return error_response(
            500,
            "Upload chat failed while processing attachments. Check gateway logs with the request id.",
            request_id=request_id,
        )


@router.post("/mfa_submit")
async def mfa_submit(request: Request):
    """Receive an operator MFA code and release the matching bridge challenge."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        from src.core.auth_api import resolve_authenticated_identity, request_has_capability

        identity = resolve_authenticated_identity(request)
        body = await parse_request_model_or_error(
            request,
            MfaSubmitRequest,
            request_id,
            error_response=error_response,
        )
        if isinstance(body, JSONResponse):
            return body
        code = body.code
        task_id = body.task_id
        if not code:
            return error_response(400, "Missing 'code' field", request_id=request_id)

        logger.info("[%s] MFA Code Received for Task %s", request_id, task_id)

        success, reason = mfa_guard.submit_code(
            task_id,
            code,
            operator_id=identity.operator_id,
            session_id=identity.session_id,
            actor=identity.display_name or identity.operator_id,
            is_admin=(
                request_has_capability(request, "governance.admin")
                or request_has_capability(request, "platform.admin")
            ),
        )

        if success:
            return {"status": "Code Accepted. Bridge Released.", "request_id": request_id}
        if reason == "forbidden":
            return error_response(
                403,
                "MFA challenge is bound to a different operator/session.",
                request_id=request_id,
            )
        return error_response(404, "Unknown Task ID or no pending challenge.", request_id=request_id)
    except Exception as exc:
        logger.exception("[%s] MFA submission failed for task_id=%s: %s", request_id, locals().get("task_id", ""), exc)
        return error_response(
            500,
            "MFA submission failed before the bridge challenge could be released.",
            request_id=request_id,
        )


@router.post("/api/secrets/reload")
async def reload_secrets(request: Request):
    """Reload cached secrets from the vault without restarting the container."""
    authz_error = _require_request_capability(request, "platform.admin")
    if authz_error is not None:
        return authz_error
    try:
        if not _boot_vault or not secret_cache.is_bootstrapped():
            return JSONResponse(
                status_code=503,
                content={"error": "Vault not initialized; secret rotation unavailable"},
            )
        changed = secret_cache.reload(_boot_vault)
        changed_count = sum(1 for value in changed.values() if value)

        if changed.get("LANCELOT_API_TOKEN"):
            new_token = secret_cache.get("LANCELOT_API_TOKEN")
            set_api_token(new_token)
            globals()["API_TOKEN"] = new_token

        try:
            from shared.receipts import ReceiptService

            receipt_service = ReceiptService(data_dir="/home/lancelot/data")
            receipt_service.create_receipt(
                task_id="secret_rotation",
                action="secret_rotation",
                category="SYSTEM",
                result={"changed_count": changed_count},
            )
        except Exception as exc:
            logger.warning("Secret rotation receipt emission failed: %s", exc)

        logger.info("Secrets reloaded: %d changed", changed_count)
        return {"status": "ok", "changed_count": changed_count}
    except Exception as exc:
        logger.exception("Secret reload failed while refreshing cache from vault: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Secret reload failed while refreshing cache from vault"},
        )


@router.get("/crusader_status")
def crusader_status(request: Request):
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    return crusader_mode.get_status()


@router.post("/api/crusader/activate")
def api_crusader_activate(request: Request):
    authz_error = _require_request_capability(request, "platform.admin")
    if authz_error is not None:
        return authz_error
    if crusader_mode.is_active:
        return {"status": "already_active", **crusader_mode.get_status()}
    ok, response_text = _transition_crusader_mode("activate")
    if not ok:
        return error_response(500, response_text)
    main_orchestrator.audit_logger.log_event(
        "CRUSADER_MODE_ACTIVATED",
        "User activated Crusader Mode via API",
        _resolve_audit_user(request),
    )
    return {"status": "activated", "message": response_text, **crusader_mode.get_status()}


@router.post("/api/crusader/deactivate")
def api_crusader_deactivate(request: Request):
    authz_error = _require_request_capability(request, "platform.admin")
    if authz_error is not None:
        return authz_error
    if not crusader_mode.is_active:
        return {"status": "already_inactive", **crusader_mode.get_status()}
    ok, response_text = _transition_crusader_mode("deactivate")
    if not ok:
        return error_response(500, response_text)
    main_orchestrator.audit_logger.log_event(
        "CRUSADER_MODE_DEACTIVATED",
        "User deactivated Crusader Mode via API",
        _resolve_audit_user(request),
    )
    return {"status": "deactivated", "message": response_text, **crusader_mode.get_status()}


@router.get("/receipt/{task_id}")
def get_receipt(task_id: str, request: Request):
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        from shared.receipts import get_receipt_service

        receipt_service = get_receipt_service("/home/lancelot/data")
        direct_match = receipt_service.get(task_id)
        if direct_match is not None:
            return {"receipt": direct_match.to_dict(), "request_id": request_id}

        matches = [receipt.to_dict() for receipt in receipt_service.search(task_id, limit=25)]
        if matches:
            return {"matches": matches, "request_id": request_id}

        return error_response(404, f"No receipts found for task or receipt ID: {task_id}", request_id=request_id)
    except Exception as exc:
        logger.exception("[%s] Receipt lookup failed for task_or_receipt_id=%s: %s", request_id, task_id, exc)
        return error_response(
            500,
            "Receipt lookup failed while reading the audit store.",
            request_id=request_id,
        )


@router.websocket("/ws/warroom")
async def ws_warroom(websocket: WebSocket):
    """War Room real-time event stream."""
    from warroom_ws import warroom_websocket

    await warroom_websocket(websocket)


@router.websocket("/live")
async def live_stream(websocket: WebSocket):
    """Gemini Live API stream with War Room session or token authentication."""
    from live_session import LiveSessionManager

    await websocket.accept()
    authenticated = False

    try:
        from src.core.auth_api import (
            get_warroom_session_cookie_name,
            verify_warroom_session_token,
        )

        cookie_name = get_warroom_session_cookie_name()
        cookie_token = websocket.cookies.get(cookie_name, "")
        authenticated = verify_warroom_session_token(cookie_token)
    except Exception:
        authenticated = False

    if not authenticated:
        try:
            auth_payload = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            auth_msg = json.loads(auth_payload)
        except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
            await websocket.close(code=4001, reason="Unauthorized")
            return

        if auth_msg.get("type") != "auth":
            await websocket.close(code=4001, reason="Unauthorized")
            return

        token = auth_msg.get("token", "")
        if API_TOKEN and not hmac.compare_digest(token or "", API_TOKEN):
            await websocket.close(code=4001, reason="Unauthorized")
            return

    if not main_orchestrator.client:
        await websocket.send_text("Error: Gemini client not initialized.")
        await websocket.close(code=4002, reason="Service unavailable")
        return

    session_mgr = LiveSessionManager(
        client=main_orchestrator.client,
        model_name=main_orchestrator.model_name,
        system_instruction=main_orchestrator.build_system_instruction(),
    )

    try:
        await session_mgr.connect()
        logger.info("Live API session connected.")
        while True:
            data = await websocket.receive_text()
            async for chunk in session_mgr.send_text(data):
                await websocket.send_text(chunk)
    except WebSocketDisconnect:
        logger.info("Live API session disconnected.")
    except Exception as exc:
        logger.exception(
            "Live API websocket failed for model=%s: %s",
            getattr(main_orchestrator, "model_name", "unknown"),
            exc,
        )
        try:
            await websocket.send_text("Error: Live API session failed; check gateway logs for model/session details.")
        except Exception as send_exc:
            logger.warning("Failed to send Live API websocket error response: %s", send_exc)
    finally:
        await session_mgr.close()
