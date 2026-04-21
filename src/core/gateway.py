import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, File, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from gateway_admin_router import create_gateway_admin_router
from gateway_request_models import (
    ChatMessage,
    MfaSubmitRequest,
    parse_request_model_or_error,
)
import gateway_boot_support as _gateway_boot_support
from gateway_spa import mount_war_room_spa
from onboarding import OnboardingOrchestrator
from orchestrator import LancelotOrchestrator
from subsystem_manager import subsystem_manager
from boot import BootConfig, boot, bind_gateway_globals as bind_boot_globals
from gateway_oauth_routes import (
    bind_gateway_globals as bind_oauth_route_globals,
    router as oauth_router,
)
from gateway_middleware import subsystem_gate_middleware
from gateway_security import (
    RateLimiter,
    _require_request_capability,
    bind_gateway_globals as bind_security_globals,
    error_response,
    make_request_id,
    verify_token,
)
from shutdown import bind_gateway_globals as bind_shutdown_globals, shutdown
from librarian_v2 import LibrarianV2
from antigravity_engine import AntigravityEngine
from security_bridge import MFAListener, WebhookAuthenticator
from mcp_sentry import MCPSentry
from vault import SecretVault
from sandbox import SandboxExecutor
from api_discovery import APIDiscoveryEngine
from post_dispatcher import PostDispatcher
from chat_poller import ChatPoller
from telegram_bot import TelegramBot
from crusader import CrusaderMode, CrusaderAdapter
import threading
import time
import os
import json
import logging
from oauth_callback_pages import render_callback_exception_page, render_callback_page
from src.core.runtime_pause import init_runtime_pause
import feature_flags as _ff

# F1: Configurable log level
LOG_LEVEL = os.getenv("LANCELOT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger("lancelot.gateway")

# S11: Request size limit (20 MB for file uploads)
MAX_REQUEST_SIZE = 20_971_520

# F8: Startup timestamp for uptime tracking
_startup_time = None

# Error rate tracking
_error_count = 0
_total_requests = 0

# Read version from VERSION file (single source of truth)
from update_checker import read_current_version
_app_version = read_current_version()


def _resolve_audit_user(request: Request) -> str:
    """Resolve the authenticated operator display name for text audit logs."""
    try:
        from src.core.auth_api import get_api_key_identity, resolve_operator_identity

        identity = resolve_operator_identity(request)
        if identity is None:
            identity = get_api_key_identity(request)
        if identity.display_name:
            return identity.display_name
        if identity.operator_id:
            return identity.operator_id
    except Exception as exc:
        logger.debug("Falling back to generic audit user: %s", exc)
    return "operator"


@asynccontextmanager
async def _gateway_lifespan(_app: FastAPI):
    await startup_event()
    try:
        yield
    finally:
        await shutdown_event()


app = FastAPI(lifespan=_gateway_lifespan)

# S11: CORS middleware â€” explicit methods/headers (F-004 hardening)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
)


# F-005: Security headers middleware + request counting
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    global _total_requests, _error_count
    _total_requests += 1
    response = await call_next(request)
    if response.status_code >= 500:
        _error_count += 1
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

app.middleware("http")(subsystem_gate_middleware)

# --- Vault-Backed Secret Cache (Phase 1) ---
import secret_cache

_boot_vault = None
try:
    from feature_flags import FEATURE_VAULT_SECRETS
    if FEATURE_VAULT_SECRETS:
        from connectors.vault import CredentialVault as _BootVault
        _boot_vault = _BootVault(config_path="config/vault.yaml")
        secret_cache.bootstrap(_boot_vault)
        secret_cache.scrub_environ()
        # Phase 3: Scrub vault key itself from environ â€” closes last /proc exposure.
        # Safe because _boot_vault already holds the cipher in memory.
        if "LANCELOT_VAULT_KEY" in os.environ:
            del os.environ["LANCELOT_VAULT_KEY"]
            logger.info("LANCELOT_VAULT_KEY scrubbed from os.environ (vault cipher in memory).")
        logger.info("Vault-backed secret cache initialized (key_source=%s).",
                     getattr(_boot_vault, 'key_source', 'unknown'))
