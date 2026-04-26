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

# F1: Configurable log level
LOG_LEVEL = os.getenv("LANCELOT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger("lancelot.gateway")

# S11: Request size limit (20 MB for file uploads)
MAX_REQUEST_SIZE = 20_971_520

# F8: Startup timestamp for uptime tracking
_startup_time = None
_gateway_started = False

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

# --- Vault-Backed Secret Cache ---
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
    logger.warning("Vault bootstrap failed â€” falling back to os.getenv(): %s", _vault_exc)

# --- API Authentication ---
API_TOKEN = secret_cache.get("LANCELOT_API_TOKEN") if secret_cache.is_bootstrapped() else os.getenv("LANCELOT_API_TOKEN")
DEV_MODE = os.getenv("LANCELOT_DEV_MODE", "").lower() in ("true", "1", "yes")




# S11: Rate limiter instance
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
    payload = run.to_dict()
    payload["run_id"] = run.run_id
    payload["receipt_proof"] = _build_chat_run_receipt_proof(run)
    return payload


_TERMINAL_CHAT_RUN_STATUSES = {"blocked", "succeeded", "failed", "cancelled"}
_TOOL_RECEIPT_TYPES = {"tool_call", "mcp_tool_call"}
_APPROVAL_GRANTED_TYPES = {"t3_approved", "mcp_t3_approved", "apl_rule_approved"}
_DEGRADED_VERIFICATION_ACTIONS = {"pii_scrub_fallback"}


def _chat_run_lineage_ids(run: ChatRun) -> list[str]:
    lineage: list[str] = []
    seen: set[str] = set()
    current: ChatRun | None = run

    while current is not None and current.run_id:
        if current.run_id in seen:
            break
        seen.add(current.run_id)
        lineage.append(current.run_id)

        retry_of = str(current.retry_of_run_id or "").strip()
        if not retry_of:
            break
        current = chat_run_store.get(retry_of)

    lineage.reverse()
    return lineage


def _load_chat_run_receipts(run: ChatRun) -> tuple[list[Any], list[str], str | None]:
    try:
        receipt_service = get_receipt_service("/home/lancelot/data")
        lineage = _chat_run_lineage_ids(run)
        receipts: list[Any] = []
        seen_receipt_ids: set[str] = set()

        for quest_id in lineage:
            for receipt in receipt_service.get_quest_receipts(quest_id):
                receipt_id = str(getattr(receipt, "id", "") or "")
                if receipt_id and receipt_id in seen_receipt_ids:
                    continue
                if receipt_id:
                    seen_receipt_ids.add(receipt_id)
                receipts.append(receipt)

        return receipts, lineage, None
    except Exception as exc:
        logger.warning("Failed to load receipt proof for chat run %s: %s", run.run_id, exc)
        return [], [], str(exc)


def _build_chat_run_receipt_proof(run: ChatRun) -> dict[str, Any] | None:
    if run.status not in _TERMINAL_CHAT_RUN_STATUSES:
        return None

    receipts, lineage, error = _load_chat_run_receipts(run)
    if error:
        return {
            "available": False,
            "receipt_count": 0,
            "linked_run_count": max(1, len(lineage)),
            "governed_tools": [],
            "approval_state": "unknown",
            "degraded_mode": "unknown",
            "degraded_reasons": [],
            "outcome": run.status,
            "error": error,
        }

    governed_tools: list[str] = []
    seen_tools: set[str] = set()
    approval_state = "not_used"
    degraded_mode = "not_used"
    degraded_reasons: list[str] = []

    for receipt in receipts:
        action_type = str(getattr(receipt, "action_type", "") or "")
        action_name = str(getattr(receipt, "action_name", "") or "")
        receipt_status = str(getattr(receipt, "status", "") or "")
        metadata = getattr(receipt, "metadata", None) or {}
        outputs = getattr(receipt, "outputs", None) or {}
        error_message = str(getattr(receipt, "error_message", "") or "")

        if action_type in _TOOL_RECEIPT_TYPES:
            tool_name = str(metadata.get("tool_name") or action_name or "").strip()
            if tool_name and tool_name not in seen_tools:
                seen_tools.add(tool_name)
                governed_tools.append(tool_name)
            if approval_state != "used" and (
                receipt_status == "pending" or metadata.get("approval_id")
            ):
                approval_state = "required"

        if action_type in _APPROVAL_GRANTED_TYPES:
            approval_state = "used"
        elif action_type == "action_card_resolved":
            resolved_status = str(outputs.get("status") or "").lower()
            if action_name.endswith(".approve") or resolved_status == "approved":
                approval_state = "used"

        degraded_flag = bool(metadata.get("degraded_privacy")) or bool(outputs.get("fallback_used"))
        if action_type == "verification" and (
            degraded_flag or action_name in _DEGRADED_VERIFICATION_ACTIONS
        ):
            degraded_mode = "used"
            reason = (
                str(metadata.get("reason") or "").strip()
                or str(outputs.get("reason") or "").strip()
                or error_message.strip()
                or action_name
            )
            if reason and reason not in degraded_reasons:
                degraded_reasons.append(reason)

    if approval_state == "not_used" and run.status == "blocked":
        approval_state = "required"

    return {
        "available": True,
        "receipt_count": len(receipts),
        "linked_run_count": max(1, len(lineage)),
        "governed_tools": governed_tools,
        "approval_state": approval_state,
        "degraded_mode": degraded_mode,
        "degraded_reasons": degraded_reasons,
        "outcome": run.status,
    }


async def _optional_json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _can_access_chat_run(run: ChatRun, identity) -> bool:
    auth_method = getattr(identity, "auth_method", "")
    identity_session_id = getattr(identity, "session_id", "")
    return bool(auth_method == "api_key" or not run.session_id or run.session_id == identity_session_id)


def _can_access_work_item(item: WorkItem, identity) -> bool:
    auth_method = getattr(identity, "auth_method", "")
    identity_session_id = getattr(identity, "session_id", "")
    return bool(auth_method == "api_key" or not item.session_id or item.session_id == identity_session_id)


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


def _summarize_local_model_role_lane(roles: dict[str, Any]) -> dict[str, Any]:
    enabled_roles = [
        payload for payload in (roles or {}).values()
        if isinstance(payload, dict) and payload.get("enabled", True)
    ]
    if not enabled_roles:
        return {"ready": False, "loaded": False, "status": "unavailable"}

    ready = all(bool(role.get("ready")) for role in enabled_roles)
    loaded = any(bool(role.get("loaded", role.get("ready"))) for role in enabled_roles)
    failed = [
        role for role in enabled_roles
        if not role.get("ready") and role.get("last_error")
    ]
    verified = [
        str(role.get("last_verified_at"))
        for role in enabled_roles
        if role.get("last_verified_at")
    ]
    checked = [
        str(role.get("last_checked_at"))
        for role in enabled_roles
        if role.get("last_checked_at")
    ]
    smoke_times = [
        float(role.get("last_smoke_elapsed_ms"))
        for role in enabled_roles
        if role.get("last_smoke_elapsed_ms") is not None
    ]
    return {
        "ready": ready,
        "loaded": loaded,
        "status": "ok" if ready else ("degraded" if loaded else "unavailable"),
        "last_error": "; ".join(str(role.get("last_error")) for role in failed) or None,
        "last_verified_at": max(verified) if verified else None,
        "last_checked_at": max(checked) if checked else None,
        "last_smoke_elapsed_ms": max(smoke_times) if smoke_times else None,
    }


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


def _normalized_command_text(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def _is_fast_runtime_command(message: str) -> bool:
    normalized = _normalized_command_text(message)
    if normalized in _FAST_RUNTIME_STATUS_COMMANDS:
        return True
    return (
        any(trigger in normalized for trigger in _OPERATIONAL_REPORT_TRIGGERS)
        and any(term in normalized for term in ("read-only", "health", "operational", "smoke"))
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


def _try_handle_operational_report_command(
    message: str,
    *,
    quest_id: str | None = None,
    identity=None,
    channel: str = "warroom",
) -> str | None:
    normalized = _normalized_command_text(message)
    if not any(trigger in normalized for trigger in _OPERATIONAL_REPORT_TRIGGERS):
        return None
    if not any(term in normalized for term in ("read-only", "health", "operational", "smoke")):
        return None

    snapshot = health_check()
    if isinstance(snapshot, JSONResponse):
        diagnostic_source = (
            "standalone gateway import; use GET /api/operator/smoke for live gateway status"
            if not _gateway_started
            else "live gateway process"
        )
        response_text = (
            "**Operational Smoke Report**\n"
            "---\n"
            "Result: degraded\n"
            f"Diagnostic source: {diagnostic_source}\n"
            "Checked: internal health snapshot\n"
            "Gateway health could not be assembled. Check `/health` and gateway logs.\n"
            "No repository writes, deployments, or external messages were performed."
        )
        _emit_fast_runtime_receipt(
            action_name="operational_smoke_report",
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

    components = snapshot.get("components", {}) if isinstance(snapshot, dict) else {}
    local_llm = snapshot.get("local_llm", {}) if isinstance(snapshot, dict) else {}
    roles = local_llm.get("roles", {}) if isinstance(local_llm, dict) else {}
    enabled_roles = [
        (name, payload)
        for name, payload in sorted((roles or {}).items())
        if isinstance(payload, dict) and payload.get("enabled", True)
    ]
    ready_roles = [
        name for name, payload in enabled_roles
        if payload.get("ready")
    ]
    scheduler = _scheduler_report_snapshot()
    active_work = _active_work_report_snapshot()

    degraded: list[str] = []
    for component, status in sorted(components.items()):
        if status not in {"ok", "disabled"}:
            degraded.append(f"{component}={status}")
    for name, payload in enabled_roles:
        if not payload.get("ready"):
            status = payload.get("status") or "not ready"
            degraded.append(f"local_model_role:{name}={status}")
    if scheduler["status"] not in {"running", "initialized"}:
        degraded.append(f"scheduler={scheduler['status']}")
    degraded.extend(scheduler["degraded_conditions"])
    degraded.extend(active_work["degraded_conditions"])

    result = "ok" if not degraded else "degraded"
    local_role_summary = (
        f"{len(ready_roles)}/{len(enabled_roles)} roles ready"
        if enabled_roles else "no role-specific endpoints configured"
    )
    scheduler_summary = (
        f"{scheduler['status']} "
        f"({scheduler['job_count']} jobs, {scheduler['enabled_count']} enabled, "
        f"last tick: {scheduler['last_tick'] or 'not observed'})"
    )

    lines = [
        "**Operational Smoke Report**",
        "---",
        f"Result: {result}",
        (
            "Diagnostic source: live gateway process"
            if _gateway_started
            else "Diagnostic source: standalone gateway import; use GET /api/operator/smoke for live gateway status"
        ),
        "Checked:",
        "- Gateway health snapshot via internal `/health` handler",
        "- Local model role health from the role router status",
        "- Scheduler service state and registered job records",
        "- Active work ledger state",
        "- Visible degraded conditions from component, role, scheduler, and active-work state",
        "",
        "Findings:",
        f"- Gateway: {components.get('gateway', 'unknown')}",
        f"- Orchestrator: {components.get('orchestrator', 'unknown')}",
        f"- Memory: {components.get('memory', 'unknown')}",
        f"- Sentry: {components.get('sentry', 'unknown')}",
        f"- Local model lane: {components.get('local_llm', 'unknown')} ({local_role_summary})",
        f"- Scheduler: {scheduler_summary}",
        f"- Active work: {active_work['status']} ({active_work['count']} open item(s))",
    ]

    if enabled_roles:
        lines.append("- Local model roles:")
        for name, payload in enabled_roles:
            smoke_ms = payload.get("last_smoke_elapsed_ms")
            smoke_text = f", smoke {smoke_ms}ms" if smoke_ms is not None else ""
            error = payload.get("last_error")
            error_text = f", error: {error}" if error else ""
            status = payload.get("status") or ("ready" if payload.get("ready") else "unknown")
            lines.append(
                f"  - {name}: {status}, ready={bool(payload.get('ready'))}{smoke_text}{error_text}"
            )

    if scheduler["jobs"]:
        lines.append("- Scheduler jobs:")
        for job in scheduler["jobs"][:8]:
            last_run = job["last_run_at"] or "never"
            last_status = job["last_run_status"] or "not run"
            enabled = "enabled" if job["enabled"] else "disabled"
            lines.append(
                f"  - {job['id']}: {enabled}, trigger={job['trigger']}, "
                f"last_run={last_run}, last_status={last_status}"
            )
        if len(scheduler["jobs"]) > 8:
            lines.append(f"  - ... {len(scheduler['jobs']) - 8} additional jobs omitted")

    if active_work["items"]:
        lines.append("- Active work items:")
        for item in active_work["items"][:5]:
            lines.append(
                f"  - {item['quest_id']}: {item['status']}/{item['phase']} - {item['objective']}"
            )
        if len(active_work["items"]) > 5:
            lines.append(f"  - ... {len(active_work['items']) - 5} additional items omitted")

    if degraded:
        lines.append(f"- Degraded conditions: {', '.join(degraded)}")
    else:
        lines.append("- Degraded conditions: none visible from these checks")

    notices = _operator_notice_snapshot(degraded)
    if notices["action_required"]:
        lines.append("- Operator action required:")
        for notice in notices["action_required"]:
            lines.append(f"  - {notice}")
    else:
        lines.append("- Operator action required: none from these checks")
    if notices["expected"]:
        lines.append("- Expected operator notices:")
        for notice in notices["expected"]:
            lines.append(f"  - {notice}")

    lines.append("- Write safety: no repository writes, deployments, or external messages were performed")
    uptime = snapshot.get("uptime_seconds", 0) if isinstance(snapshot, dict) else 0
    lines.append(f"- Uptime: {uptime}s")
    response_text = "\n".join(lines)
    _emit_fast_runtime_receipt(
        action_name="operational_smoke_report",
        message=message,
        response_text=response_text,
        checks=[
            "internal_health_snapshot",
            "local_model_role_health",
            "scheduler_service_state",
            "registered_scheduler_jobs",
            "active_work_ledger",
        ],
        degraded_conditions=degraded,
        result=result,
        quest_id=quest_id,
        identity=identity,
        channel=channel,
    )
    return response_text


def _operator_notice_snapshot(degraded: list[str]) -> dict[str, list[str]]:
    action_required = [
        f"{condition}. Investigate before continuing customer-facing work."
        for condition in degraded
    ]
    expected: list[str] = []
    if getattr(_ff, "FEATURE_TOOLS_HOST_EXECUTION", False):
        expected.append(
            "Host execution provider is enabled for this local operator instance; commands run in container Linux."
        )
    if getattr(_ff, "FEATURE_TOOLS_HOST_BRIDGE", False):
        expected.append(
            "Host bridge provider is enabled; commands can cross from the container to the host agent."
        )
    if getattr(_ff, "FEATURE_HOST_WRITE_COMMANDS", False):
        expected.append(
            "Host write commands are enabled; keep this off for customer deployments unless explicitly required."
        )
    if getattr(_ff, "FEATURE_TOOLS_UAB", False) or getattr(_ff, "FEATURE_HIVE_UAB", False):
        expected.append(
            "UAB desktop bridge is enabled; verify the daemon before desktop-control workflows."
        )
    return {
        "action_required": action_required,
        "expected": expected,
    }


def _active_work_report_snapshot() -> dict[str, Any]:
    store = work_ledger_store
    if store is None:
        return {
            "status": "unavailable",
            "count": 0,
            "items": [],
            "degraded_conditions": ["active work ledger is not initialized"],
        }

    try:
        items = store.list_work(include_terminal=False, limit=10)
    except Exception as exc:
        return {
            "status": "error",
            "count": 0,
            "items": [],
            "degraded_conditions": [f"active work list failed: {exc}"],
        }

    payloads = []
    for item in items:
        payloads.append({
            "quest_id": str(getattr(item, "quest_id", ""))[:80],
            "status": str(getattr(item, "status", ""))[:40],
            "phase": str(getattr(item, "phase", ""))[:40],
            "objective": _preview_text(str(getattr(item, "objective", "")), limit=140),
        })

    return {
        "status": "ok",
        "count": len(payloads),
        "items": payloads,
        "degraded_conditions": [],
    }


def _scheduler_report_snapshot() -> dict[str, Any]:
    service = scheduler_service or getattr(main_orchestrator, "scheduler_service", None)
    if service is None:
        return {
            "status": "unavailable",
            "last_tick": None,
            "job_count": 0,
            "enabled_count": 0,
            "jobs": [],
            "degraded_conditions": ["scheduler service is not initialized"],
        }

    try:
        jobs = service.list_jobs()
    except Exception as exc:
        return {
            "status": "error",
            "last_tick": getattr(service, "last_scheduler_tick_at", None),
            "job_count": 0,
            "enabled_count": 0,
            "jobs": [],
            "degraded_conditions": [f"scheduler job list failed: {exc}"],
        }

    job_payloads = []
    degraded_conditions = []
    for job in jobs:
        job_id = str(_job_field(job, "id", "unknown"))
        enabled = bool(_job_field(job, "enabled", False))
        trigger_type = str(_job_field(job, "trigger_type", "unknown"))
        trigger_value = str(_job_field(job, "trigger_value", ""))
        last_status = _job_field(job, "last_run_status", None)
        if last_status and str(last_status).lower() not in {
            "ok",
            "success",
            "succeeded",
            "triggered",
            "not run",
        }:
            degraded_conditions.append(f"job:{job_id} last_status={last_status}")
        job_payloads.append({
            "id": job_id,
            "enabled": enabled,
            "trigger": f"{trigger_type}:{trigger_value}" if trigger_value else trigger_type,
            "last_run_at": _job_field(job, "last_run_at", None),
            "last_run_status": last_status,
        })

    last_tick = getattr(service, "last_scheduler_tick_at", None)
    return {
        "status": "running" if last_tick else "initialized",
        "last_tick": last_tick,
        "job_count": len(job_payloads),
        "enabled_count": sum(1 for job in job_payloads if job["enabled"]),
        "jobs": job_payloads,
        "degraded_conditions": degraded_conditions,
    }


def _job_field(job: Any, name: str, default: Any = None) -> Any:
    if isinstance(job, dict):
        return job.get(name, default)
    return getattr(job, name, default)


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
    onboarding_orch.state = onboarding_orch._determine_state()

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


@app.post("/chat/async")
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


@app.get("/api/chat/runs")
async def list_chat_runs(request: Request, limit: int = 25):
    """Return recent async chat runs for the authenticated session."""
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        safe_limit = max(1, min(int(limit), 100))
        runs = chat_run_store.list_recent(
            limit=safe_limit,
            session_id=getattr(identity, "session_id", ""),
        )
        return {"runs": [_chat_run_payload(run) for run in runs], "count": len(runs)}
    except Exception as e:
        logger.error("Async chat run list failed: %s", e)
        return error_response(500, "Internal server error")


@app.get("/api/chat/runs/{run_id}")
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


@app.post("/api/chat/runs/{run_id}/cancel")
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


@app.post("/api/chat/runs/{run_id}/retry")
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


@app.get("/api/work/active")
async def list_active_work(request: Request, limit: int = 25):
    """Return active work ledger items for the authenticated operator session."""
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    try:
        from src.core.auth_api import resolve_authenticated_identity

        identity = resolve_authenticated_identity(request)
        session_id = "" if getattr(identity, "auth_method", "") == "api_key" else getattr(identity, "session_id", "")
        safe_limit = max(1, min(int(limit), 100))
        work_ledger_store.checkpoint_quiet_work(
            max_quiet_seconds=ACTIVE_WORK_QUIET_CHECKPOINT_AFTER_SECONDS,
            reason="quiet_phase",
            session_id=session_id,
            limit=safe_limit,
        )
        items = work_ledger_store.list_work(session_id=session_id, include_terminal=False, limit=safe_limit)
        return {
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }
    except Exception as e:
        logger.error("Active work list failed: %s", e)
        return error_response(500, "Internal server error")


@app.get("/api/work/{quest_id}")
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


@app.post("/api/work/{quest_id}/checkpoint")
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


@app.post("/api/work/{quest_id}/resume")
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


@app.post("/api/work/{quest_id}/archive")
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


@app.get("/api/operator/smoke")
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


# â”€â”€ Secret Rotation Endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        local_llm_roles = {}
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
        try:
            local_roles_router = getattr(main_orchestrator, "local_model_roles", None)
            if local_roles_router is not None:
                from src.core.model_usage_policy import set_local_model_roles_status

                raw_roles = local_roles_router.status()
                set_local_model_roles_status(raw_roles)
            else:
                from src.core.model_usage_policy import get_model_usage_status

                raw_roles = get_model_usage_status().get("local_model_roles", {}) or {}
            if isinstance(raw_roles, dict) and isinstance(raw_roles.get("roles"), dict):
                local_llm_roles = raw_roles["roles"]
            elif isinstance(raw_roles, dict):
                local_llm_roles = raw_roles
        except Exception as exc:
            logger.debug("Local model role status unavailable during health check: %s", exc)
        role_lane = _summarize_local_model_role_lane(local_llm_roles)
        if not local_llm_ready and role_lane.get("ready"):
            local_llm_ready = True
            local_llm_loaded = True
            local_llm_status = "ok"
            local_llm_last_error = None
            local_llm_last_verified_at = role_lane.get("last_verified_at")
            local_llm_last_checked_at = role_lane.get("last_checked_at")
            local_llm_last_smoke_elapsed_ms = role_lane.get("last_smoke_elapsed_ms")
        elif not local_llm_ready and role_lane.get("loaded"):
            local_llm_loaded = True
            local_llm_status = "degraded"
            local_llm_last_error = role_lane.get("last_error") or local_llm_last_error
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
                "roles": local_llm_roles,
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
