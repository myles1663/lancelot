"""FastAPI composition root for the Lancelot runtime gateway."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from gateway_admin_router import create_gateway_admin_router
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


async def _run_orchestrator_chat(*args, **kwargs) -> str:
    """Run the synchronous orchestrator turn off the FastAPI event loop.

    The orchestrator keeps per-turn state on the instance, so chat turns remain
    serialized. The important part is that WebSocket progress events can flush
    while the worker thread is waiting on governance/model/tool work.
    """
    def _invoke() -> str:
        with _orchestrator_chat_lock:
            return main_orchestrator.chat(*args, **kwargs)

    return await asyncio.to_thread(_invoke)


def _get_async_chat_worker_slot() -> asyncio.Lock:
    """Return the event-loop-local worker slot for governed async chat turns."""
    global _async_chat_worker_slot_lock, _async_chat_worker_slot_loop

    loop = asyncio.get_running_loop()
    if _async_chat_worker_slot_lock is None or _async_chat_worker_slot_loop is not loop:
        _async_chat_worker_slot_lock = asyncio.Lock()
        _async_chat_worker_slot_loop = loop
    return _async_chat_worker_slot_lock


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


def _chat_run_payload(run: ChatRun) -> dict[str, Any]:
    return build_chat_run_payload(run, chat_run_store, logger, get_receipt_service)

async def _optional_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _can_access_chat_run(run: ChatRun, identity) -> bool:
    auth_method = getattr(identity, "auth_method", "")
    identity_session_id = getattr(identity, "session_id", "")
    identity_operator_id = getattr(identity, "operator_id", "")
    return bool(
        auth_method == "api_key"
        or not run.session_id
        or run.session_id == identity_session_id
        or (run.operator_id and run.operator_id == identity_operator_id)
    )


def _can_access_work_item(item: WorkItem, identity) -> bool:
    auth_method = getattr(identity, "auth_method", "")
    identity_session_id = getattr(identity, "session_id", "")
    identity_operator_id = getattr(identity, "operator_id", "")
    return bool(
        auth_method == "api_key"
        or not item.session_id
        or item.session_id == identity_session_id
        or (item.operator_id and item.operator_id == identity_operator_id)
    )


def _sync_work_ledger_from_chat_run(
    run: ChatRun | None,
    *,
    event_type: str = "chat_run_updated",
    metadata: dict[str, Any] | None = None,
) -> WorkItem | None:
    if run is None:
        return None
    try:
        item = work_ledger_store.upsert_from_chat_run(
            run,
            event_type=event_type,
            metadata=metadata,
        )
        _close_superseded_retry_source(run)
        return item
    except Exception as exc:
        logger.warning(
            "Failed to sync work ledger from chat run %s: %s",
            getattr(run, "run_id", "unknown"),
            exc,
        )
        return None


def _close_superseded_retry_source(run: ChatRun | None) -> WorkItem | None:
    if run is None:
        return None
    retry_of = str(getattr(run, "retry_of_run_id", "") or "").strip()
    status = str(getattr(run, "status", "") or "").strip().lower()
    if not retry_of or status not in _TERMINAL_CHAT_RUN_STATUSES:
        return None
    return work_ledger_store.mark_superseded_by_retry(
        retry_of,
        retry_run_id=run.run_id,
        retry_status=status,
    )


def _reconcile_superseded_retry_work(*, limit: int = 200) -> int:
    closed = 0
    try:
        retries = chat_run_store.list_terminal_retries(limit=limit)
    except Exception as exc:
        logger.warning("Failed to list terminal retries for work reconciliation: %s", exc)
        return 0

    for retry in retries:
        try:
            before = work_ledger_store.get_work(retry.retry_of_run_id)
            updated = _close_superseded_retry_source(retry)
            if (
                before is not None
                and before.status not in {"completed", "failed", "cancelled"}
                and updated is not None
                and updated.status in {"completed", "failed", "cancelled"}
            ):
                closed += 1
        except Exception as exc:
            logger.warning(
                "Failed to close superseded work %s from retry %s: %s",
                getattr(retry, "retry_of_run_id", "unknown"),
                getattr(retry, "run_id", "unknown"),
                exc,
            )
    return closed


def _archive_pending_actioncards_for_work(
    quest_id: str,
    *,
    identity,
    reason: str,
) -> list[dict[str, Any]]:
    card_store = getattr(app.state, "actioncard_store", None)
    card_resolver = getattr(app.state, "actioncard_resolver", None)
    if card_store is None or card_resolver is None:
        return []
    list_by_quest = getattr(card_store, "list_pending_by_quest", None)
    archive_card = getattr(card_resolver, "archive", None)
    if not callable(list_by_quest) or not callable(archive_card):
        return []

    archived: list[dict[str, Any]] = []
    try:
        cards = list_by_quest(quest_id, limit=50)
    except Exception as exc:
        logger.warning("Failed to list ActionCards for archived work %s: %s", quest_id, exc)
        return []

    for card in cards:
        try:
            result = archive_card(
                card.card_id,
                channel="work_archive",
                operator_id=getattr(identity, "operator_id", ""),
                session_id=getattr(identity, "session_id", ""),
                actor=getattr(identity, "display_name", "") or getattr(identity, "operator_id", ""),
                reason=f"Work item archived: {reason}",
            )
            if result.get("status") == "archived":
                archived.append({
                    "card_id": card.card_id,
                    "title": card.title,
                    "source_system": card.source_system,
                })
        except Exception as exc:
            logger.warning("Failed to archive ActionCard %s for work %s: %s", card.card_id, quest_id, exc)
    return archived


def _emit_chat_run_event(event_type: str, run: ChatRun, **extra: Any) -> None:
    try:
        from event_bus import Event, event_bus

        payload = _chat_run_payload(run)
        payload.update(extra)
        event_bus.publish_sync(Event(type=event_type, payload=payload))
    except Exception as exc:
        logger.warning("Failed to emit %s for chat run %s: %s", event_type, run.run_id, exc)


async def _record_chat_progress_event(event) -> None:
    payload = getattr(event, "payload", {}) or {}
    run_id = str(payload.get("chat_run_id") or payload.get("run_id") or payload.get("quest_id") or "")
    if not run_id:
        return
    phase = str(payload.get("phase") or "processing")
    message = str(payload.get("message") or "Processing request")
    severity = str(payload.get("severity") or "") or None
    degraded = payload.get("degraded")
    degraded_reason = str(payload.get("degraded_reason") or "") or None
    progress_metadata = {
        key: value for key, value in payload.items()
        if key not in {
            "chat_run_id",
            "run_id",
            "quest_id",
            "phase",
            "message",
            "severity",
            "degraded",
            "degraded_reason",
        }
    }
    run = _record_persisted_chat_progress(
        run_id,
        phase=phase,
        message=message,
        event_timestamp=getattr(event, "timestamp", None),
        severity=severity,
        degraded=bool(degraded) if degraded is not None else None,
        degraded_reason=degraded_reason,
        metadata=progress_metadata,
    )


def _record_persisted_chat_progress(
    run_id: str,
    *,
    phase: str,
    message: str,
    event_timestamp: float | None = None,
    severity: str | None = None,
    degraded: bool | None = None,
    degraded_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ChatRun | None:
    run = chat_run_store.record_progress(
        run_id,
        phase=phase,
        message=message,
        event_timestamp=event_timestamp,
        severity=severity,
        degraded=degraded,
        degraded_reason=degraded_reason,
        metadata=metadata,
    )
    if run is not None:
        ledger_metadata = dict(metadata or {})
        if severity:
            ledger_metadata["severity"] = severity
        if degraded is not None:
            ledger_metadata["degraded"] = bool(degraded)
        if degraded_reason:
            ledger_metadata["degraded_reason"] = degraded_reason
        _sync_work_ledger_from_chat_run(
            run,
            event_type="chat_run_progress",
            metadata=ledger_metadata,
        )
    if run is not None and run.status not in {"succeeded", "failed", "cancelled"}:
        _emit_chat_run_event("chat.run_progress", run)
    return run


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


def _classify_chat_run_status(response_text: str) -> str:
    text = str(response_text or "")
    lowered = text.lower()
    if (
        "send `continue` after approval" in lowered
        or "continue control to resume" in lowered
        or "approval id:" in lowered
        or "approval group id:" in lowered
        or "pending_approval" in lowered
        or "pending approval" in lowered
    ):
        return "blocked"
    if text.startswith("Error:") or text.startswith("Status: FAILED") or "\nStatus: FAILED" in text:
        return "failed"
    return "succeeded"


_FAST_RUNTIME_STATUS_COMMANDS = {
    "status",
    "/status",
    "system status",
    "runtime status",
    "health",
    "health check",
}
_OPERATIONAL_REPORT_TRIGGERS = (
    "operational smoke report",
    "operator acceptance smoke test",
    "operator acceptance test",
    "operational report",
    "runtime health report",
    "system health report",
    "runtime health and active work status",
    "active work status",
    "health report for this lancelot instance",
)
_OPERATOR_WORK_STATUS_TRIGGERS = (
    "active work status",
    "active-work status",
    "current active work",
    "active-work state",
    "active work state",
)
_OPERATOR_CONTINUATION_STATUS_TRIGGERS = (
    "continue with the plan",
    "continue the plan",
    "keep going with the plan",
    "resume the plan",
)
_OPERATOR_STATUS_TERMS = (
    "status",
    "what's next",
    "whats next",
    "what is next",
    "next practical step",
    "where do we stand",
    "what is left",
    "whats left",
)


def _normalized_command_text(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def _is_fast_runtime_command(message: str) -> bool:
    normalized = _normalized_command_text(message)
    if normalized in _FAST_RUNTIME_STATUS_COMMANDS:
        return True
    if _is_operator_work_status_command(normalized):
        return True
    return (
        any(trigger in normalized for trigger in _OPERATIONAL_REPORT_TRIGGERS)
        and any(term in normalized for term in ("read-only", "health", "operational", "smoke"))
    )


def _is_operator_work_status_command(normalized: str) -> bool:
    if any(trigger in normalized for trigger in _OPERATOR_WORK_STATUS_TRIGGERS):
        return True
    return (
        any(trigger in normalized for trigger in _OPERATOR_CONTINUATION_STATUS_TRIGGERS)
        and any(term in normalized for term in _OPERATOR_STATUS_TERMS)
    )


def _preview_text(value: str, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _emit_fast_runtime_receipt(
    *,
    action_name: str,
    message: str,
    response_text: str,
    checks: list[str],
    degraded_conditions: list[str],
    result: str,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> None:
    try:
        receipt_service = get_receipt_service("/home/lancelot/data")
        operator_id = getattr(identity, "operator_id", None) if identity is not None else None
        session_id = getattr(identity, "session_id", None) if identity is not None else None
        receipt = create_finalized_receipt(
            ActionType.VERIFICATION,
            action_name,
            {
                "message": _preview_text(message, limit=240),
                "checks": list(checks),
            },
            outputs={
                "result": result,
                "degraded_conditions": list(degraded_conditions),
                "response_preview": _preview_text(response_text, limit=600),
            },
            tier=CognitionTier.DETERMINISTIC,
            quest_id=quest_id,
            metadata={
                "fast_runtime_command": True,
                "channel": channel,
                "degraded": bool(degraded_conditions),
            },
            operator_id=operator_id,
            session_id=session_id,
            duration_ms=0,
        )
        receipt_service.create(receipt)
    except Exception as exc:
        logger.warning("Failed to emit fast runtime receipt %s for quest %s: %s", action_name, quest_id, exc)


def _try_handle_fast_runtime_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    """Handle exact low-risk runtime commands without a model/tool loop."""
    normalized = _normalized_command_text(message)
    if _is_operator_work_status_command(normalized):
        return _try_handle_operator_work_status_command(
            message,
            quest_id=quest_id,
            identity=identity,
            channel=channel,
        )
    if normalized not in _FAST_RUNTIME_STATUS_COMMANDS:
        return _try_handle_operational_report_command(
            message,
            quest_id=quest_id,
            identity=identity,
            channel=channel,
        )

    snapshot = health_check()
    if isinstance(snapshot, JSONResponse):
        response_text = "Runtime status unavailable. Check `/health` and gateway logs."
        _emit_fast_runtime_receipt(
            action_name="runtime_status_report",
            message=message,
            response_text=response_text,
            checks=["internal_health_snapshot"],
            degraded_conditions=["gateway health unavailable"],
            result="degraded",
            quest_id=quest_id,
            identity=identity,
            channel=channel,
        )
        return response_text
    components = snapshot.get("components", {})
    local_llm = snapshot.get("local_llm", {})
    roles = local_llm.get("roles", {}) if isinstance(local_llm, dict) else {}
    enabled_roles = [
        payload for payload in roles.values()
        if isinstance(payload, dict) and payload.get("enabled", True)
    ]
    ready_roles = [payload for payload in enabled_roles if payload.get("ready")]
    role_summary = (
        f"{len(ready_roles)}/{len(enabled_roles)} roles ready"
        if enabled_roles else "no role-specific endpoints configured"
    )
    degraded_conditions = [
        f"{component}={status}"
        for component, status in sorted(components.items())
        if status not in {"ok", "disabled"}
    ]
    degraded_conditions.extend(
        f"local_model_role:{name}={payload.get('status') or 'not ready'}"
        for name, payload in sorted(roles.items())
        if isinstance(payload, dict) and payload.get("enabled", True) and not payload.get("ready")
    )
    uptime = snapshot.get("uptime_seconds", 0)
    response_text = "\n".join([
        "**Runtime Status**",
        "---",
        f"Gateway: {components.get('gateway', 'unknown')}",
        f"Orchestrator: {components.get('orchestrator', 'unknown')}",
        f"Local model lane: {components.get('local_llm', 'unknown')} ({role_summary})",
        f"Memory: {components.get('memory', 'unknown')}",
        f"Sentry: {components.get('sentry', 'unknown')}",
        f"Uptime: {uptime}s",
    ])
    _emit_fast_runtime_receipt(
        action_name="runtime_status_report",
        message=message,
        response_text=response_text,
        checks=["internal_health_snapshot", "local_model_role_health"],
        degraded_conditions=degraded_conditions,
        result="ok" if not degraded_conditions else "degraded",
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    return response_text


def _try_handle_operator_work_status_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str:
    """Return durable operator work state without involving a model turn."""
    active_work = _active_work_report_snapshot(
        exclude_quest_id=quest_id or "",
        session_id=getattr(identity, "session_id", "") if identity is not None else "",
        operator_id=getattr(identity, "operator_id", "") if identity is not None else "",
    )
    scheduler = _scheduler_report_snapshot()
    degraded = list(active_work.get("degraded_conditions", []))
    degraded.extend(scheduler.get("degraded_conditions", []))

    failed_jobs = [
        job for job in scheduler.get("jobs", [])
        if str(job.get("last_run_status") or "").lower() == "failed"
    ]
    ticket_job = next(
        (job for job in scheduler.get("jobs", []) if job.get("id") == "ticket_sentinel_sync"),
        None,
    )

    lines = [
        "**Operator Work Status**",
        "---",
    ]
    items = active_work.get("items", [])
    if items:
        lines.append(f"Active work: {len(items)} open item(s).")
        lines.append("Open items:")
        for item in items[:5]:
            lines.append(
                f"- {item['quest_id']}: {item['status']}/{item['phase']} - {item['objective']}"
            )
            if item.get("blocker"):
                lines.append(f"  Blocked on: {item['blocker']}")
            if item.get("next_action"):
                lines.append(f"  Next: {item['next_action']}")
        if len(items) > 5:
            lines.append(f"- ... {len(items) - 5} additional items omitted")
        first = items[0]
        next_action = first.get("blocker") or first.get("next_action") or "review the first open item"
        lines.append(f"Next practical step: {next_action}.")
    else:
        lines.append("Active work: no retained active work is currently open for this operator.")
        lines.append(
            "Next practical step: start the next requested task, or name a blocked/failed run if you want it retried."
        )

    scheduler_status = scheduler.get("status", "unknown")
    lines.append(
        f"Scheduler: {scheduler_status} ({scheduler.get('job_count', 0)} jobs, "
        f"{scheduler.get('enabled_count', 0)} enabled)."
    )
    if failed_jobs:
        lines.append("Failed scheduler jobs:")
        for job in failed_jobs[:5]:
            error = f" - {_preview_text(job.get('last_run_error'), 180)}" if job.get("last_run_error") else ""
            lines.append(f"- {job['id']}: {job.get('last_run_status')}{error}")
    else:
        lines.append("Failed scheduler jobs: none.")
    if ticket_job:
        ticket_error = ticket_job.get("last_run_error")
        ticket_suffix = f", error={_preview_text(ticket_error, 180)}" if ticket_error else ""
        lines.append(
            f"Ticket Sentinel: last_status={ticket_job.get('last_run_status') or 'not run'}, "
            f"last_run={ticket_job.get('last_run_at') or 'never'}{ticket_suffix}."
        )

    if degraded:
        lines.append(f"Degraded conditions: {', '.join(degraded)}.")
    else:
        lines.append("Degraded conditions: none visible from work/scheduler state.")
    lines.append("Write safety: read-only status check; no files, deployments, or external messages were performed.")

    response_text = "\n".join(lines)
    _emit_fast_runtime_receipt(
        action_name="operator_work_status_report",
        message=message,
        response_text=response_text,
        checks=["active_work_ledger", "scheduler_service_state"],
        degraded_conditions=degraded,
        result="ok" if not degraded else "degraded",
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    return response_text


def _try_handle_operational_report_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    return handle_operational_report_command(
        message,
        normalized_command_text=_normalized_command_text,
        triggers=_OPERATIONAL_REPORT_TRIGGERS,
        health_snapshot=health_check,
        gateway_started=_gateway_started,
        operator_notice_snapshot_fn=_operator_notice_snapshot,
        active_work_report_snapshot_fn=_active_work_report_snapshot,
        scheduler_report_snapshot_fn=_scheduler_report_snapshot,
        emit_receipt=_emit_fast_runtime_receipt,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )


def _operator_notice_snapshot(degraded: list[str]) -> dict[str, list[str]]:
    return build_operator_notice_snapshot(degraded, _ff)


def _active_work_report_snapshot(
    *,
    exclude_quest_id: str = "",
    session_id: str = "",
    operator_id: str = "",
) -> dict[str, Any]:
    return build_active_work_report_snapshot(
        work_ledger_store,
        _preview_text,
        exclude_quest_id=exclude_quest_id,
        session_id=session_id,
        operator_id=operator_id,
    )


def _scheduler_report_snapshot() -> dict[str, Any]:
    return build_scheduler_report_snapshot(scheduler_service, main_orchestrator)


def _job_field(job: Any, name: str, default: Any = None) -> Any:
    return runtime_report_job_field(job, name, default)

async def _execute_chat_turn(
    message: str,
    *,
    user: str,
    channel: str,
    identity,
    attachments=None,
    quest_id: str | None = None,
) -> str:
    """Execute the existing chat semantics for sync and async callers."""
    onboarding_orch.state = onboarding_orch.determine_state()

    if onboarding_orch.state != "READY":
        return onboarding_orch.process(user, message)

    fast_response = _try_handle_fast_runtime_command(
        message,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    if fast_response is not None:
        return fast_response

    is_trigger, action = crusader_mode.should_intercept(message)
    if is_trigger:
        if action == "activate":
            ok, response_text = _transition_crusader_mode("activate")
            if ok:
                main_orchestrator.audit_logger.log_event(
                    "CRUSADER_MODE_ACTIVATED",
                    "User activated Crusader Mode",
                    user,
                )
            return response_text

        ok, response_text = _transition_crusader_mode("deactivate")
        if ok:
            main_orchestrator.audit_logger.log_event(
                "CRUSADER_MODE_DEACTIVATED",
                "User deactivated Crusader Mode",
                user,
            )
        return response_text

    operator_name = getattr(identity, "display_name", "") or user
    operator_id = getattr(identity, "operator_id", "")
    session_id = getattr(identity, "session_id", "")

    if crusader_mode.is_active:
        if crusader_adapter.check_auto_pause(message):
            main_orchestrator.audit_logger.log_event(
                "CRUSADER_AUTO_PAUSE",
                f"Blocked: {message}",
                user,
            )
            return (
                "Authority required.\n"
                "This operation is restricted even in Crusader Mode."
            )

        response_text = await _run_orchestrator_chat(
            message,
            crusader_mode=True,
            attachments=attachments,
            channel=channel,
            session_id=session_id,
            operator_id=operator_id,
            operator_name=operator_name,
            quest_id=quest_id,
        )
        return crusader_adapter.format_response(response_text)

    return await _run_orchestrator_chat(
        message,
        attachments=attachments,
        channel=channel,
        session_id=session_id,
        operator_id=operator_id,
        operator_name=operator_name,
        quest_id=quest_id,
    )


async def _execute_async_chat_run(
    run_id: str,
    *,
    message: str,
    user: str,
    channel: str,
    identity,
) -> None:
    async def _execute_in_worker_slot() -> None:
        run = chat_run_store.mark_running(run_id)
        if run is None:
            logger.warning("Async chat run %s disappeared before execution started.", run_id)
            return
        if run.status == "cancelled":
            logger.info("Async chat run %s was cancelled before execution started.", run_id)
            return
        if run.status != "running":
            logger.info(
                "Async chat run %s is no longer queued for execution (status=%s).",
                run_id,
                run.status,
            )
            return

        _sync_work_ledger_from_chat_run(run, event_type="chat_run_started")
        _emit_chat_run_event("chat.run_started", run)
        _record_persisted_chat_progress(
            run_id,
            phase="execution",
            message="Worker slot acquired; starting governed chat turn.",
            metadata={"wait_reason": "execution_start"},
        )

        try:
            response_text = await _execute_chat_turn(
                message,
                user=user,
                channel=channel,
                identity=identity,
                quest_id=run_id,
            )
            current = chat_run_store.get(run_id)
            if current is not None and current.status == "cancelled":
                logger.info("Async chat run %s finished after operator cancellation; preserving cancelled state.", run_id)
                _sync_work_ledger_from_chat_run(current, event_type="chat_run_cancelled")
                return

            status = _classify_chat_run_status(response_text)
            if status == "blocked":
                _record_persisted_chat_progress(
                    run_id,
                    phase="approval",
                    message="Waiting for Commander approval.",
                    metadata={"wait_reason": "approval"},
                )
            else:
                _record_persisted_chat_progress(
                    run_id,
                    phase="finalization",
                    message="Finalizing response and execution proof.",
                    metadata={"wait_reason": "finalization"},
                )

            run = chat_run_store.complete(
                run_id,
                status=status,
                response=response_text,
                crusader_mode=crusader_mode.is_active,
            )
            if run is not None:
                event_name = "chat.run_blocked" if status == "blocked" else (
                    "chat.run_failed" if status == "failed" else "chat.run_completed"
                )
                _sync_work_ledger_from_chat_run(run, event_type=event_name.replace(".", "_"))
                _emit_chat_run_event(event_name, run)
        except Exception as exc:
            logger.exception("Async chat run failed: %s", run_id)
            run = chat_run_store.fail(run_id, str(exc))
            if run is not None:
                _sync_work_ledger_from_chat_run(run, event_type="chat_run_failed")
                _emit_chat_run_event("chat.run_failed", run)

    if _is_fast_runtime_command(message):
        await _execute_in_worker_slot()
        return

    queued = _record_persisted_chat_progress(
        run_id,
        phase="waiting_worker_slot",
        message="Waiting for governed execution worker slot.",
        metadata={"wait_reason": "worker_slot"},
    )
    if queued is None:
        logger.warning("Async chat run %s disappeared before worker-slot wait was recorded.", run_id)
        return
    if queued.status == "cancelled":
        logger.info("Async chat run %s was cancelled before worker-slot acquisition.", run_id)
        return

    async with _get_async_chat_worker_slot():
        current = chat_run_store.get(run_id)
        if current is not None and current.status == "cancelled":
            logger.info("Async chat run %s was cancelled while waiting for worker slot.", run_id)
            _sync_work_ledger_from_chat_run(current, event_type="chat_run_cancelled")
            return
        await _execute_in_worker_slot()


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


@app.post("/api/warroom/client-error")
async def warroom_client_error(request: Request):
    """Record War Room browser-side failures for operator debugging."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    def _short(value: Any, limit: int = 2000) -> str:
        text = str(value or "")
        return text[:limit]

    logger.error(
        "warroom_client_error kind=%s href=%s ua=%s message=%s stack=%s",
        _short(payload.get("kind"), 80),
        _short(payload.get("href"), 300),
        _short(payload.get("user_agent"), 500),
        _short(payload.get("message"), 1000),
        _short(payload.get("stack"), 3000),
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