except Exception as _vault_exc:
    logger.warning("Vault bootstrap failed â€” falling back to os.getenv(): %s", _vault_exc)

# --- API Authentication ---
API_TOKEN = secret_cache.get("LANCELOT_API_TOKEN") if secret_cache.is_bootstrapped() else os.getenv("LANCELOT_API_TOKEN")
DEV_MODE = os.getenv("LANCELOT_DEV_MODE", "").lower() in ("true", "1", "yes")




# S11: Rate limiter instance
rate_limiter = RateLimiter()

# Long-lived services are created at import time and wired into startup phases.
main_orchestrator = LancelotOrchestrator(data_dir="/home/lancelot/data")
onboarding_orch = OnboardingOrchestrator(data_dir="/home/lancelot/data")
librarian = LibrarianV2(data_dir="/home/lancelot/data")
antigravity = AntigravityEngine(data_dir="/home/lancelot/data")
init_runtime_pause("/home/lancelot/data")
mfa_guard = MFAListener()
webhook_auth = WebhookAuthenticator()

sentry = MCPSentry(data_dir="/home/lancelot/data")

# Crusader Mode: session-scoped, non-persistent
crusader_mode = CrusaderMode()
crusader_adapter = CrusaderAdapter()

# Forge of Innovation modules
forge_vault = SecretVault(data_dir="/home/lancelot/data")
forge_sandbox = SandboxExecutor()
forge_discovery = APIDiscoveryEngine(orchestrator=main_orchestrator)
forge_dispatcher = PostDispatcher(vault=forge_vault)
# Communications: Select backend based on LANCELOT_COMMS_TYPE
COMMS_TYPE = os.getenv("LANCELOT_COMMS_TYPE", "").lower()
chat_poller = None
telegram_bot = None
scheduler_service = None  # Module-level ref for schedule_job skill

if COMMS_TYPE == "telegram":
    _voice_proc = None
    if _ff.FEATURE_VOICE_NOTES:
        try:
            from voice_processor import VoiceProcessor
            _voice_proc = VoiceProcessor()
            logger.info("Voice notes enabled for Telegram")
        except Exception as _vp_err:
            logger.warning("Voice processor init failed: %s", _vp_err)
    telegram_bot = TelegramBot(orchestrator=main_orchestrator, voice_processor=_voice_proc)
    logger.info("Comms backend: Telegram")
elif COMMS_TYPE == "google_chat":
    chat_poller = ChatPoller(data_dir="/home/lancelot/data", orchestrator=main_orchestrator)
    logger.info("Comms backend: Google Chat")
elif COMMS_TYPE in ("", "none"):
    logger.info("Comms backend: disabled")
else:
    logger.warning(
        "Unsupported comms backend '%s' configured; communications disabled.",
        COMMS_TYPE,
    )

_boot_result = None

_BOOTSTRAP_MODEL_ROUTER_IMPL = _gateway_boot_support._bootstrap_model_router
_BOOTSTRAP_MODEL_DISCOVERY_IMPL = _gateway_boot_support._bootstrap_model_discovery
_RESTORE_PERSISTED_PROVIDER_IMPL = _gateway_boot_support._restore_persisted_provider

def _build_boot_config() -> BootConfig:
    return BootConfig(
        api_token=API_TOKEN,
        app_version=_app_version,
        boot_vault=_boot_vault,
        verify_token=verify_token,
        secret_cache=secret_cache,
    )

