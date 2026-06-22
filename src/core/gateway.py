"""FastAPI composition root for the Lancelot runtime gateway."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from gateway_admin_router import create_gateway_admin_router
import gateway_boot_support as _gateway_boot_support
import gateway_chat_runtime as _gateway_chat_runtime
from gateway_spa import mount_war_room_spa
from onboarding import OnboardingOrchestrator
from orchestrator import LancelotOrchestrator
from subsystem_manager import subsystem_manager
from boot import BootConfig, boot, bind_gateway_globals as bind_boot_globals
from gateway_oauth_routes import (
    bind_gateway_globals as bind_oauth_route_globals,
    router as oauth_router,
)
from gateway_chat_routes import (
    bind_gateway_globals as bind_chat_route_globals,
    router as chat_router,
)
from gateway_runtime_routes import (
    bind_gateway_globals as bind_runtime_route_globals,
    router as runtime_router,
)
from gateway_middleware import subsystem_gate_middleware
from gateway_health import (
    build_health_snapshot,
    build_readiness_snapshot,
    summarize_local_model_role_lane as _summarize_local_model_role_lane,
)
from gateway_receipt_proof import (
    TERMINAL_CHAT_RUN_STATUSES as _TERMINAL_CHAT_RUN_STATUSES,
    chat_run_payload as build_chat_run_payload,
)
from gateway_runtime_reports import (
    active_work_report_snapshot as build_active_work_report_snapshot,
    handle_operational_report_command,
    job_field as runtime_report_job_field,
    operator_notice_snapshot as build_operator_notice_snapshot,
    scheduler_report_snapshot as build_scheduler_report_snapshot,
)
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
from typing import Any
from oauth_callback_pages import render_callback_exception_page, render_callback_page
from src.core.runtime_pause import init_runtime_pause
from src.core.api_auth import require_authenticated_request
from src.core.auth_api import require_operator_capability
from chat_runs import ChatRun, ChatRunStore
from work_ledger import WorkItem, WorkLedgerStore
import feature_flags as _ff
from shared.receipts import (
    ActionType,
    CognitionTier,
    create_finalized_receipt,
    get_receipt_service,
)

# Configure process log level from deployment environment.
LOG_LEVEL = os.getenv("LANCELOT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger("lancelot.gateway")

# Request size limit allows file uploads without accepting unbounded bodies.
MAX_REQUEST_SIZE = 20_971_520
WARROOM_CLIENT_ERROR_MAX_BYTES = 8_192

_startup_time = None
_gateway_started = False

_error_count = 0
_total_requests = 0

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

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
)


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

import secret_cache

_boot_vault = None
try:
    from feature_flags import FEATURE_VAULT_SECRETS
    if FEATURE_VAULT_SECRETS:
        from connectors.vault import CredentialVault as _BootVault
        _boot_vault = _BootVault(config_path="config/vault.yaml")
        secret_cache.bootstrap(_boot_vault)
        secret_cache.scrub_environ()
        # Scrub vault key itself from environ; closes the remaining /proc exposure.
        # Safe because _boot_vault already holds the cipher in memory.
        if "LANCELOT_VAULT_KEY" in os.environ:
            del os.environ["LANCELOT_VAULT_KEY"]
            logger.info("LANCELOT_VAULT_KEY scrubbed from os.environ (vault cipher in memory).")
        logger.info("Vault-backed secret cache initialized (key_source=%s).",
                     getattr(_boot_vault, 'key_source', 'unknown'))
except Exception as _vault_exc:
    logger.warning("Vault bootstrap failed; falling back to os.getenv(): %s", _vault_exc)

API_TOKEN = secret_cache.get("LANCELOT_API_TOKEN") if secret_cache.is_bootstrapped() else os.getenv("LANCELOT_API_TOKEN")
DEV_MODE = os.getenv("LANCELOT_DEV_MODE", "").lower() in ("true", "1", "yes")


def set_api_token(value: str) -> None:
    global API_TOKEN
    API_TOKEN = value

rate_limiter = RateLimiter()
_orchestrator_chat_lock = threading.Lock()
_async_chat_tasks: set[asyncio.Task] = set()
_async_chat_worker_slot_lock: asyncio.Lock | None = None
_async_chat_worker_slot_loop: asyncio.AbstractEventLoop | None = None
_chat_progress_subscription_registered = False
CHAT_RUN_STALE_AFTER_SECONDS = int(os.getenv("LANCELOT_CHAT_RUN_STALE_AFTER_S", "3600"))
ACTIVE_WORK_QUIET_CHECKPOINT_AFTER_SECONDS = int(os.getenv("LANCELOT_WORK_QUIET_CHECKPOINT_AFTER_S", "300"))

# Long-lived services are created at import time and wired into startup phases.
main_orchestrator = LancelotOrchestrator(data_dir="/home/lancelot/data")
onboarding_orch = OnboardingOrchestrator(data_dir="/home/lancelot/data")
librarian = LibrarianV2(data_dir="/home/lancelot/data")
antigravity = AntigravityEngine(data_dir="/home/lancelot/data")
init_runtime_pause("/home/lancelot/data")
mfa_guard = MFAListener()
webhook_auth = WebhookAuthenticator()

sentry = MCPSentry(data_dir="/home/lancelot/data")
chat_run_store = ChatRunStore("/home/lancelot/data/chat/async_runs.sqlite")
work_ledger_store = WorkLedgerStore("/home/lancelot/data/work/work_ledger.sqlite")
main_orchestrator.work_ledger_store = work_ledger_store

# Crusader Mode: session-scoped, non-persistent
crusader_mode = CrusaderMode()
crusader_adapter = CrusaderAdapter()

# Self-build helpers are wired here so boot can inject runtime policy.
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

_BOOTSTRAP_MODEL_ROUTER_IMPL = _gateway_boot_support.bootstrap_model_router
_BOOTSTRAP_MODEL_DISCOVERY_IMPL = _gateway_boot_support.bootstrap_model_discovery
_RESTORE_PERSISTED_PROVIDER_IMPL = _gateway_boot_support.restore_persisted_provider




def _get_async_chat_worker_slot() -> asyncio.Lock:
    """Return the event-loop-local worker slot for governed async chat turns."""
    global _async_chat_worker_slot_lock, _async_chat_worker_slot_loop

    loop = asyncio.get_running_loop()
    if _async_chat_worker_slot_lock is None or _async_chat_worker_slot_loop is not loop:
        _async_chat_worker_slot_lock = asyncio.Lock()
        _async_chat_worker_slot_loop = loop
    return _async_chat_worker_slot_lock

# Chat and operator-runtime helper implementations live in gateway_chat_runtime.
# These wrappers keep gateway.py as the stable import surface for tests/routes.
_CHAT_RUNTIME_HELPERS = (
    "_run_orchestrator_chat",
    "_refresh_runtime_soul_from_store",
    "_transition_crusader_mode",
    "_chat_run_payload",
    "_optional_json_body",
    "_can_access_chat_run",
    "_can_access_work_item",
    "_sync_work_ledger_from_chat_run",
    "_close_superseded_retry_source",
    "_reconcile_superseded_retry_work",
    "_archive_pending_actioncards_for_work",
    "_emit_chat_run_event",
    "_record_chat_progress_event",
    "_record_persisted_chat_progress",
    "_classify_chat_run_status",
    "_normalized_command_text",
    "_is_fast_runtime_command",
    "_is_operator_work_status_command",
    "_preview_text",
    "_emit_fast_runtime_receipt",
    "_try_handle_fast_runtime_command",
    "_try_handle_operator_work_status_command",
    "_try_handle_operational_report_command",
    "_operator_notice_snapshot",
    "_active_work_report_snapshot",
    "_scheduler_report_snapshot",
    "_job_field",
    "_execute_chat_turn",
    "_execute_async_chat_run",
)

_CHAT_RUNTIME_WRAPPERS: dict[str, object] = {}

def _sync_chat_runtime_globals() -> None:
    payload = {
        "app": app,
        "logger": logger,
        "main_orchestrator": main_orchestrator,
        "onboarding_orch": onboarding_orch,
        "crusader_mode": crusader_mode,
        "crusader_adapter": crusader_adapter,
        "chat_run_store": chat_run_store,
        "work_ledger_store": work_ledger_store,
        "scheduler_service": scheduler_service,
        "_orchestrator_chat_lock": _orchestrator_chat_lock,
        "_get_async_chat_worker_slot": _get_async_chat_worker_slot,
        "_resolve_audit_user": _resolve_audit_user,
        "_gateway_started": _gateway_started,
        "_TERMINAL_CHAT_RUN_STATUSES": _TERMINAL_CHAT_RUN_STATUSES,
        "ActionType": ActionType,
        "CognitionTier": CognitionTier,
        "create_finalized_receipt": create_finalized_receipt,
        "get_receipt_service": get_receipt_service,
    }
    for optional_name in ("health_check",):
        if optional_name in globals():
            payload[optional_name] = globals()[optional_name]
    for helper_name, wrapper in _CHAT_RUNTIME_WRAPPERS.items():
        current = globals().get(helper_name)
        if current is not wrapper:
            payload[helper_name] = current
    _gateway_chat_runtime.bind_gateway_globals(**payload)
    _bind_gateway_route_globals()

def _chat_runtime_call(name: str, *args, **kwargs):
    _sync_chat_runtime_globals()
    return getattr(_gateway_chat_runtime, name)(*args, **kwargs)

def _make_chat_runtime_wrapper(name: str, *, is_async: bool = False):
    if is_async:
        async def _async_wrapper(*args, **kwargs):
            return await _chat_runtime_call(name, *args, **kwargs)
        return _async_wrapper

    def _wrapper(*args, **kwargs):
        return _chat_runtime_call(name, *args, **kwargs)
    return _wrapper

for _helper_name in _CHAT_RUNTIME_HELPERS:
    globals()[_helper_name] = _make_chat_runtime_wrapper(
        _helper_name,
        is_async=_helper_name in {
            "_run_orchestrator_chat",
            "_optional_json_body",
            "_record_chat_progress_event",
            "_execute_chat_turn",
            "_execute_async_chat_run",
        },
    )
_CHAT_RUNTIME_WRAPPERS.update({name: globals()[name] for name in _CHAT_RUNTIME_HELPERS})
del _helper_name


def _bind_gateway_route_globals() -> None:
    bind_chat_route_globals(
        logger=logger,
        main_orchestrator=main_orchestrator,
        crusader_mode=crusader_mode,
        chat_run_store=chat_run_store,
        work_ledger_store=work_ledger_store,
        rate_limiter=rate_limiter,
        verify_token=verify_token,
        error_response=error_response,
        make_request_id=make_request_id,
        _require_request_capability=_require_request_capability,
        _resolve_audit_user=_resolve_audit_user,
        _optional_json_body=_optional_json_body,
        _can_access_chat_run=_can_access_chat_run,
        _can_access_work_item=_can_access_work_item,
        _sync_work_ledger_from_chat_run=_sync_work_ledger_from_chat_run,
        _chat_run_payload=_chat_run_payload,
        _emit_chat_run_event=_emit_chat_run_event,
        _execute_chat_turn=_execute_chat_turn,
        _execute_async_chat_run=_execute_async_chat_run,
        _track_async_chat_task=_track_async_chat_task,
        _try_handle_fast_runtime_command=_try_handle_fast_runtime_command,
        _try_handle_operational_report_command=_try_handle_operational_report_command,
        _preview_text=_preview_text,
        _archive_pending_actioncards_for_work=_archive_pending_actioncards_for_work,
        MAX_REQUEST_SIZE=MAX_REQUEST_SIZE,
        ACTIVE_WORK_QUIET_CHECKPOINT_AFTER_SECONDS=ACTIVE_WORK_QUIET_CHECKPOINT_AFTER_SECONDS,
    )
    bind_runtime_route_globals(
        logger=logger,
        main_orchestrator=main_orchestrator,
        crusader_mode=crusader_mode,
        rate_limiter=rate_limiter,
        verify_token=verify_token,
        error_response=error_response,
        make_request_id=make_request_id,
        _require_request_capability=_require_request_capability,
        _resolve_audit_user=_resolve_audit_user,
        _execute_chat_turn=_execute_chat_turn,
        _transition_crusader_mode=_transition_crusader_mode,
        mfa_guard=mfa_guard,
        secret_cache=secret_cache,
        _boot_vault=_boot_vault,
        API_TOKEN=API_TOKEN,
        set_api_token=set_api_token,
    )


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
        "work_ledger_store": work_ledger_store,
        "subsystem_manager": subsystem_manager,
        "_boot_vault": _boot_vault,
        "secret_cache": secret_cache,
        "API_TOKEN": API_TOKEN,
        "_app_version": _app_version,
        "_startup_time": _startup_time,
        "verify_token": verify_token,
        "_init_memory": _gateway_boot_support.init_memory,
        "_shutdown_memory": _gateway_boot_support.shutdown_memory,
        "_init_soul": _gateway_boot_support.init_soul,
        "_shutdown_soul": _gateway_boot_support.shutdown_soul,
        "_init_skills": _gateway_boot_support.init_skills,
        "_shutdown_skills": _gateway_boot_support.shutdown_skills,
        "_init_scheduler": _gateway_boot_support.init_scheduler,
        "_shutdown_scheduler": _gateway_boot_support.shutdown_scheduler,
        "_init_health_monitor": _gateway_boot_support.init_health_monitor,
        "_shutdown_health_monitor": _gateway_boot_support.shutdown_health_monitor,
        "_init_host_bridge": _gateway_boot_support.init_host_bridge,
        "_shutdown_host_bridge": _gateway_boot_support.shutdown_host_bridge,
        "_init_uab": _gateway_boot_support.init_uab,
        "_shutdown_uab": _gateway_boot_support.shutdown_uab,
        "_init_hive": _gateway_boot_support.init_hive,
        "_shutdown_hive": _gateway_boot_support.shutdown_hive,
        "_resolve_peer_key": _gateway_boot_support.resolve_peer_key,
        "_init_federation": _gateway_boot_support.init_federation,
        "_shutdown_federation": _gateway_boot_support.shutdown_federation,
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
        _require_request_capability=_require_request_capability,
        _bootstrap_model_discovery=_bootstrap_model_discovery,
    )
    if "_optional_json_body" in globals():
        _bind_gateway_route_globals()

def _bootstrap_model_router() -> bool:
    _sync_gateway_runtime_bindings()
    return _BOOTSTRAP_MODEL_ROUTER_IMPL()

def _restore_persisted_provider(persisted_provider: str, orchestrator=None) -> bool:
    _sync_gateway_runtime_bindings()
    return _RESTORE_PERSISTED_PROVIDER_IMPL(persisted_provider, orchestrator)

def _bootstrap_model_discovery():
    _sync_gateway_runtime_bindings()
    original = _gateway_boot_support.bootstrap_model_router
    try:
        _gateway_boot_support.bootstrap_model_router = _bootstrap_model_router
        return _BOOTSTRAP_MODEL_DISCOVERY_IMPL()
    finally:
        _gateway_boot_support.bootstrap_model_router = original

def _resolve_peer_key(peer_registry, topology, instance_id: str):
    _sync_gateway_runtime_bindings()
    return _gateway_boot_support.resolve_peer_key(peer_registry, topology, instance_id)

def _shutdown_hive(objects):
    _sync_gateway_runtime_bindings()
    return _gateway_boot_support.shutdown_hive(objects)

def _shutdown_federation(objects):
    _sync_gateway_runtime_bindings()
    return _gateway_boot_support.shutdown_federation(objects)

async def startup_event():
    global _boot_result, scheduler_service, _startup_time, _gateway_started

    _sync_gateway_runtime_bindings()
    _boot_result = await boot(app, _build_boot_config())
    app.state.boot_result = _boot_result
    scheduler_service = _gateway_boot_support.scheduler_service
    if _boot_result is not None:
        _startup_time = _boot_result.env.startup_time or time.time()
    _gateway_started = True
    _register_chat_progress_recorder()
    stale_runs = chat_run_store.fail_stale_active_runs(
        max_age_seconds=CHAT_RUN_STALE_AFTER_SECONDS,
        reason="Async chat run was still active after gateway restart.",
    )
    for stale_run in stale_runs:
        _sync_work_ledger_from_chat_run(stale_run, event_type="chat_run_stale_failed")
    superseded = _reconcile_superseded_retry_work()
    work_ledger_store.checkpoint_quiet_work(
        max_quiet_seconds=CHAT_RUN_STALE_AFTER_SECONDS,
        reason="gateway_startup_stale_work",
    )
    if stale_runs:
        logger.warning("Marked %d stale async chat run(s) failed after startup.", len(stale_runs))
    if superseded:
        logger.info("Closed %d superseded retry source work item(s) after startup.", superseded)

async def shutdown_event():
    global _boot_result, scheduler_service, _startup_time, _gateway_started

    work_ledger_store.checkpoint_open_work(
        reason="gateway_shutdown",
        dedupe_window_seconds=60,
    )
    for task in list(_async_chat_tasks):
        task.cancel()
    if _async_chat_tasks:
        await asyncio.gather(*list(_async_chat_tasks), return_exceptions=True)
        _async_chat_tasks.clear()
    chat_run_store.close()
    work_ledger_store.close()
    _sync_gateway_runtime_bindings()
    await shutdown(app, _boot_result)
    scheduler_service = _gateway_boot_support.scheduler_service
    _boot_result = None
    _startup_time = None
    _gateway_started = False

def _register_chat_progress_recorder() -> None:
    global _chat_progress_subscription_registered
    if _chat_progress_subscription_registered:
        return
    try:
        from event_bus import event_bus

        event_bus.subscribe("chat.progress", _record_chat_progress_event)
        _chat_progress_subscription_registered = True
    except Exception as exc:
        logger.warning("Failed to register chat progress recorder: %s", exc)




def _track_async_chat_task(task: asyncio.Task) -> None:
    _async_chat_tasks.add(task)

    def _done(done_task: asyncio.Task) -> None:
        _async_chat_tasks.discard(done_task)
        try:
            done_task.result()
        except asyncio.CancelledError:
            logger.info("Async chat task cancelled")
        except Exception as exc:
            logger.error("Async chat task crashed: %s", exc)

    task.add_done_callback(_done)


# Chat-run and work-ledger HTTP routes are registered by gateway_chat_routes.

_sync_gateway_runtime_bindings()


@app.get("/health")
def health_check():
    """Return component health for operators and container probes."""
    try:
        return build_health_snapshot(
            main_orchestrator=main_orchestrator,
            crusader_mode=crusader_mode,
            app_version=_app_version,
            startup_time=_startup_time,
            error_count=_error_count,
            total_requests=_total_requests,
            logger=logger,
        )
    except Exception as exc:
        logger.error("Health check error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": "Health check failed"},
        )


@app.get("/ready")
def readiness_check():
    """Return readiness state for traffic admission."""
    status_code, content = build_readiness_snapshot(
        main_orchestrator=main_orchestrator,
        startup_time=_startup_time,
    )
    return JSONResponse(
        status_code=status_code,
        content=content,
    )


def _sanitize_warroom_client_error_value(value: Any, limit: int = 2000) -> str:
    """Return a bounded single-line value suitable for structured logs."""
    text = str(value or "")
    text = "".join(ch if ch.isprintable() and ch not in "\r\n\t" else " " for ch in text)
    return " ".join(text.split())[:limit]


@app.post(
    "/api/warroom/client-error",
    dependencies=[
        Depends(require_authenticated_request),
        Depends(require_operator_capability("warroom.login")),
    ],
)
async def warroom_client_error(request: Request):
    """Record War Room browser-side failures for operator debugging."""
    raw_body = await request.body()
    if len(raw_body) > WARROOM_CLIENT_ERROR_MAX_BYTES:
        return JSONResponse(
            status_code=413,
            content={"error": "Client error report too large", "status": 413},
        )

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}

    logger.error(
        "warroom_client_error kind=%s href=%s ua=%s message=%s stack=%s",
        _sanitize_warroom_client_error_value(payload.get("kind"), 80),
        _sanitize_warroom_client_error_value(payload.get("href"), 300),
        _sanitize_warroom_client_error_value(payload.get("user_agent"), 500),
        _sanitize_warroom_client_error_value(payload.get("message"), 1000),
        _sanitize_warroom_client_error_value(payload.get("stack"), 3000),
    )
    return {"status": "recorded"}


from ucp_connector import UCPConnector

ucp_connector = UCPConnector(audit_logger=main_orchestrator.audit_logger)
_sync_gateway_runtime_bindings()
app.include_router(chat_router)
app.include_router(runtime_router)
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