def _sync_gateway_runtime_bindings() -> None:
    shared_globals = {
        "app": app,
        "logger": logger,
        "main_orchestrator": main_orchestrator,
        "onboarding_orch": onboarding_orch,
        "librarian": librarian,
        "antigravity": antigravity,
        "mfa_guard": mfa_guard,
        "webhook_auth": webhook_auth,
        "sentry": sentry,
        "forge_vault": forge_vault,
        "forge_sandbox": forge_sandbox,
        "forge_discovery": forge_discovery,
        "forge_dispatcher": forge_dispatcher,
        "chat_poller": chat_poller,
        "telegram_bot": telegram_bot,
        "scheduler_service": scheduler_service,
        "subsystem_manager": subsystem_manager,
        "_boot_vault": _boot_vault,
        "secret_cache": secret_cache,
        "API_TOKEN": API_TOKEN,
        "_app_version": _app_version,
        "_startup_time": _startup_time,
        "verify_token": verify_token,
        "_init_memory": _gateway_boot_support._init_memory,
        "_shutdown_memory": _gateway_boot_support._shutdown_memory,
        "_init_soul": _gateway_boot_support._init_soul,
        "_shutdown_soul": _gateway_boot_support._shutdown_soul,
        "_init_skills": _gateway_boot_support._init_skills,
        "_shutdown_skills": _gateway_boot_support._shutdown_skills,
        "_init_scheduler": _gateway_boot_support._init_scheduler,
        "_shutdown_scheduler": _gateway_boot_support._shutdown_scheduler,
        "_init_health_monitor": _gateway_boot_support._init_health_monitor,
        "_shutdown_health_monitor": _gateway_boot_support._shutdown_health_monitor,
        "_init_bal": _gateway_boot_support._init_bal,
        "_shutdown_bal": _gateway_boot_support._shutdown_bal,
        "_init_host_bridge": _gateway_boot_support._init_host_bridge,
        "_shutdown_host_bridge": _gateway_boot_support._shutdown_host_bridge,
        "_init_uab": _gateway_boot_support._init_uab,
        "_shutdown_uab": _gateway_boot_support._shutdown_uab,
        "_init_hive": _gateway_boot_support._init_hive,
        "_shutdown_hive": _gateway_boot_support._shutdown_hive,
        "_resolve_peer_key": _gateway_boot_support._resolve_peer_key,
        "_init_federation": _gateway_boot_support._init_federation,
        "_shutdown_federation": _gateway_boot_support._shutdown_federation,
        "_bootstrap_model_discovery": _bootstrap_model_discovery,
        "_restore_persisted_provider": _restore_persisted_provider,
        "_bootstrap_model_router": _bootstrap_model_router,
    }
    _gateway_boot_support.bind_gateway_globals(**shared_globals)
    bind_boot_globals(**shared_globals)
    bind_shutdown_globals(**shared_globals)
    bind_security_globals(
        logger=logger,
        API_TOKEN=API_TOKEN,
        DEV_MODE=DEV_MODE,
    )
    bind_oauth_route_globals(
        logger=logger,
        main_orchestrator=main_orchestrator,
        onboarding_orch=onboarding_orch,
        render_callback_page=render_callback_page,
        render_callback_exception_page=render_callback_exception_page,
        error_response=error_response,
        verify_token=verify_token,
        _bootstrap_model_discovery=_bootstrap_model_discovery,
    )

def _bootstrap_model_router() -> bool:
    _sync_gateway_runtime_bindings()
    return _BOOTSTRAP_MODEL_ROUTER_IMPL()

def _restore_persisted_provider(persisted_provider: str, orchestrator=None) -> bool:
    _sync_gateway_runtime_bindings()
    return _RESTORE_PERSISTED_PROVIDER_IMPL(persisted_provider, orchestrator)

def _bootstrap_model_discovery():
    _sync_gateway_runtime_bindings()
    original = _gateway_boot_support._bootstrap_model_router
    try:
        _gateway_boot_support._bootstrap_model_router = _bootstrap_model_router
        return _BOOTSTRAP_MODEL_DISCOVERY_IMPL()
    finally:
        _gateway_boot_support._bootstrap_model_router = original

def _resolve_peer_key(peer_registry, topology, instance_id: str):
    _sync_gateway_runtime_bindings()
    return _gateway_boot_support._resolve_peer_key(peer_registry, topology, instance_id)

def _shutdown_bal(objects):
    _sync_gateway_runtime_bindings()
    return _gateway_boot_support._shutdown_bal(objects)

def _shutdown_hive(objects):
    _sync_gateway_runtime_bindings()
    return _gateway_boot_support._shutdown_hive(objects)

def _shutdown_federation(objects):
    _sync_gateway_runtime_bindings()
    return _gateway_boot_support._shutdown_federation(objects)

async def startup_event():
    global _boot_result, scheduler_service, _startup_time

    _sync_gateway_runtime_bindings()
    _boot_result = await boot(app, _build_boot_config())
    app.state.boot_result = _boot_result
    scheduler_service = _gateway_boot_support.scheduler_service
    if _boot_result is not None:
        _startup_time = _boot_result.env.startup_time or time.time()

async def shutdown_event():
    global _boot_result, scheduler_service, _startup_time

    _sync_gateway_runtime_bindings()
    await shutdown(app, _boot_result)
    scheduler_service = _gateway_boot_support.scheduler_service
    _boot_result = None
    _startup_time = None

_sync_gateway_runtime_bindings()

def _refresh_runtime_soul_from_store():
    """Reload the active Soul from store and apply it to live runtime subscribers."""
    from soul.store import load_active_soul
    from soul.layers import load_overlays, merge_soul

    active_soul = load_active_soul()
    if active_soul is None:
        raise RuntimeError("No active Soul found for runtime refresh")

    overlays = load_overlays()
    if overlays:
        active_soul = merge_soul(active_soul, overlays)

    apply_runtime_soul = getattr(app.state, "apply_runtime_soul", None)
    if callable(apply_runtime_soul):
        apply_runtime_soul(active_soul)
    else:
        main_orchestrator.soul = active_soul
        app.state.active_soul = active_soul

    return active_soul


def _transition_crusader_mode(action: str) -> tuple[bool, str]:
    """Apply a Crusader mode transition and refresh live runtime Soul subscribers."""
    if action == "activate":
        response_text = crusader_mode.activate()
        rollback = crusader_mode.deactivate
        failure_prefix = "Crusader activation"
    elif action == "deactivate":
        response_text = crusader_mode.deactivate()
        rollback = crusader_mode.activate
        failure_prefix = "Crusader deactivation"
    else:
        raise ValueError(f"Unsupported Crusader action: {action}")

    try:
        _refresh_runtime_soul_from_store()
        return True, response_text
    except Exception as exc:
        logger.error("%s runtime Soul refresh failed: %s", failure_prefix, exc)
        try:
            rollback()
            _refresh_runtime_soul_from_store()
        except Exception as rollback_exc:
            logger.error("%s rollback failed: %s", failure_prefix, rollback_exc)
        return False, f"{failure_prefix} failed to refresh runtime Soul: {exc}"
@app.get("/api/chat/history")
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


@app.post("/chat")
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

        # Check Onboarding State
        onboarding_orch.state = onboarding_orch._determine_state()

        if onboarding_orch.state != "READY":
            response_text = onboarding_orch.process(user, message)
        else:
            # --- CRUSADER MODE INTERCEPT ---
            is_trigger, action = crusader_mode.should_intercept(message)

            if is_trigger:
                if action == "activate":
                    ok, response_text = _transition_crusader_mode("activate")
                    if ok:
                        main_orchestrator.audit_logger.log_event(
                            "CRUSADER_MODE_ACTIVATED",
                            "User activated Crusader Mode",
                            user
                        )
                else:
                    ok, response_text = _transition_crusader_mode("deactivate")
                    if ok:
                        main_orchestrator.audit_logger.log_event(
                            "CRUSADER_MODE_DEACTIVATED",
                            "User deactivated Crusader Mode",
                            user
                        )
            elif crusader_mode.is_active:
                if crusader_adapter.check_auto_pause(message):
                    response_text = (
                        "Authority required.\n"
                        "This operation is restricted even in Crusader Mode."
                    )
                    main_orchestrator.audit_logger.log_event(
                        "CRUSADER_AUTO_PAUSE",
                        f"Blocked: {message}",
                        user
                    )
                else:
                    response_text = main_orchestrator.chat(
                        message,
                        crusader_mode=True,
                        channel=req_channel,
                        session_id=identity.session_id,
                        operator_id=identity.operator_id,
                        operator_name=identity.display_name or user,
                    )
                    response_text = crusader_adapter.format_response(
                        response_text
                    )
            else:
                # Standard mode
                response_text = main_orchestrator.chat(
                    message,
                    channel=req_channel,
                    session_id=identity.session_id,
                    operator_id=identity.operator_id,
                    operator_name=identity.display_name or user,
                )

        return {
            "response": response_text,
            "crusader_mode": crusader_mode.is_active,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error(f"[{request_id}] Chat error: {e}")
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/chat/upload")
async def chat_with_files(
    request: Request,
    text: str = Form(""),
    user: str = Form("Commander"),
    files: list[UploadFile] = File(default=[]),
    save_to_workspace: bool = Form(default=False),
):
    """
    Chat endpoint with file/image upload support.
    Accepts multipart/form-data with text + files.
    Images are sent to Gemini as vision input.
    Documents are read as text and included in context.
    """
    from typing import List
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
        for f in files:
            file_bytes = await f.read()
            mime = f.content_type or "application/octet-stream"
            attachments.append(ChatAttachment(
                filename=f.filename or "unknown",
                mime_type=mime,
                data=file_bytes,
            ))

            # Optionally save to shared workspace
            if save_to_workspace:
                workspace_path = "/home/lancelot/workspace"
                os.makedirs(workspace_path, exist_ok=True)
                safe_name = os.path.basename(f.filename or "upload")
                save_path = os.path.join(workspace_path, safe_name)
                with open(save_path, "wb") as wf:
                    wf.write(file_bytes)
                logger.info(f"[{request_id}] Saved upload to workspace: {save_path}")

        logger.info(f"[{request_id}] Upload from {resolved_user}: text={text[:50]}... files={len(attachments)}")

        # Route through onboarding/crusader/orchestrator
        onboarding_orch.state = onboarding_orch._determine_state()
        if onboarding_orch.state != "READY":
            response_text = onboarding_orch.process(resolved_user, text)
        else:
            is_trigger, action = crusader_mode.should_intercept(text)
            if is_trigger:
                if action == "activate":
                    _ok, response_text = _transition_crusader_mode("activate")
                else:
                    _ok, response_text = _transition_crusader_mode("deactivate")
            elif crusader_mode.is_active:
                if crusader_adapter.check_auto_pause(text):
                    response_text = "Authority required.\nThis operation is restricted even in Crusader Mode."
                else:
                    response_text = main_orchestrator.chat(
                        text,
                        crusader_mode=True,
                        attachments=attachments,
                        channel="warroom",
                        session_id=identity.session_id,
                        operator_id=identity.operator_id,
                        operator_name=identity.display_name or resolved_user,
                    )
                    response_text = crusader_adapter.format_response(response_text)
            else:
                response_text = main_orchestrator.chat(
                    text,
                    attachments=attachments,
                    channel="warroom",
                    session_id=identity.session_id,
                    operator_id=identity.operator_id,
                    operator_name=identity.display_name or resolved_user,
                )

        return {
            "response": response_text,
            "crusader_mode": crusader_mode.is_active,
            "request_id": request_id,
            "files_received": len(attachments),
        }
    except Exception as e:
        logger.error(f"[{request_id}] Upload chat error: {e}")
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/mfa_submit")
async def mfa_submit(request: Request):
    """
    Receives MFA code and unblocks the security bridge.
    Payload: {"code": "123456", "task_id": "..."}
    """
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

        logger.info(f"[{request_id}] MFA Code Received for Task {task_id}")

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
            return error_response(403, "MFA challenge is bound to a different operator/session.", request_id=request_id)
        else:
            return error_response(404, "Unknown Task ID or no pending challenge.", request_id=request_id)
            
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


# â”€â”€ Phase 2: Secret Rotation Endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.post("/api/secrets/reload")
async def reload_secrets(request: Request):
    """Reload secrets from vault into cache without restart.

    Owner-token-protected. Returns count of changed secrets.
    Emits SYSTEM receipt with action secret_rotation.
    """
    global API_TOKEN
    authz_error = _require_request_capability(request, "platform.admin")
    if authz_error is not None:
        return authz_error
    try:
        if not _boot_vault or not secret_cache.is_bootstrapped():
            return JSONResponse(status_code=503, content={
                "error": "Vault not initialized â€” secret rotation unavailable",
            })
        changed = secret_cache.reload(_boot_vault)
        changed_count = sum(1 for v in changed.values() if v)

        # Update module-level API_TOKEN if it changed
        if changed.get("LANCELOT_API_TOKEN"):
            API_TOKEN = secret_cache.get("LANCELOT_API_TOKEN")

        # Emit receipt
        try:
            from shared.receipts import ReceiptService
            _rs = ReceiptService(data_dir="/home/lancelot/data")
            _rs.create_receipt(
                task_id="secret_rotation",
                action="secret_rotation",
                category="SYSTEM",
                result={"changed_count": changed_count},
            )
        except Exception as exc:
            logger.warning("Secret rotation receipt emission failed: %s", exc)

        logger.info("Secrets reloaded: %d changed", changed_count)
        return {"status": "ok", "changed_count": changed_count}
    except Exception as e:
        logger.error("Secret reload failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Reload failed"})


@app.get("/health")
def health_check():
    """F6: Enhanced health check with component status."""
    try:
        local_llm_ready = False
        local_llm_loaded = False
        local_llm_status = "unavailable"
        local_llm_last_error = None
        local_llm_last_verified_at = None
        local_llm_last_checked_at = None
        local_llm_consecutive_failures = 0
        local_llm_last_smoke_elapsed_ms = None
        if getattr(main_orchestrator, "local_model", None) is not None:
            try:
                local_health = main_orchestrator.local_model.health()
                local_llm_ready = bool(local_health.get("ready"))
                local_llm_loaded = bool(local_health.get("loaded", local_llm_ready))
                local_llm_status = local_health.get(
                    "status",
                    "ok" if local_llm_ready else "degraded",
                )
                local_llm_last_error = local_health.get("last_error")
                local_llm_last_verified_at = local_health.get("last_verified_at")
                local_llm_last_checked_at = local_health.get("last_checked_at")
                local_llm_consecutive_failures = local_health.get("consecutive_failures", 0)
                local_llm_last_smoke_elapsed_ms = local_health.get("last_smoke_elapsed_ms")
            except Exception as exc:
                local_llm_last_error = str(exc)
                local_llm_status = "unavailable"
        components = {
            "gateway": "ok",
            "orchestrator": "ok" if main_orchestrator.provider else "degraded",
            "local_llm": "ok" if local_llm_ready else ("degraded" if local_llm_loaded else "unavailable"),
            "sentry": "ok",
            "vault": "ok",
            "memory": "ok" if getattr(main_orchestrator, '_memory_enabled', False) else "disabled",
        }
        uptime = round(time.time() - _startup_time, 1) if _startup_time else 0
        return {
            "status": "online",
            "version": _app_version,
            "components": components,
            "local_llm": {
                "loaded": local_llm_loaded,
                "ready": local_llm_ready,
                "status": local_llm_status,
                "last_error": local_llm_last_error,
                "last_verified_at": local_llm_last_verified_at,
                "last_checked_at": local_llm_last_checked_at,
                "consecutive_failures": local_llm_consecutive_failures,
                "last_smoke_elapsed_ms": local_llm_last_smoke_elapsed_ms,
            },
            "crusader_mode": crusader_mode.is_active,
            "uptime_seconds": uptime,
            "error_count": _error_count,
            "total_requests": _total_requests,
            "error_rate": round(_error_count / max(_total_requests, 1) * 100, 2),
        }
    except Exception as exc:
        logger.error("Health check error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": "Health check failed"},
        )


@app.get("/ready")
def readiness_check():
    """F8: Readiness probe â€” checks all components are initialized."""
    ready = _startup_time is not None
    components = {
        "gateway": "ok",
        "orchestrator": "ok" if main_orchestrator.provider else "degraded",
        "sentry": "ok",
        "memory": "ok" if getattr(main_orchestrator, '_memory_enabled', False) else "disabled",
    }
    all_ok = all(v in ("ok", "disabled") for v in components.values())
    status_code = 200 if (ready and all_ok) else 503
    return JSONResponse(
        status_code=status_code,
        content={"ready": ready and all_ok, "components": components},
    )


@app.get("/crusader_status")
def crusader_status(request: Request):
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    return crusader_mode.get_status()


@app.post("/api/crusader/activate")
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


@app.post("/api/crusader/deactivate")
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


@app.get("/receipt/{task_id}")
def get_receipt(task_id: str, request: Request):
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        from shared.receipts import get_receipt_service

        receipt_svc = get_receipt_service("/home/lancelot/data")
        direct_match = receipt_svc.get(task_id)
        if direct_match is not None:
            return {"receipt": direct_match.to_dict(), "request_id": request_id}

        matches = [receipt.to_dict() for receipt in receipt_svc.search(task_id, limit=25)]
        if matches:
            return {"matches": matches, "request_id": request_id}

        return error_response(404, f"No receipts found for task or receipt ID: {task_id}", request_id=request_id)
    except Exception:
        return error_response(500, "Internal server error", request_id=request_id)


# --- War Room WebSocket ---

from warroom_ws import warroom_websocket


@app.websocket("/ws/warroom")
async def ws_warroom(websocket: WebSocket):
    """War Room real-time event stream."""
    await warroom_websocket(websocket)


# --- Live API (Real-Time Streaming) ---

from live_session import LiveSessionManager


@app.websocket("/live")
async def live_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time streaming via Gemini Live API.
    Browser clients authenticate via the War Room session cookie.
    Programmatic clients can authenticate via first-message auth:
    {"type": "auth", "token": "<LANCELOT_API_TOKEN>"}
    """
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
        system_instruction=main_orchestrator._build_system_instruction(),
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
    except Exception as e:
        logger.error(f"Live API error: {e}")
        try:
            await websocket.send_text("Error: internal server error")
        except Exception as send_exc:
            logger.warning("Failed to send Live API websocket error response: %s", send_exc)
    finally:
        await session_mgr.close()


# --- UCP (Universal Commerce Protocol) ---

from ucp_connector import UCPConnector

ucp_connector = UCPConnector(audit_logger=main_orchestrator.audit_logger)
app.include_router(oauth_router)
app.include_router(
    create_gateway_admin_router(
        error_response=error_response,
        require_request_capability=_require_request_capability,
        make_request_id=make_request_id,
        webhook_auth=webhook_auth,
        sentry=sentry,
        forge_discovery=forge_discovery,
        forge_dispatcher=forge_dispatcher,
        ucp_connector=ucp_connector,
        logger=logger,
    )
)
mount_war_room_spa(app, logger=logger)
