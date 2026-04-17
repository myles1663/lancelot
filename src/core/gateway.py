import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, File, UploadFile, Form
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from onboarding import OnboardingOrchestrator
from orchestrator import LancelotOrchestrator
# [NEW] Production Modules
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
import hmac
import time
import uuid
import os
import json
import logging
from oauth_callback_pages import render_callback_exception_page, render_callback_page
from src.core.runtime_pause import init_runtime_pause

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


# F2: Structured error response helper
def error_response(status_code: int, message: str, detail: str = None, request_id: str = None) -> JSONResponse:
    content = {"error": message, "status": status_code}
    if detail:
        content["detail"] = detail
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=content)


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


class RateLimiter:
    """S11: Sliding-window rate limiter per IP address.

    F-012: Includes periodic cleanup of stale IP entries to prevent
    unbounded memory growth.
    """

    _CLEANUP_INTERVAL_S = 300  # Clean up every 5 minutes

    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = {}  # ip -> [timestamps]
        self._last_cleanup = time.time()

    def check(self, ip: str) -> bool:
        """Returns True if request is allowed."""
        now = time.time()
        # F-012: Periodic stale IP cleanup
        if now - self._last_cleanup > self._CLEANUP_INTERVAL_S:
            self._cleanup_stale(now)
        if ip not in self._requests:
            self._requests[ip] = []
        self._requests[ip] = [t for t in self._requests[ip] if t > now - self.window]
        if len(self._requests[ip]) >= self.max_requests:
            return False
        self._requests[ip].append(now)
        return True

    def _cleanup_stale(self, now: float) -> None:
        """Remove IPs with no recent requests to prevent memory growth."""
        stale_ips = [
            ip for ip, timestamps in self._requests.items()
            if not timestamps or all(t <= now - self.window for t in timestamps)
        ]
        for ip in stale_ips:
            del self._requests[ip]
        self._last_cleanup = now
        if stale_ips:
            logger.debug("Rate limiter: cleaned %d stale IP entries", len(stale_ips))


app = FastAPI()

# S11: CORS middleware — explicit methods/headers (F-004 hardening)
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

# --- Subsystem Gate Middleware ---
# Routes for feature-gated subsystems are always mounted but gated here.
# When a subsystem's flag is OFF, its routes return 503 instead of crashing.
import feature_flags as _ff

_SUBSYSTEM_GATES = [
    ("/memory", "FEATURE_MEMORY_VNEXT"),
    ("/soul", "FEATURE_SOUL"),
    ("/api/scheduler", "FEATURE_SCHEDULER"),
    ("/api/v1/clients", "FEATURE_BAL"),
    ("/api/hive", "FEATURE_HIVE"),
    ("/api/federation", "FEATURE_FEDERATION"),
    ("/api/mcp", "FEATURE_MCP"),
    ("/api/observability", "FEATURE_OBSERVABILITY"),
    ("/api/metrics", "FEATURE_OBSERVABILITY"),
    ("/api/timetravel", "FEATURE_TIME_TRAVEL"),
    ("/api/a2a", "FEATURE_A2A"),
    ("/a2a", "FEATURE_A2A"),
    ("/.well-known/agent.json", "FEATURE_A2A"),
    ("/api/incidents", "FEATURE_INCIDENT_RESPONSE"),
    ("/api/playbooks", "FEATURE_INCIDENT_RESPONSE"),
    ("/api/actioncards", "FEATURE_ACTION_CARDS"),
]


@app.middleware("http")
async def subsystem_gate_middleware(request: Request, call_next):
    path = request.url.path
    for prefix, flag_name in _SUBSYSTEM_GATES:
        if path == prefix or path.startswith(prefix + "/"):
            if not getattr(_ff, flag_name, False):
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "subsystem_disabled",
                        "flag": flag_name,
                        "message": f"Enable {flag_name} to use this endpoint",
                    },
                )
    return await call_next(request)

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
        # Phase 3: Scrub vault key itself from environ — closes last /proc exposure.
        # Safe because _boot_vault already holds the cipher in memory.
        if "LANCELOT_VAULT_KEY" in os.environ:
            del os.environ["LANCELOT_VAULT_KEY"]
            logger.info("LANCELOT_VAULT_KEY scrubbed from os.environ (vault cipher in memory).")
        logger.info("Vault-backed secret cache initialized (key_source=%s).",
                     getattr(_boot_vault, 'key_source', 'unknown'))
except Exception as _vault_exc:
    logger.warning("Vault bootstrap failed — falling back to os.getenv(): %s", _vault_exc)

# --- API Authentication ---
API_TOKEN = secret_cache.get("LANCELOT_API_TOKEN") if secret_cache.is_bootstrapped() else os.getenv("LANCELOT_API_TOKEN")
DEV_MODE = os.getenv("LANCELOT_DEV_MODE", "").lower() in ("true", "1", "yes")


def verify_token(request: Request) -> bool:
    """Validates Bearer token from Authorization header.

    Security: When LANCELOT_API_TOKEN is not set, authentication is only
    bypassed if LANCELOT_DEV_MODE is explicitly enabled. Otherwise, all
    requests are rejected (fail-closed).

    Also accepts War Room session tokens as a fallback.
    """
    if not API_TOKEN:
        if DEV_MODE:
            logger.warning(
                "SECURITY: Gateway running in dev mode (LANCELOT_DEV_MODE=true) — "
                "all requests accepted without authentication."
            )
            return True
        # Fall back to War Room session check
        try:
            from src.core.auth_api import verify_warroom_session
            if verify_warroom_session(request):
                return True
        except ImportError:
            pass
        logger.error(
            "SECURITY: No LANCELOT_API_TOKEN configured and dev mode not enabled. "
            "Set LANCELOT_API_TOKEN for production or LANCELOT_DEV_MODE=true for development."
        )
        return False
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        if hmac.compare_digest(auth_header[7:], API_TOKEN):
            return True
    # Fall back to War Room session check
    try:
        from src.core.auth_api import verify_warroom_session
        return verify_warroom_session(request)
    except ImportError:
        return False


def _require_request_capability(
    request: Request,
    capability: str,
    *,
    request_id: str | None = None,
) -> JSONResponse | None:
    """Require authenticated access plus a coarse operator capability."""
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        from src.core.auth_api import request_has_capability

        if request_has_capability(request, capability):
            return None
    except Exception as exc:
        logger.warning("Capability enforcement failed for %s: %s", capability, exc)
        return error_response(503, "Authorization unavailable", request_id=request_id)

    return error_response(403, f"Missing capability: {capability}", request_id=request_id)


# F7: Generate unique request ID
def make_request_id() -> str:
    return str(uuid.uuid4())


# S11: Rate limiter instance
rate_limiter = RateLimiter()

# [NEW] Initialize Production Modules
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


# ---------------------------------------------------------------------------
# Subsystem init / shutdown functions (called by SubsystemManager)
# ---------------------------------------------------------------------------
from subsystem_manager import subsystem_manager


def _init_memory():
    """Initialize Memory vNext subsystem."""
    from memory.store import CoreBlockStore
    from memory.sqlite_store import MemoryStoreManager
    from memory.compiler import ContextCompilerService

    mem_data_dir = Path("/home/lancelot/data")
    core_store = CoreBlockStore(data_dir=mem_data_dir)
    core_store.initialize()
    user_md = mem_data_dir / "USER.md"
    if user_md.exists():
        core_store.bootstrap_from_user_file(str(user_md))

    store_manager = MemoryStoreManager(data_dir=mem_data_dir)
    compiler_svc = ContextCompilerService(
        data_dir=mem_data_dir,
        core_store=core_store,
        memory_manager=store_manager,
    )
    main_orchestrator._memory_enabled = True
    main_orchestrator.context_compiler = compiler_svc
    logger.info("Memory vNext initialized and wired.")
    return {"core_store": core_store, "store_manager": store_manager, "compiler": compiler_svc}


def _shutdown_memory(objects):
    """Shut down Memory vNext subsystem."""
    main_orchestrator._memory_enabled = False
    main_orchestrator.context_compiler = None
    logger.info("Memory vNext shut down.")


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


def _init_soul():
    """Initialize Soul subsystem."""
    from soul.store import load_active_soul, SoulStoreError
    from soul.api import router as soul_router

    active_soul = load_active_soul()
    if active_soul is None:
        logger.warning("No active soul found — Soul subsystem starting without a soul document")
        main_orchestrator.soul = None
        return {"soul": None}

    # Apply composable soul overlays if BAL is enabled
    try:
        from feature_flags import FEATURE_BAL
        if FEATURE_BAL:
            from soul.layers import load_overlays, merge_soul
            overlays = load_overlays()
            if overlays:
                active_soul = merge_soul(active_soul, overlays)
                logger.info("Soul overlays applied: %s", [o.overlay_name for o in overlays])
    except Exception as exc:
        logger.warning("Soul overlay loading failed: %s — using base soul", exc)

    main_orchestrator.soul = active_soul
    if getattr(main_orchestrator, "_risk_classifier", None) is not None:
        try:
            main_orchestrator._risk_classifier.update_soul(active_soul)
        except Exception as exc:
            logger.warning("Risk classifier Soul update failed during Soul init: %s", exc)
    logger.info("Soul loaded: version=%s", active_soul.version)
    return {"soul": active_soul}


def _shutdown_soul(objects):
    """Shut down Soul subsystem."""
    main_orchestrator.soul = None
    logger.info("Soul shut down.")


def _init_skills():
    """Initialize Skills subsystem."""
    from skills.registry import SkillRegistry, SkillEntry, SkillOwnership
    from skills.executor import SkillExecutor
    from skills.factory import SkillFactory

    skill_registry = SkillRegistry(data_dir="/home/lancelot/data")
    for builtin_name in ("echo", "command_runner", "repo_writer", "service_runner",
                         "network_client", "telegram_send", "warroom_send", "schedule_job",
                         "health_check", "document_creator", "skill_manager"):
        if not skill_registry.get_skill(builtin_name):
            skill_registry._skills[builtin_name] = SkillEntry(
                name=builtin_name, version="1.0.0",
                enabled=True, ownership=SkillOwnership.SYSTEM,
            )
    skill_registry._save()

    executor = SkillExecutor(registry=skill_registry)
    skill_factory = SkillFactory(data_dir="/home/lancelot/data")

    main_orchestrator.skill_executor = executor
    main_orchestrator.skill_factory = skill_factory
    main_orchestrator.skill_registry = skill_registry
    if main_orchestrator.task_runner:
        main_orchestrator.task_runner.skill_executor = executor
    logger.info("Skills initialized: %d skills (factory enabled)", len(skill_registry.list_skills()))
    return {"registry": skill_registry, "executor": executor, "factory": skill_factory}


def _shutdown_skills(objects):
    """Shut down Skills subsystem."""
    main_orchestrator.skill_executor = None
    if main_orchestrator.task_runner:
        main_orchestrator.task_runner.skill_executor = None
    logger.info("Skills shut down.")


def _init_scheduler():
    """Initialize Scheduler subsystem."""
    global scheduler_service
    from scheduler.service import SchedulerService

    service = SchedulerService(
        data_dir="/home/lancelot/data/scheduler",
        config_dir="config",
    )
    scheduler_service = service
    count = service.register_from_config()
    main_orchestrator.scheduler_service = service
    logger.info("Scheduler initialized: %d jobs registered", count)

    memory_job_executor = None
    MEMORY_JOB_SKILLS = set()
    execute_memory_job = None
    skill_executor = main_orchestrator.skill_executor
    memory_jobs_inserted = 0
    memory_jobs_updated = 0

    if getattr(main_orchestrator, "_memory_enabled", False) and main_orchestrator.context_compiler:
        try:
            from memory.jobs import (
                MEMORY_JOB_SKILLS,
                MemoryJobExecutor,
                execute_memory_job,
                get_memory_job_specs,
            )

            compiler_svc = main_orchestrator.context_compiler
            memory_job_executor = MemoryJobExecutor(
                core_store=compiler_svc.core_store,
                store_manager=compiler_svc.memory_manager,
                data_dir=Path("/home/lancelot/data"),
            )
            memory_jobs_inserted, memory_jobs_updated = service.ensure_job_specs(get_memory_job_specs())
            logger.info(
                "Memory scheduler jobs ensured: %d inserted, %d updated",
                memory_jobs_inserted,
                memory_jobs_updated,
            )
        except Exception as e:
            logger.error("Memory scheduler job initialization failed: %s", e)
            memory_job_executor = None

    # Connect job executor if skills or memory jobs are available
    job_exec = None
    if skill_executor or memory_job_executor:
        from scheduler.executor import JobExecutor

        def _execute_scheduled_job(name, inputs):
            if memory_job_executor and name in MEMORY_JOB_SKILLS:
                result = execute_memory_job(memory_job_executor, name, inputs)
                if not result.success:
                    raise RuntimeError("; ".join(result.errors) or f"Memory job {name} failed")
                return result.to_dict()
            if skill_executor:
                return skill_executor.run(name, inputs)
            raise RuntimeError(f"No executor available for scheduled job '{name}'")

        job_exec = JobExecutor(
            scheduler_service=service,
            skill_execute_fn=_execute_scheduled_job,
        )
        main_orchestrator.job_executor = job_exec
        job_exec.start_tick_loop()
        logger.info(
            "Job executor wired (skills=%s, memory_jobs=%s).",
            "yes" if skill_executor else "no",
            "yes" if memory_job_executor else "no",
        )

    # Init scheduler API
    try:
        from scheduler_api import init_scheduler_api
        init_scheduler_api(service=service, executor=job_exec)
        logger.info("Scheduler API initialized.")
    except Exception as e:
        logger.warning("Scheduler API initialization failed: %s", e)

    return {
        "service": service,
        "job_executor": job_exec,
        "memory_job_executor": memory_job_executor,
        "memory_jobs_inserted": memory_jobs_inserted,
        "memory_jobs_updated": memory_jobs_updated,
    }


def _shutdown_scheduler(objects):
    """Shut down Scheduler subsystem."""
    global scheduler_service
    if objects.get("job_executor"):
        objects["job_executor"].stop()
        logger.info("Job executor stopped.")
    main_orchestrator.scheduler_service = None
    main_orchestrator.job_executor = None
    scheduler_service = None
    logger.info("Scheduler shut down.")


def _init_health_monitor():
    """Initialize Health Monitor subsystem."""
    from health.monitor import HealthMonitor, HealthCheck
    from health.api import set_snapshot_provider

    checks = [
        HealthCheck(
            name="llm_provider",
            check_fn=lambda: main_orchestrator.provider is not None,
            degraded_reason="LLM provider not initialized",
        ),
        HealthCheck(
            name="onboarding_ready",
            check_fn=lambda: onboarding_orch._determine_state() == "READY",
            degraded_reason="Onboarding not complete",
        ),
        HealthCheck(
            name="local_llm",
            check_fn=lambda: (
                main_orchestrator.local_model is not None
                and main_orchestrator.local_model.is_healthy()
            ),
            degraded_reason="Local LLM not responding",
        ),
    ]
    if main_orchestrator.scheduler_service:
        checks.append(HealthCheck(
            name="scheduler",
            check_fn=lambda: bool(
                main_orchestrator.job_executor
                and main_orchestrator.job_executor.is_running
            ),
            degraded_reason="Scheduler not running",
            snapshot_details_fn=lambda: {
                "last_scheduler_tick_at": (
                    main_orchestrator.scheduler_service.last_scheduler_tick_at
                    if main_orchestrator.scheduler_service
                    else None
                )
            },
        ))

    monitor = HealthMonitor(checks=checks, interval_s=30.0)
    monitor.start_monitor()
    # Serve a fresh readiness snapshot on each /health/ready request so the
    # panel reflects current provider/local-LLM state even after startup races.
    set_snapshot_provider(monitor.compute_snapshot)
    logger.info("Health monitor started.")
    return {"monitor": monitor}


def _shutdown_health_monitor(objects):
    """Shut down Health Monitor subsystem."""
    if objects.get("monitor"):
        objects["monitor"].stop_monitor()
    logger.info("Health monitor stopped.")


def _init_bal():
    """Initialize Business Automation Layer (BAL) subsystem."""
    from bal.config import load_bal_config
    from bal.database import BALDatabase
    from bal.receipts import emit_bal_receipt
    from bal.clients.api import init_client_api
    from bal.clients.repository import ClientRepository

    bal_config = load_bal_config()
    bal_db = BALDatabase(data_dir=bal_config.bal_data_dir)

    main_orchestrator._bal_config = bal_config
    main_orchestrator._bal_db = bal_db

    bal_client_repo = ClientRepository(bal_db)
    init_client_api(bal_client_repo)
    main_orchestrator._bal_client_repo = bal_client_repo
    logger.info("BAL Client Manager API initialized.")

    emit_bal_receipt(
        event_type="client",
        action_name="bal_startup",
        inputs={
            "phase": "2_client_manager",
            "intake_enabled": bal_config.bal_intake,
            "repurpose_enabled": bal_config.bal_repurpose,
            "delivery_enabled": bal_config.bal_delivery,
            "billing_enabled": bal_config.bal_billing,
        },
    )
    logger.info(
        "BAL initialized: intake=%s, repurpose=%s, delivery=%s, billing=%s",
        bal_config.bal_intake, bal_config.bal_repurpose,
        bal_config.bal_delivery, bal_config.bal_billing,
    )
    return {"config": bal_config, "db": bal_db, "repo": bal_client_repo}


def _shutdown_bal(objects):
    """Shut down BAL subsystem."""
    if objects.get("db"):
        try:
            objects["db"].close()
        except Exception:
            pass
    main_orchestrator._bal_config = None
    main_orchestrator._bal_db = None
    main_orchestrator._bal_client_repo = None
    logger.info("BAL shut down.")


# ── Tool Fabric Provider Subsystems ──────────────────────────────────
# These init/shutdown functions let the SubsystemManager hot-toggle
# individual providers inside the already-running ToolFabric.


def _init_host_bridge():
    """Hot-start the Host Bridge provider inside Tool Fabric."""
    from src.tools.fabric import get_tool_fabric
    from src.tools.providers.host_bridge import HostBridgeProvider

    fabric = get_tool_fabric()
    provider = HostBridgeProvider(workspace=fabric.config.default_workspace)
    fabric.register_provider(provider)
    fabric.update_router_preferences()
    logger.warning(
        "HOST BRIDGE hot-started — commands will be sent to host agent at %s",
        provider.config.agent_url,
    )
    return {"provider": provider}


def _shutdown_host_bridge(objects):
    """Hot-stop the Host Bridge provider."""
    from src.tools.fabric import get_tool_fabric
    fabric = get_tool_fabric()
    fabric.unregister_provider("host_bridge")
    fabric.update_router_preferences()
    logger.info("Host Bridge provider unregistered.")


def _init_uab():
    """Hot-start the UAB provider inside Tool Fabric."""
    from src.tools.fabric import get_tool_fabric
    from src.tools.providers.uab_bridge import UABProvider

    fabric = get_tool_fabric()
    provider = UABProvider()
    fabric.register_provider(provider)
    fabric.update_router_preferences()
    logger.warning(
        "UAB BRIDGE hot-started — desktop app control via daemon at %s",
        provider.config.daemon_url,
    )
    return {"provider": provider}


def _shutdown_uab(objects):
    """Hot-stop the UAB provider."""
    from src.tools.fabric import get_tool_fabric
    fabric = get_tool_fabric()
    fabric.unregister_provider("uab_bridge")
    fabric.update_router_preferences()
    logger.info("UAB Bridge provider unregistered.")


# ── HIVE Agent Mesh Subsystem ──────────────────────────────────────────

class _OrchestratorRouterAdapter:
    """Adapts orchestrator's provider to the ModelRouter.route() interface.

    The TaskDecomposer expects router.route(task_type, text) -> RouterResult,
    but the orchestrator uses provider.generate() directly. This adapter bridges
    the gap so HIVE can use the orchestrator's LLM provider for decomposition.
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    def route(self, task_type: str, text: str, **kwargs):
        from dataclasses import dataclass
        from typing import Optional

        @dataclass
        class _Result:
            output: Optional[str] = None

        provider = self._orch.provider
        if provider is None:
            return _Result(output=None)

        try:
            # Use the deep model for decomposition (planning tasks)
            deep_model = self._orch._get_deep_model()
            messages = [self._orch._build_frontier_user_message(text)]
            result = self._orch._provider_generate(
                model=deep_model,
                messages=messages,
                system_instruction="You are a task decomposer. Return only valid JSON.",
                config={"max_tokens": 4096},
            )
            return _Result(output=result.text if result and result.text else None)
        except Exception as exc:
            logger.error("HIVE router adapter LLM call failed: %s", exc)
            # Fall back to the fast model
            try:
                messages = [self._orch._build_frontier_user_message(text)]
                result = self._orch._provider_generate(
                    model=self._orch.model_name,
                    messages=messages,
                    system_instruction="You are a task decomposer. Return only valid JSON.",
                    config={"max_tokens": 4096},
                )
                return _Result(output=result.text if result and result.text else None)
            except Exception:
                return _Result(output=None)


def _init_hive():
    """Initialize the HIVE Agent Mesh subsystem."""
    from src.hive.config import load_hive_config
    from src.hive.registry import AgentRegistry
    from src.hive.receipt_manager import HiveReceiptManager
    from src.hive.scoped_soul import ScopedSoulGenerator
    from src.hive.lifecycle import AgentLifecycleManager
    from src.hive.decomposer import TaskDecomposer
    from src.hive.architect import ArchitectAgent
    from src.hive.api import init_hive_api
    from src.hive.integration.governance_bridge import GovernanceBridge
    from src.hive.integration.uab_executor import HiveUABExecutor
    from feature_flags import FEATURE_HIVE_UAB

    config = load_hive_config()
    registry = AgentRegistry(max_concurrent_agents=config.max_concurrent_agents)
    data_dir = os.environ.get("LANCELOT_DATA_DIR", "lancelot_data")
    receipt_mgr = HiveReceiptManager(data_dir=data_dir)
    soul_gen = ScopedSoulGenerator()
    parent_soul = getattr(main_orchestrator, "soul", None)
    governance_bridge = GovernanceBridge(
        risk_classifier=getattr(main_orchestrator, "_risk_classifier", None),
        trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
        decision_log=getattr(main_orchestrator, "decision_log", None),
        mcp_sentry=sentry,
    )

    # Bridge orchestrator's provider to the ModelRouter interface
    router_adapter = _OrchestratorRouterAdapter(main_orchestrator)

    # Create UAB action executor if UAB is enabled
    action_executor = None
    if FEATURE_HIVE_UAB:
        uab_provider = _get_uab_provider()
        if uab_provider:
            action_executor = HiveUABExecutor(
                uab_provider=uab_provider,
                llm_router=router_adapter,
                governance_bridge=governance_bridge,
            )
            logger.info("HIVE UAB executor wired — sub-agents will execute real desktop actions")
        else:
            logger.warning("HIVE_UAB enabled but no UABProvider found — sub-agents will run without UAB")

    lifecycle = AgentLifecycleManager(
        config=config,
        registry=registry,
        receipt_manager=receipt_mgr,
        soul_generator=soul_gen,
        governance_bridge=governance_bridge,
        parent_soul=parent_soul,
        action_executor=action_executor,
    )

    federation_entry = subsystem_manager._subsystems.get("federation")
    if federation_entry and federation_entry.running:
        try:
            lifecycle.update_spawn_controls(
                spawn_gate=federation_entry.objects.get("spawn_gate"),
                spawn_record_hook=federation_entry.objects.get("spawn_record_hook"),
                collapse_record_hook=federation_entry.objects.get("collapse_record_hook"),
            )
        except Exception as exc:
            logger.warning("Failed to wire existing federation budget governance into HIVE lifecycle: %s", exc)

    decomposer = TaskDecomposer(model_router=router_adapter)

    architect = ArchitectAgent(
        config=config,
        decomposer=decomposer,
        lifecycle=lifecycle,
        receipt_manager=receipt_mgr,
    )

    # Wire up API endpoints
    init_hive_api(architect, lifecycle, registry, receipt_mgr, config, audit_logger=main_orchestrator.audit_logger)

    logger.info(
        "HIVE Agent Mesh initialized: max_agents=%d, timeout=%ds, uab_executor=%s",
        config.max_concurrent_agents, config.default_task_timeout,
        "active" if action_executor else "none",
    )
    return {
        "config": config,
        "registry": registry,
        "receipt_mgr": receipt_mgr,
        "lifecycle": lifecycle,
        "architect": architect,
    }


def _get_uab_provider():
    """Get the UABProvider instance from ToolFabric if available."""
    try:
        from src.tools.providers.uab_bridge import UABProvider
        # Check if we can reach the daemon
        provider = UABProvider()
        health = provider.health_check()
        if health.state.value == "healthy":
            return provider
        logger.info("UAB provider unavailable at startup: %s", health.state.value)
        return provider  # Return anyway — daemon might come up later
    except Exception as exc:
        logger.info("UAB provider unavailable at startup: %s", exc)
        return None


def _shutdown_hive(objects):
    """Shut down the HIVE Agent Mesh subsystem."""
    from src.hive.api import shutdown_hive_api

    if objects.get("lifecycle"):
        try:
            objects["lifecycle"].shutdown()
        except Exception:
            pass
    shutdown_hive_api()
    logger.info("HIVE Agent Mesh shut down.")


def _resolve_peer_key(peer_registry, topology, instance_id: str):
    """Resolve a peer's public key from registry or topology.

    Checks PeerRegistryStore first (persistent), falls back to
    TopologyRegistry (runtime).
    """
    # Try persistent peer registry first
    try:
        key = peer_registry.get_peer_public_key(instance_id)
        if key:
            return key
    except Exception:
        pass
    # Fall back to topology registry
    peer = topology.get_peer(instance_id)
    if peer and peer.public_key_hex:
        try:
            return bytes.fromhex(peer.public_key_hex)
        except ValueError:
            return None
    return None


def _init_federation():
    """Initialize the Federation subsystem (control plane + data plane)."""
    from src.federation.config import load_federation_config
    from src.federation.identity import load_or_generate_identity
    from src.federation.heartbeat import HeartbeatEmitter
    from src.federation.topology import TopologyRegistry
    from src.federation.divergence import DivergenceDetector
    from src.federation.divergence import ReconciliationOutcome, reconcile_divergence
    from src.federation.receipt_manager import FederationReceiptManager
    from src.federation.api import init_federation_api, init_federation_transport
    from src.federation.auth import FederationAuth
    from src.federation.soul_compat import hash_soul
    from src.federation.transport import FederationTransport
    from src.federation.peer_registry import PeerRegistryStore
    from src.federation.peer_protocol import PeerRegistrationProtocol
    from src.federation.command_relay import CommandRelay
    from src.federation.soul_transport import SoulTransport
    from src.federation.soul_propagation import SoulPropagationEngine
    from src.federation.handoff_protocol import HandoffProtocol
    from src.federation.cost_reporter import CostReporter
    from src.federation.cost_aggregation import FederatedCostAggregator
    from src.federation.budget import FederationBudgetTracker
    from src.federation.heartbeat_mesh import HeartbeatMesh
    from src.federation.audit import FederationAuditEngine
    from src.federation.runtime_budget_source import RuntimeBudgetResolver
    from src.federation.runtime_budget_control import (
        handle_federation_cost_threshold_change,
    )

    data_dir = os.environ.get("LANCELOT_DATA_DIR", "lancelot_data")
    config = load_federation_config()
    identity = load_or_generate_identity(data_dir=data_dir)

    fed_data_dir = os.path.join(data_dir, "federation")
    graph_data_dir = os.path.join(data_dir, "federation")
    os.makedirs(fed_data_dir, exist_ok=True)
    topology = TopologyRegistry(
        self_instance_id=identity.instance_id,
        staleness_warning_s=config.staleness_warning_s,
        staleness_critical_s=config.staleness_critical_s,
        staleness_lost_s=config.staleness_lost_s,
        max_peers=config.max_peers,
        persistence_path=config.topology_path,
    )

    divergence = DivergenceDetector(
        instance_id=identity.instance_id,
        staleness_lost_s=config.staleness_lost_s,
    )

    receipt_mgr = FederationReceiptManager(
        instance_id=identity.instance_id,
        data_dir=data_dir,
    )

    # Audit engine — cross-instance audit trail
    audit_engine = FederationAuditEngine(
        max_entries=10000,
        persistence_path=os.path.join(fed_data_dir, "audit_log.json"),
    )

    # --- Control Plane (existing) ---
    emitter = HeartbeatEmitter(
        instance_id=identity.instance_id,
        interval_s=config.heartbeat_interval_s,
    )

    budget_tracker = FederationBudgetTracker()
    cost_aggregator = None
    runtime_budget_resolver = RuntimeBudgetResolver(
        topology_data_dir=graph_data_dir,
        identity=identity,
        fallback_daily_ceiling_usd=float(config.daily_budget_ceiling_usd),
    )

    def _record_cost_threshold_change(old_threshold, new_threshold) -> None:
        try:
            handle_federation_cost_threshold_change(
                old_threshold,
                new_threshold,
                cost_aggregator=cost_aggregator,
                receipt_mgr=receipt_mgr,
                audit_engine=audit_engine,
                identity=identity,
                soul_hash_provider=lambda: hash_soul(
                    getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None))
                ) if getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None)) is not None else "",
                pause_runtime_fn=_federation_local_pause_handler,
            )
        except Exception as exc:
            logger.warning("Failed to record federation cost threshold change: %s", exc)

    def _federation_usage_provider():
        tracker = getattr(main_orchestrator, "usage_tracker", None)
        usage_summary = tracker.summary() if tracker else {}
        total_cost = float(usage_summary.get("total_cost_est", 0.0) or 0.0)
        total_tokens = int(usage_summary.get("total_tokens_est", 0) or 0)

        hive_entry = subsystem_manager._subsystems.get("hive")
        active_spawns = 0
        if hive_entry and hive_entry.running:
            registry = hive_entry.objects.get("registry")
            if registry is not None:
                try:
                    active_spawns = int(registry.active_count())
                except Exception:
                    active_spawns = 0

        # Federation budget tracking is cost-governance oriented today, not
        # a second token-budget system. We surface live cost/usage from the
        # main orchestrator and active HIVE spawn pressure from the registry.
        daily_ceiling = runtime_budget_resolver.resolve_daily_ceiling_usd()
        projected_today = total_cost

        return {
            "actual_today_usd": total_cost,
            "projected_today_usd": projected_today,
            "daily_ceiling_usd": daily_ceiling,
            "active_spawns": active_spawns,
            "spawn_cost_rate_usd_hr": 0.0,
            "total_tokens_today": total_tokens,
            "budget_tracker": budget_tracker.get_snapshot(),
        }

    def _runtime_federation_soul():
        return getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None))

    def _runtime_federation_soul_hash() -> str:
        current_soul = _runtime_federation_soul()
        return hash_soul(current_soul) if current_soul is not None else ""

    def _record_divergence(peer_instance_id, snapshot) -> None:
        receipt_mgr.record_divergence(
            peer_instance_id=peer_instance_id,
            staleness_seconds=divergence.get_divergence_duration_s(),
            soul_version_hash=snapshot.soul_hash_at_divergence,
        )
        audit_engine.record(
            event_type="divergence_detected",
            instance_id=identity.instance_id,
            soul_version_hash=snapshot.soul_hash_at_divergence,
            details={
                "peer_instance_id": peer_instance_id,
                "snapshot": snapshot.to_dict(),
            },
        )

    def _reconcile_after_reconnect(peer_instance_id, detector) -> None:
        snapshot = detector.divergence_snapshot
        if snapshot is None:
            return
        outcome, conflicts = reconcile_divergence(
            divergence_snapshot=snapshot,
            reconnection_soul_hash=_runtime_federation_soul_hash(),
            reconnection_budget_pct=float(
                cost_reporter.get_aggregate_status().get("utilization_pct", 0.0)
            ) if cost_reporter else 0.0,
        )
        detector.mark_reconciled(outcome, conflicts)
        receipt_mgr.record_reconnection(
            peer_instance_id=peer_instance_id,
            divergence_duration_s=detector.get_divergence_duration_s(),
            reconciliation_result=outcome.value,
        )
        audit_engine.record(
            event_type="reconciliation_completed",
            instance_id=identity.instance_id,
            soul_version_hash=_runtime_federation_soul_hash(),
            details={
                "peer_instance_id": peer_instance_id,
                "outcome": outcome.value,
                "conflicts": [
                    {
                        "conflict_type": c.conflict_type,
                        "description": c.description,
                        "resolution": c.resolution,
                        "affected_component": c.affected_component,
                    }
                    for c in conflicts
                ],
            },
        )
        if outcome == ReconciliationOutcome.COMPATIBLE:
            detector.reset_to_connected()

    emitter.set_providers(
        soul_hash=lambda: (
            _runtime_federation_soul_hash()
        ),
        mode=lambda: topology.deployment_mode.value,
        budget=lambda: (
            float(_federation_usage_provider().get("actual_today_usd", 0.0))
            / max(float(_federation_usage_provider().get("daily_ceiling_usd", 10.0)), 0.0001)
        ) * 100.0,
        peer_count=lambda: topology.peer_count(),
    )

    # --- Data Plane (transport layer) ---

    # 1. Persistent peer registry (SQLite)
    peer_db_path = config.peer_db_path or os.path.join(fed_data_dir, "peers.sqlite")
    peer_registry = PeerRegistryStore(db_path=peer_db_path)

    # 2. Auth (Ed25519 request signing + verification)
    auth = FederationAuth(
        identity=identity,
        peer_key_resolver=lambda instance_id: _resolve_peer_key(peer_registry, topology, instance_id),
        timestamp_window_s=config.auth_timestamp_window_s,
        nonce_cache_size=config.nonce_cache_size,
        nonce_store=peer_registry,
    )

    # 3. Resilient HTTP transport (circuit breakers, retries, connection pooling)
    transport = FederationTransport(
        auth=auth,
        max_retries=config.retry_max_attempts,
        base_backoff_s=config.retry_base_backoff_s,
        circuit_breaker_threshold=config.circuit_breaker_threshold,
        circuit_breaker_recovery_s=config.circuit_breaker_recovery_s,
        connect_timeout_s=config.connect_timeout_s,
        read_timeout_s=config.read_timeout_s,
    )

    def _handle_federation_peer_removed(instance_id: str) -> None:
        if heartbeat_mesh:
            heartbeat_mesh.on_peer_removed(instance_id)
        if cost_aggregator:
            cost_aggregator.remove_instance(instance_id)

    # 4. Peer registration handshake protocol
    peer_protocol = PeerRegistrationProtocol(
        identity=identity,
        topology=topology,
        transport=transport,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
        self_address=config.self_address,
        on_peer_registered=lambda instance_id, address: heartbeat_mesh.on_peer_added(instance_id, address) if heartbeat_mesh else None,
        on_peer_removed=_handle_federation_peer_removed,
        persistence_path=os.path.join(fed_data_dir, "pending_registrations.json"),
    )

    # 5. Command relay (kill switch + pause propagation)
    def _federation_local_kill_handler(reason: str) -> int:
        hive_entry = subsystem_manager._subsystems.get("hive")
        lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
        if lifecycle is None:
            logger.warning("Federation kill requested but HIVE lifecycle is not available")
            return 0
        collapsed = lifecycle.kill_all(
            reason,
            operator_id="federation-peer",
            session_id="federation-peer",
        )
        return len(collapsed)

    def _cancel_federation_approval_queues(reason: str) -> dict:
        scheduler_pending = 0
        scheduler_granted = 0
        sentry_pending_denied = 0

        job_executor = getattr(main_orchestrator, "job_executor", None)
        if job_executor is not None:
            try:
                cleared = job_executor.clear_approval_state(
                    reason=reason,
                    operator_id="federation-peer",
                    session_id="federation-peer",
                    actor="Federation Peer",
                )
                scheduler_pending = int(cleared.get("pending_cleared", 0) or 0)
                scheduler_granted = int(cleared.get("granted_cleared", 0) or 0)
            except Exception as exc:
                logger.warning(
                    "Failed to clear scheduler approvals during federation full stop: %s",
                    exc,
                )

        try:
            sentry._cleanup_expired()
            for req_id, req in list(sentry.pending_requests.items()):
                if req.get("status") == "PENDING" and sentry.deny_request(req_id):
                    sentry_pending_denied += 1
        except Exception as exc:
            logger.warning(
                "Failed to clear sentry approvals during federation full stop: %s",
                exc,
            )

        return {
            "scheduler_pending_cleared": scheduler_pending,
            "scheduler_granted_cleared": scheduler_granted,
            "sentry_pending_denied": sentry_pending_denied,
        }

    def _federation_local_pause_handler(
        reason: str,
        *,
        full_stop: bool = False,
        source: str = "federation",
    ) -> dict:
        from src.core.runtime_pause import pause_runtime

        pause_runtime(
            reason,
            operator_id="federation-peer",
            operator_name="Federation Peer",
            session_id="federation-peer",
            source=source,
        )

        approval_queue_result = {
            "scheduler_pending_cleared": 0,
            "scheduler_granted_cleared": 0,
            "sentry_pending_denied": 0,
        }
        if full_stop:
            approval_queue_result = _cancel_federation_approval_queues(reason)

        hive_entry = subsystem_manager._subsystems.get("hive")
        lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
        registry = hive_entry.objects.get("registry") if hive_entry and hive_entry.running else None
        if lifecycle is None or registry is None:
            return {
                "paused_agents": 0,
                "already_paused_agents": 0,
                "execution_state": "paused",
                "full_stop": full_stop,
                **approval_queue_result,
            }

        from src.hive.types import AgentState

        paused_agents = 0
        failed_agents = []

        for record in registry.list_by_state(AgentState.EXECUTING):
            try:
                lifecycle.pause(
                    record.agent_id,
                    reason,
                    operator_id="federation-peer",
                    session_id="federation-peer",
                )
                paused_agents += 1
            except KeyError:
                # Agent may have completed between roster read and pause request.
                continue
            except Exception:
                failed_agents.append(record.agent_id)

        if failed_agents:
            raise RuntimeError(
                f"Failed to pause active agents: {', '.join(sorted(failed_agents))}"
            )

        already_paused_agents = len(registry.list_by_state(AgentState.PAUSED))
        execution_state = "paused" if (paused_agents or already_paused_agents) else "idle"
        return {
            "paused_agents": paused_agents,
            "already_paused_agents": already_paused_agents,
            "execution_state": execution_state,
            "full_stop": full_stop,
            **approval_queue_result,
        }

    def _federation_local_resume_handler(reason: str) -> dict:
        from src.core.runtime_pause import resume_runtime

        resume_runtime(
            operator_id="federation-peer",
            operator_name="Federation Peer",
            session_id="federation-peer",
            source="federation",
        )

        hive_entry = subsystem_manager._subsystems.get("hive")
        lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
        registry = hive_entry.objects.get("registry") if hive_entry and hive_entry.running else None
        if lifecycle is None or registry is None:
            return {
                "resumed_agents": 0,
                "execution_state": "running",
            }

        from src.hive.types import AgentState

        resumed_agents = 0
        failed_agents = []

        for record in registry.list_by_state(AgentState.PAUSED):
            try:
                lifecycle.resume(
                    record.agent_id,
                    operator_id="federation-peer",
                    session_id="federation-peer",
                )
                resumed_agents += 1
            except KeyError:
                continue
            except Exception:
                failed_agents.append(record.agent_id)

        if failed_agents:
            raise RuntimeError(
                f"Failed to resume paused agents: {', '.join(sorted(failed_agents))}"
            )

        return {
            "resumed_agents": resumed_agents,
            "execution_state": "running",
        }

    from src.federation.kill_switch import FederatedKillSwitch

    kill_switch = FederatedKillSwitch(
        self_instance_id=identity.instance_id,
        peer_ids=[p.instance_id for p in topology.list_peers()],
        local_kill_handler=_federation_local_kill_handler,
        persistence_path=os.path.join(fed_data_dir, "kill_commands.json"),
    )

    command_relay = CommandRelay(
        identity=identity,
        transport=transport,
        topology=topology,
        kill_switch=kill_switch,
        local_pause_handler=_federation_local_pause_handler,
        local_resume_handler=_federation_local_resume_handler,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
    )

    propagation_engine = SoulPropagationEngine(
        self_instance_id=identity.instance_id,
        peer_ids=[p.instance_id for p in topology.list_peers()],
        persistence_path=os.path.join(fed_data_dir, "soul_propagation.json"),
    )

    # 6. Soul transport (push/pull with tier-aware propagation)
    soul_transport = SoulTransport(
        identity=identity,
        transport=transport,
        topology=topology,
        propagation_engine=propagation_engine,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
        handoff_timeout_s=config.handoff_timeout_s,
        current_soul_provider=lambda: getattr(app.state, "active_soul", getattr(main_orchestrator, "soul", None)),
        runtime_reload_callback=getattr(app.state, "apply_runtime_soul", None),
        soul_dir=os.environ.get("SOUL_DIR", None),
        heartbeat_emitter=emitter,
        local_pause_handler=_federation_local_pause_handler,
        local_resume_handler=_federation_local_resume_handler,
    )

    # 7. Handoff protocol (task delegation between peers)
    handoff_protocol = HandoffProtocol(
        identity=identity,
        transport=transport,
        topology=topology,
        receipt_mgr=receipt_mgr,
        audit=audit_engine,
        handoff_timeout_s=config.handoff_timeout_s,
        current_soul_provider=lambda: getattr(main_orchestrator, "soul", None),
        persistence_path=os.path.join(fed_data_dir, "active_handoffs.json"),
    )

    # 8. Cost reporter (periodic budget reporting to peers)
    cost_aggregator = FederatedCostAggregator(
        on_threshold_change=_record_cost_threshold_change,
        persistence_path=os.path.join(fed_data_dir, "cost_aggregate.json"),
        stale_after_s=max(config.cost_report_interval_s * 2.0, config.staleness_lost_s),
    )
    cost_reporter = CostReporter(
        identity=identity,
        transport=transport,
        topology=topology,
        cost_aggregator=cost_aggregator,
        usage_provider=_federation_usage_provider,
        interval_s=config.cost_report_interval_s,
    )

    # 9. Heartbeat mesh (SSE subscription to peer health streams)
    heartbeat_mesh = HeartbeatMesh(
        topology=topology,
        divergence_detector=divergence,
        auth=auth,
        connect_timeout_s=config.connect_timeout_s,
        read_timeout_s=120.0,
        current_soul_hash_provider=lambda: (
            _runtime_federation_soul_hash()
        ),
        active_task_count_provider=lambda: int(bool(getattr(main_orchestrator, "agent_busy", False))),
        hive_spawn_count_provider=lambda: (
            hive_entry.objects["registry"].active_count()
            if (
                (hive_entry := subsystem_manager._subsystems.get("hive"))
                and hive_entry.running
                and hive_entry.objects.get("registry") is not None
            ) else 0
        ),
        hive_spawn_states_provider=lambda: (
            {
                record.agent_id: record.state.value
                for record in hive_entry.objects["registry"].list_active()
            }
            if (
                (hive_entry := subsystem_manager._subsystems.get("hive"))
                and hive_entry.running
                and hive_entry.objects.get("registry") is not None
            ) else {}
        ),
        pending_handoffs_provider=lambda: (
            [
                {
                    "handoff_id": handoff["handoff_id"],
                    "state": handoff["status"],
                    "target_instance_id": handoff.get("target_instance_id", ""),
                }
                for handoff in handoff_protocol.list_active_handoffs()
            ] if handoff_protocol else []
        ),
        budget_utilization_provider=lambda: float(
            cost_reporter.get_aggregate_status().get("utilization_pct", 0.0)
        ) if cost_reporter else 0.0,
        on_diverged=_record_divergence,
        on_reconnecting=_reconcile_after_reconnect,
    )

    def _federation_spawn_gate(task_spec) -> None:
        model_tier = (
            str(task_spec.context.get("model_tier", "")).strip()
            if getattr(task_spec, "context", None) else ""
        ) or "T2"
        estimated_tokens = 0
        if getattr(task_spec, "context", None):
            try:
                estimated_tokens = int(task_spec.context.get("estimated_tokens", 0) or 0)
            except Exception:
                estimated_tokens = 0

        if divergence.state.value == "diverged" and model_tier.upper() == "T3":
            divergence_duration_s = divergence.get_divergence_duration_s()
            receipt_mgr.record_budget_threshold(
                threshold_level="diverged",
                utilization_pct=float(cost_aggregator.get_aggregate().utilization_pct),
                action_taken="block_t3_spawn",
            )
            audit_engine.record(
                event_type="divergence_detected",
                instance_id=identity.instance_id,
                soul_version_hash=_runtime_federation_soul_hash(),
                risk_tier="T3",
                details={
                    "action": "spawn_blocked",
                    "reason": "federation_diverged",
                    "model_tier": model_tier,
                    "estimated_tokens": estimated_tokens,
                    "divergence_duration_s": divergence_duration_s,
                },
            )
            raise RuntimeError(
                "Federation divergence active - T3 spawns are blocked until reconciliation completes"
            )

        if (
            heartbeat_mesh
            and getattr(heartbeat_mesh, "divergence_evaluation_failed", False)
            and model_tier.upper() == "T3"
        ):
            raise RuntimeError(
                "Federation divergence evaluation unavailable - T3 spawns are blocked until mesh health recovers"
            )

        allowed, reason = cost_aggregator.check_spawn_allowed(identity.instance_id)
        if not allowed:
            raise RuntimeError(f"Federation spawn budget blocked: {reason}")

        decision, reason = budget_tracker.check_spawn(
            model_tier=model_tier,
            estimated_tokens=estimated_tokens,
        )
        if decision.value == "blocked":
            raise RuntimeError(f"Federation spawn budget blocked: {reason}")
        if decision.value == "restricted":
            receipt_mgr.record_budget_threshold(
                threshold_level=budget_tracker.threshold_level.value,
                utilization_pct=budget_tracker.utilization_pct,
                action_taken="spawn_allowed_with_notice",
            )

    def _federation_record_spawn(record) -> None:
        model_tier = (
            str(record.task_spec.context.get("model_tier", "")).strip()
            if getattr(record.task_spec, "context", None) else ""
        ) or "T2"
        estimated_tokens = 0
        if getattr(record.task_spec, "context", None):
            try:
                estimated_tokens = int(record.task_spec.context.get("estimated_tokens", 0) or 0)
            except Exception:
                estimated_tokens = 0
        budget_tracker.record_spawn(
            agent_id=record.agent_id,
            instance_id=identity.instance_id,
            model_tier=model_tier,
            estimated_tokens=estimated_tokens,
        )
        receipt_mgr.record_spawn_receipt(
            agent_id=record.agent_id,
            model_tier=model_tier,
            estimated_cost=float(estimated_tokens),
            federation_quest_id=record.quest_id,
        )

    def _federation_record_collapse(record, result) -> None:
        actual_tokens = 0
        outputs = getattr(result, "outputs", {}) or {}
        try:
            actual_tokens = int(outputs.get("actual_tokens", 0) or 0)
        except Exception:
            actual_tokens = 0
        budget_tracker.record_collapse(record.agent_id, actual_tokens=actual_tokens)

    # Wire control plane API endpoints
    init_federation_api(
        identity=identity,
        heartbeat_emitter=emitter,
        config=config,
        topology_registry=topology,
        divergence_detector=divergence,
    )

    # Wire data plane transport handlers into API
    init_federation_transport(
        peer_protocol=peer_protocol,
        command_relay=command_relay,
        soul_transport=soul_transport,
        handoff_protocol=handoff_protocol,
        cost_reporter=cost_reporter,
        auth=auth,
        audit_engine=audit_engine,
        transport=transport,
        heartbeat_mesh=heartbeat_mesh,
    )

    hive_entry = subsystem_manager._subsystems.get("hive")
    if hive_entry and hive_entry.running and hive_entry.objects.get("lifecycle") is not None:
        try:
            hive_entry.objects["lifecycle"].update_spawn_controls(
                spawn_gate=_federation_spawn_gate,
                spawn_record_hook=_federation_record_spawn,
                collapse_record_hook=_federation_record_collapse,
            )
        except Exception as exc:
            logger.warning("Failed to wire federation budget governance into HIVE lifecycle: %s", exc)

    # Initialize Graph Builder API
    try:
        from src.federation.graph_api import init_graph_api
        init_graph_api(graph_data_dir)
    except Exception as e:
        logger.warning("Graph Builder API init failed: %s", e)

    # Start synchronous background heartbeat emitter
    emitter.start()

    # Schedule async transport startup (will run when event loop is available)
    async def _start_transport_layer():
        try:
            await transport.start()
            logger.info("Federation transport started (httpx connection pool ready)")
        except Exception as e:
            logger.warning("Federation transport start failed: %s", e)
        try:
            await heartbeat_mesh.start()
            logger.info("Federation heartbeat mesh started")
        except Exception as e:
            logger.warning("Federation heartbeat mesh start failed: %s", e)
        try:
            await cost_reporter.start()
            logger.info("Federation cost reporter started")
        except Exception as e:
            logger.warning("Federation cost reporter start failed: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_start_transport_layer())
    except RuntimeError:
        # No event loop yet — will be started from startup_event
        pass

    logger.info(
        "Federation initialized: instance=%s, fingerprint=%s, heartbeat=%.1fs, mode=%s, transport=ACTIVE",
        identity.instance_id, identity.fingerprint, config.heartbeat_interval_s,
        topology.deployment_mode.value,
    )
    return {
        "config": config,
        "identity": identity,
        "emitter": emitter,
        "topology": topology,
        "divergence": divergence,
        "receipt_mgr": receipt_mgr,
        "peer_registry": peer_registry,
        "auth": auth,
        "transport": transport,
        "peer_protocol": peer_protocol,
        "command_relay": command_relay,
        "propagation_engine": propagation_engine,
        "soul_transport": soul_transport,
        "handoff_protocol": handoff_protocol,
        "budget_tracker": budget_tracker,
        "cost_aggregator": cost_aggregator,
        "cost_reporter": cost_reporter,
        "heartbeat_mesh": heartbeat_mesh,
        "audit_engine": audit_engine,
        "spawn_gate": _federation_spawn_gate,
        "spawn_record_hook": _federation_record_spawn,
        "collapse_record_hook": _federation_record_collapse,
    }


def _shutdown_federation(objects):
    """Shut down the Federation subsystem (control plane + data plane).

    Note: async transport objects (transport, heartbeat_mesh, cost_reporter)
    are stopped in shutdown_event() before this runs, since this function
    is called synchronously by subsystem_manager.stop_all().
    """
    from src.federation.api import shutdown_federation_api

    if objects.get("emitter"):
        try:
            objects["emitter"].stop()
        except Exception:
            pass

    shutdown_federation_api()
    logger.info("Federation shut down.")


def _bootstrap_model_discovery():
    """Create ModelDiscovery + wire into Provider API when provider becomes available.

    Called at startup and again after OAuth hot-initializes the provider.
    Safe to call multiple times — skips if provider is still None.
    """
    if main_orchestrator.provider is None:
        return False

    try:
        from model_discovery import ModelDiscovery
        from providers.api import init_provider_api, load_persisted_config

        _persisted_config = load_persisted_config()
        _persisted_lane_overrides = _persisted_config.get("lane_overrides", {})

        _lane_overrides = {}
        try:
            from provider_profile import ProfileRegistry
            _registry = ProfileRegistry()
            _prov_name = main_orchestrator.provider.provider_name
            if _registry.has_provider(_prov_name):
                _profile = _registry.get_profile(_prov_name)
                _lane_overrides["fast"] = _profile.fast.model
                _lane_overrides["deep"] = _profile.deep.model
                if _profile.cache:
                    _lane_overrides["cache"] = _profile.cache.model
        except Exception:
            pass

        _lane_overrides.update(_persisted_lane_overrides)

        discovery = ModelDiscovery(
            provider=main_orchestrator.provider,
            profiles_path="config/model_profiles.yaml",
            lane_overrides=_lane_overrides,
        )
        discovery.refresh()

        for _lane, _model_id in _persisted_lane_overrides.items():
            try:
                main_orchestrator.set_lane_model(_lane, _model_id)
            except Exception as _e:
                logger.warning("Failed to apply lane override %s=%s: %s", _lane, _model_id, _e)

        init_provider_api(discovery, orchestrator=main_orchestrator)
        _bootstrap_model_router()
        logger.info(
            "Model discovery: %d models found, lanes: %s",
            len(discovery.discovered_models),
            discovery.lane_assignments,
        )
        return True
    except Exception as e:
        logger.warning("Model discovery bootstrap failed: %s", e)
        return False


def _bootstrap_model_router() -> bool:
    """Create the live ModelRouter and wire it into the orchestrator + control plane."""
    if main_orchestrator.provider is None:
        return False

    try:
        from model_router import ModelRouter
        from provider_profile import ProfileRegistry
        from src.core.control_plane import set_model_router, set_usage_tracker

        router = ModelRouter(
            registry=ProfileRegistry(),
            local_client=getattr(main_orchestrator, "local_model", None),
            provider_client=main_orchestrator.provider,
        )

        existing_tracker = getattr(main_orchestrator, "usage_tracker", None)
        persistence = getattr(existing_tracker, "_persistence", None)
        if persistence is not None:
            router.usage.set_persistence(persistence)

        main_orchestrator.model_router = router
        main_orchestrator.usage_tracker = router.usage
        set_model_router(router)
        set_usage_tracker(router.usage)
        logger.info(
            "Model router wired live (local_model=%s, provider=%s).",
            "ready" if getattr(main_orchestrator, "local_model", None) else "none",
            main_orchestrator.provider.provider_name,
        )
        return True
    except Exception as exc:
        logger.warning("Model router bootstrap failed: %s", exc)
        return False


@app.on_event("startup")
async def startup_event():
    global _startup_time
    _startup_time = time.time()

    # Capture the main event loop for cross-thread EventBus publishing
    try:
        from event_bus import event_bus as _eb
        _eb.set_loop(asyncio.get_running_loop())
    except Exception:
        pass

    # F8: Validate environment on startup
    _provider = os.getenv("LANCELOT_PROVIDER", "gemini")
    _key_vars = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "xai": "XAI_API_KEY"}
    _key_var = _key_vars.get(_provider, "GEMINI_API_KEY")
    if not os.getenv(_key_var) and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        logger.warning("No %s set. LLM features may be unavailable.", _key_var)
    if not API_TOKEN:
        logger.warning("LANCELOT_API_TOKEN not set. Running in dev mode (no auth required).")

    # [NEW] Start Production Services
    librarian.start()
    await antigravity.start()

    # Inject Sentry into Orchestrator (Dependency Injection pattern)
    main_orchestrator.sentry = sentry
    # [NEW] Inject MFA Guard and Antigravity into Orchestrator (Monkey Patching for now)
    main_orchestrator.mfa_guard = mfa_guard
    main_orchestrator.antigravity = antigravity

    # ===== FEATURE FLAG LOGGING =====
    try:
        from feature_flags import log_feature_flags
        log_feature_flags()
    except Exception as e:
        logger.warning(f"Feature flag logging failed: {e}")

    # Shared backend auth for War Room/control-plane routers.
    try:
        from src.core.api_auth import init_api_auth
        init_api_auth(verify_token)
    except Exception as e:
        logger.warning("Shared API auth initialization failed: %s", e)

    def _apply_runtime_soul(active_soul):
        """Refresh live runtime policy objects after Soul activation."""
        main_orchestrator.soul = active_soul

        risk_classifier = getattr(main_orchestrator, "_risk_classifier", None)
        if risk_classifier is not None:
            risk_classifier.update_soul(active_soul)

        try:
            from src.a2a.agent_card import invalidate_card
            invalidate_card()
        except Exception:
            pass

        try:
            from src.timetravel.api import update_timetravel_soul
            update_timetravel_soul(active_soul)
        except Exception as exc:
            logger.warning("Time-Travel Soul refresh failed: %s", exc)

        try:
            from src.mcp.api import update_mcp_soul
            update_mcp_soul(active_soul)
        except Exception as exc:
            logger.warning("MCP Soul refresh failed: %s", exc)

        try:
            hive_entry = subsystem_manager._subsystems.get("hive")
            lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
            if lifecycle is not None:
                lifecycle.update_parent_soul(active_soul)
        except Exception as exc:
            logger.warning("HIVE Soul refresh failed: %s", exc)

        app.state.active_soul = active_soul

        try:
            federation_entry = subsystem_manager._subsystems.get("federation")
            emitter = federation_entry.objects.get("emitter") if federation_entry and federation_entry.running else None
            if emitter is not None:
                emitter.emit_once()
        except Exception as exc:
            logger.warning("Federation heartbeat Soul refresh failed: %s", exc)

    app.state.apply_runtime_soul = _apply_runtime_soul

    try:
        from soul.api import init_soul_runtime
        init_soul_runtime(_apply_runtime_soul)
    except Exception as e:
        logger.warning("Soul runtime refresh initialization failed: %s", e)

    # ===== ALWAYS MOUNT SUBSYSTEM ROUTERS =====
    # Routes are gated by middleware when their flag is OFF.
    try:
        from memory.api import router as memory_router
        app.include_router(memory_router)
    except Exception as e:
        logger.warning("Memory API router mount failed: %s", e)

    try:
        from soul.api import router as soul_router
        app.include_router(soul_router)
    except Exception as e:
        logger.warning("Soul API router mount failed: %s", e)

    try:
        from soul.template_api import router as template_router
        app.include_router(template_router)
    except Exception as e:
        logger.warning("Soul Template API router mount failed: %s", e)

    try:
        from scheduler_api import router as scheduler_router
        app.include_router(scheduler_router)
    except Exception as e:
        logger.warning("Scheduler API router mount failed: %s", e)

    try:
        from health.api import router as health_api_router
        app.include_router(health_api_router)
    except Exception as e:
        logger.warning("Health API router mount failed: %s", e)

    try:
        from bal.clients.api import router as bal_client_router
        app.include_router(bal_client_router)
    except Exception as e:
        logger.warning("BAL Client API router mount failed: %s", e)

    try:
        from src.hive.api import router as hive_router
        app.include_router(hive_router)
    except Exception as e:
        logger.warning("HIVE Agent Mesh API router mount failed: %s", e)

    try:
        from src.federation.api import router as federation_router
        app.include_router(federation_router)
    except Exception as e:
        logger.warning("Federation API router mount failed: %s", e)

    try:
        from src.federation.graph_api import graph_router
        app.include_router(graph_router)
    except Exception as e:
        logger.warning("Graph Builder API router mount failed: %s", e)

    try:
        from src.mcp.api import router as mcp_router
        app.include_router(mcp_router)
    except Exception as e:
        logger.warning("MCP API router mount failed: %s", e)

    # ===== REGISTER SUBSYSTEMS WITH HOT-TOGGLE MANAGER =====
    subsystem_manager.register("memory", "FEATURE_MEMORY_VNEXT", _init_memory, _shutdown_memory, ["/memory"])
    subsystem_manager.register("soul", "FEATURE_SOUL", _init_soul, _shutdown_soul, ["/soul"])
    subsystem_manager.register("skills", "FEATURE_SKILLS", _init_skills, _shutdown_skills, [])
    subsystem_manager.register("scheduler", "FEATURE_SCHEDULER", _init_scheduler, _shutdown_scheduler, ["/api/scheduler"])
    subsystem_manager.register("health_monitor", "FEATURE_HEALTH_MONITOR", _init_health_monitor, _shutdown_health_monitor, ["/health"])
    subsystem_manager.register("bal", "FEATURE_BAL", _init_bal, _shutdown_bal, ["/api/v1/clients"])

    # Tool Fabric provider subsystems (hot-toggle individual providers)
    subsystem_manager.register("host_bridge", "FEATURE_TOOLS_HOST_BRIDGE", _init_host_bridge, _shutdown_host_bridge, [])
    subsystem_manager.register("uab_bridge", "FEATURE_TOOLS_UAB", _init_uab, _shutdown_uab, [])
    subsystem_manager.register("hive", "FEATURE_HIVE", _init_hive, _shutdown_hive, ["/api/hive"])
    subsystem_manager.register("federation", "FEATURE_FEDERATION", _init_federation, _shutdown_federation, ["/api/federation"])

    # ===== CONDITIONALLY START SUBSYSTEMS =====
    from feature_flags import (
        FEATURE_MEMORY_VNEXT, FEATURE_SOUL, FEATURE_SKILLS,
        FEATURE_SCHEDULER, FEATURE_HEALTH_MONITOR, FEATURE_BAL,
        FEATURE_TOOLS_HOST_BRIDGE, FEATURE_TOOLS_UAB, FEATURE_HIVE,
        FEATURE_FEDERATION,
    )

    if FEATURE_MEMORY_VNEXT:
        try:
            subsystem_manager.start("memory")
        except Exception as e:
            logger.error("Memory vNext initialization failed: %s", e)
            main_orchestrator._memory_enabled = False
    else:
        logger.info("Memory vNext disabled by feature flag.")

    if FEATURE_SOUL:
        try:
            subsystem_manager.start("soul")
        except Exception as e:
            logger.warning("Soul initialization failed: %s", e)
    else:
        logger.info("Soul disabled by feature flag.")

    if FEATURE_SKILLS:
        try:
            subsystem_manager.start("skills")
            # Register Skills API for War Room proposal management
            from skills_api import router as skills_api_router, init_skills_api
            init_skills_api(
                factory=main_orchestrator.skill_factory,
                registry=main_orchestrator.skill_registry,
                executor=main_orchestrator.skill_executor,
            )
            app.include_router(skills_api_router)
            logger.info("Skills API initialized.")
        except Exception as e:
            logger.warning("Skills initialization failed: %s", e)

    if FEATURE_SCHEDULER:
        try:
            subsystem_manager.start("scheduler")
        except Exception as e:
            logger.warning("Scheduler initialization failed: %s", e)

    # ===== MARK PROVIDER SUBSYSTEMS RUNNING IF ALREADY BOOTED =====
    # _setup_default_providers() already registered these at ToolFabric init,
    # so just mark the SubsystemManager entries as running (no double-init).
    if FEATURE_TOOLS_HOST_BRIDGE:
        entry = subsystem_manager._subsystems.get("host_bridge")
        if entry and not entry.running:
            entry.running = True
            logger.info("Host Bridge provider marked running (booted at init)")
    if FEATURE_TOOLS_UAB:
        entry = subsystem_manager._subsystems.get("uab_bridge")
        if entry and not entry.running:
            entry.running = True
            logger.info("UAB Bridge provider marked running (booted at init)")

    if FEATURE_HIVE:
        try:
            subsystem_manager.start("hive")
        except Exception as e:
            logger.warning("HIVE Agent Mesh initialization failed: %s", e)
    else:
        logger.info("HIVE Agent Mesh disabled by feature flag.")

    if FEATURE_FEDERATION:
        try:
            subsystem_manager.start("federation")
        except Exception as e:
            logger.warning("Federation initialization failed: %s", e)
    else:
        logger.info("Federation disabled by feature flag.")

    # ===== PHASE 4b: LOCAL MODEL CLIENT (V8) =====
    try:
        from feature_flags import FEATURE_LOCAL_AGENTIC
        if FEATURE_LOCAL_AGENTIC:
            from local_model_client import LocalModelClient
            _local_model = LocalModelClient()
            if _local_model.is_healthy():
                main_orchestrator.local_model = _local_model
                logger.info("Local model client connected and healthy")
            else:
                logger.warning("Local model client created but not healthy — local agentic disabled")
    except Exception as e:
        logger.warning("Local model client initialization failed: %s", e)

    if FEATURE_BAL:
        try:
            subsystem_manager.start("bal")
        except Exception as e:
            logger.warning("BAL initialization failed: %s", e)
    else:
        logger.info("BAL disabled by feature flag.")

    # ===== PHASE 6: CONTROL PLANE =====
    try:
        from src.core.control_plane import init_control_plane, set_runtime_control_hooks
        from src.core.control_plane import router as cp_router

        def _runtime_emergency_stop_handler(
            *,
            reason: str,
            operator_id: str = "",
            operator_name: str = "",
            session_id: str = "",
        ) -> dict:
            hive_entry = subsystem_manager._subsystems.get("hive")
            lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
            if lifecycle is None:
                raise RuntimeError("HIVE emergency stop engine is not available")

            collapsed = lifecycle.kill_all(
                reason,
                operator_id=operator_id or "operator",
                session_id=session_id or "system",
            )
            return {
                "stopped_hive_agents": len(collapsed),
                "stopped_agent_ids": collapsed,
                "execution_state": "emergency_stopped",
            }

        init_control_plane(data_dir="/home/lancelot/data")
        set_runtime_control_hooks(emergency_stop_handler=_runtime_emergency_stop_handler)
        app.include_router(cp_router)
        logger.info("Control plane initialized.")
    except Exception as e:
        logger.warning(f"Control plane initialization failed: {e}")

    # ===== WAR ROOM APIs =====
    # Receipts API
    try:
        import receipts_api as _receipts_api_module
        from receipts_api import router as receipts_router, init_receipts_api
        from src.core.governance_receipts import init_governance_receipts
        init_receipts_api(data_dir="/home/lancelot/data")
        if _receipts_api_module._receipt_service is not None:
            init_governance_receipts(_receipts_api_module._receipt_service)
        app.include_router(receipts_router)
        logger.info("Receipts API initialized.")
    except Exception as e:
        logger.warning(f"Receipts API initialization failed: {e}")

    # Compliance Export API
    try:
        from compliance.api import router as compliance_router, init_compliance_api
        from receipts_api import _receipt_service as _compliance_receipt_svc
        if _compliance_receipt_svc is not None:
            init_compliance_api(
                receipt_service=_compliance_receipt_svc,
                data_dir="/home/lancelot/data",
            )
            app.include_router(compliance_router)
            logger.info("Compliance Export API initialized.")
        else:
            logger.warning("Compliance Export API skipped: receipt service not available")
    except Exception as e:
        logger.warning(f"Compliance Export API initialization failed: {e}")

    # Governance + Trust + APL APIs — wire to existing subsystem instances
    _trust_ledger_inst = getattr(main_orchestrator, 'trust_ledger', None)
    _rule_engine_inst = None
    _decision_log_inst = None
    try:
        from governance.approval_learning.rule_engine import RuleEngine
        _rule_engine_inst = getattr(main_orchestrator, 'rule_engine', None)
        _decision_log_inst = getattr(main_orchestrator, 'decision_log', None)
    except ImportError:
        pass

    try:
        from governance_api import router as gov_router, init_governance_api
        init_governance_api(
            trust_ledger=_trust_ledger_inst,
            rule_engine=_rule_engine_inst,
            decision_log=_decision_log_inst,
            mcp_sentry=sentry,
        )
        app.include_router(gov_router)
        logger.info("Governance API initialized.")
    except Exception as e:
        logger.warning(f"Governance API initialization failed: {e}")

    try:
        from trust_api import router as trust_router, init_trust_api
        init_trust_api(trust_ledger=_trust_ledger_inst)
        app.include_router(trust_router)
        logger.info("Trust API initialized.")
    except Exception as e:
        logger.warning(f"Trust API initialization failed: {e}")

    try:
        from apl_api import router as apl_router, init_apl_api
        init_apl_api(rule_engine=_rule_engine_inst, decision_log=_decision_log_inst)
        app.include_router(apl_router)
        logger.info("APL API initialized.")
    except Exception as e:
        logger.warning(f"APL API initialization failed: {e}")

    # ===== TOOLS API =====
    try:
        from tools_api import router as tools_router, init_tools_api
        init_tools_api()
        app.include_router(tools_router)
        logger.info("Tools API initialized.")
    except Exception as e:
        logger.warning(f"Tools API initialization failed: {e}")

    # ===== FLAGS API =====
    try:
        from flags_api import router as flags_router, init_flags_api
        init_flags_api(audit_logger=main_orchestrator.audit_logger)
        app.include_router(flags_router)
        logger.info("Flags API initialized.")
    except Exception as e:
        logger.warning(f"Flags API initialization failed: {e}")

    # ===== V31: TOOL FLOW STREAMING + ACTION CARDS =====
    try:
        from feature_flags import FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS
        from event_bus import event_bus as _event_bus

        # Tool Flow Streaming — emitter injected into orchestrator
        if FEATURE_TOOL_FLOW_STREAMING:
            from toolflow.emitter import ToolFlowEmitter
            _toolflow_emitter = ToolFlowEmitter(event_bus=_event_bus, enabled=True)
            main_orchestrator.toolflow_emitter = _toolflow_emitter
            logger.info("ToolFlow streaming enabled — emitter injected into orchestrator")
        else:
            logger.info("ToolFlow streaming disabled by feature flag")

        # ActionCards — store, factory, resolver, API
        if FEATURE_ACTION_CARDS:
            from actioncard.store import ActionCardStore
            from actioncard.factory import ActionCardFactory
            from actioncard.resolver import ActionCardResolver
            from actioncard_api import router as actioncard_router, init_actioncard_api

            _ac_store = ActionCardStore(data_dir=main_orchestrator.data_dir)
            _ac_factory = ActionCardFactory(card_store=_ac_store, event_bus=_event_bus)
            _ac_resolver = ActionCardResolver(
                card_store=_ac_store,
                event_bus=_event_bus,
                receipt_service=main_orchestrator.receipt_service,
            )

            # Register approval handlers for each subsystem
            # Governance (sentry) handler
            try:
                from governance_api import _approve_item_direct, _deny_item_direct
                from src.core.operator_identity import OperatorIdentity

                def _gov_handler(item_id, button_id, **context):
                    identity = OperatorIdentity(
                        operator_id=context.get("operator_id", "") or "",
                        display_name=context.get("actor", "") or "",
                        session_id=context.get("session_id", "") or "",
                    )
                    if button_id == "approve":
                        result = _approve_item_direct(
                            item_id,
                            reason="Approved via ActionCard",
                            identity=identity if identity.operator_id and identity.display_name else None,
                        )
                        return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                    elif button_id in ("deny", "reject"):
                        result = _deny_item_direct(
                            item_id,
                            reason="Denied via ActionCard",
                            identity=identity if identity.operator_id and identity.display_name else None,
                        )
                        return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                    return {"status": "error", "message": f"Unknown button: {button_id}"}
                _ac_resolver.register_handler("governance", _gov_handler)
            except Exception as _e:
                logger.debug("Governance handler not available for ActionCards: %s", _e)

            # Scheduler handler
            try:
                if main_orchestrator.job_executor:
                    def _sched_handler(job_id, button_id, **context):
                        if button_id == "approve":
                            ok = main_orchestrator.job_executor.approve_job(
                                job_id,
                                operator_id=context.get("operator_id", "") or "",
                                session_id=context.get("session_id", "") or "",
                                actor=context.get("actor", "") or "",
                            )
                            return {"status": "approved" if ok else "error",
                                    "message": "Approved" if ok else "Not pending"}
                        return {"status": "denied", "message": "Denied"}
                    _ac_resolver.register_handler("scheduler", _sched_handler)
            except Exception as _e:
                logger.debug("Scheduler handler not available for ActionCards: %s", _e)

            # Soul handler
            try:
                from soul.api import _approve_proposal_direct, _reject_proposal_direct

                def _soul_handler(proposal_id, button_id, **context):
                    actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                    if button_id == "approve":
                        result = _approve_proposal_direct(proposal_id, actor=actor)
                        result["message"] = f"Soul proposal {proposal_id} approved via ActionCard"
                        return result
                    elif button_id in ("deny", "reject"):
                        result = _reject_proposal_direct(proposal_id, actor=actor)
                        result["message"] = f"Soul proposal {proposal_id} denied via ActionCard"
                        return result
                    return {"status": "error", "message": f"Unknown button: {button_id}"}
                _ac_resolver.register_handler("soul", _soul_handler)
            except Exception as _e:
                logger.debug("Soul handler not available for ActionCards: %s", _e)

            # Skills handler
            try:
                def _skills_handler(proposal_id, button_id, **context):
                    actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                    if button_id == "approve":
                        if main_orchestrator.skill_factory:
                            main_orchestrator.skill_factory.approve_proposal(
                                proposal_id,
                                approved_by=actor,
                            )
                            return {"status": "approved", "message": f"Skill proposal {proposal_id} approved"}
                        return {"status": "error", "message": "Skill factory not available"}
                    elif button_id in ("reject", "deny"):
                        if main_orchestrator.skill_factory:
                            main_orchestrator.skill_factory.reject_proposal(proposal_id)
                            return {"status": "denied", "message": f"Skill proposal {proposal_id} rejected"}
                        return {"status": "error", "message": "Skill factory not available"}
                    return {"status": "error", "message": f"Unknown button: {button_id}"}
                _ac_resolver.register_handler("skills", _skills_handler)
            except Exception as _e:
                logger.debug("Skills handler not available for ActionCards: %s", _e)

            init_actioncard_api(_ac_store, _ac_resolver)
            app.include_router(actioncard_router)

            # Store references for use by other subsystems
            app.state.actioncard_store = _ac_store
            app.state.actioncard_factory = _ac_factory
            app.state.actioncard_resolver = _ac_resolver

            # Wire ActionCard factory into approval subsystems
            try:
                from soul.api import init_soul_actioncards
                init_soul_actioncards(_ac_factory)
            except Exception as _e:
                logger.debug("Soul ActionCard wiring skipped: %s", _e)
            try:
                if main_orchestrator.skill_factory:
                    main_orchestrator.skill_factory.actioncard_factory = _ac_factory
            except Exception as _e:
                logger.debug("Skills ActionCard wiring skipped: %s", _e)
            # Wire ActionCard factory into orchestrator for sentry escalation cards
            main_orchestrator.actioncard_factory = _ac_factory

            logger.info("ActionCards enabled — store, factory, resolver, API initialized")
        else:
            logger.info("ActionCards disabled by feature flag")
    except Exception as e:
        logger.warning(f"V31 ToolFlow/ActionCards initialization failed: {e}")

    # ===== CONNECTORS SUBSYSTEM =====
    # Always mount the management API so War Room can list/configure connectors.
    # Connector registration in the runtime registry is gated by FEATURE_CONNECTORS.
    try:
        from connectors.registry import ConnectorRegistry
        from connectors.base import ConnectorStatus
        from connectors.vault import CredentialVault as ConnectorVault
        from connectors.runtime import ConnectorRuntime
        from connectors.credential_api import router as cred_router, init_credential_api
        from connectors_api import router as connectors_mgmt_router, init_connectors_api

        _connector_registry = ConnectorRegistry(config_path="config/connectors.yaml")
        _connector_vault = _boot_vault if _boot_vault else ConnectorVault(config_path="config/vault.yaml")

        # Register enabled connectors if FEATURE_CONNECTORS is on
        # V22: Validate credentials at registration — connectors without
        # credentials are registered but marked as NOT CONFIGURED.
        from feature_flags import FEATURE_CONNECTORS
        if FEATURE_CONNECTORS:
            _conn_config = _connector_registry._config.get("connectors", {})
            for _cid, _ccfg in _conn_config.items():
                if _ccfg.get("enabled", False):
                    try:
                        from connectors_api import register_connector_with_vault_access
                        from src.connectors.google_feature_gate import (
                            google_connector_disabled_reason,
                            is_google_connector_enabled,
                        )

                        _backend = _ccfg.get("backend")
                        if not is_google_connector_enabled(_cid, _backend):
                            logger.info(
                                "Skipping connector %s registration: %s",
                                _cid,
                                google_connector_disabled_reason(_cid, _backend),
                            )
                            continue

                        _conn = register_connector_with_vault_access(
                            _connector_registry,
                            _connector_vault,
                            _cid,
                            _ccfg,
                        )
                        if _conn:
                            if _conn.status == ConnectorStatus.CONFIGURED:
                                logger.info(f"Connector registered + configured: {_cid}")
                            else:
                                logger.warning(f"Connector registered but NOT configured (missing credentials): {_cid}")
                    except Exception as _e:
                        logger.warning(f"Failed to register connector {_cid}: {_e}")

        _connector_policy_engine = None
        try:
            from src.tools.fabric import get_tool_fabric
            _connector_policy_engine = getattr(get_tool_fabric(), "_policy_engine", None)
        except Exception as _e:
            logger.debug("Connector policy engine unavailable: %s", _e)

        # Mount management APIs even if governed execution wiring later degrades.
        init_credential_api(_connector_registry, _connector_vault)
        init_connectors_api(_connector_registry, _connector_vault)
        app.include_router(cred_router)
        app.include_router(connectors_mgmt_router)

        try:
            _connector_runtime = ConnectorRuntime(
                registry=_connector_registry,
                vault=_connector_vault,
                risk_classifier=getattr(main_orchestrator, "_risk_classifier", None),
                policy_engine=_connector_policy_engine,
                receipt_service=getattr(main_orchestrator, "receipt_service", None),
                trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
            )
            for _entry in _connector_registry.list_connectors():
                _connector_runtime.register_connector(_entry.manifest.id)

            main_orchestrator.connector_runtime = _connector_runtime
            if getattr(main_orchestrator, "task_runner", None) is not None:
                main_orchestrator.task_runner.connector_runtime = _connector_runtime
            app.state.connector_runtime = _connector_runtime
        except Exception as _e:
            logger.warning("Connector runtime degraded: %s", _e)

        # V22: Inject connector registry into orchestrator for dynamic capability reporting
        main_orchestrator._connector_registry = _connector_registry

        # Seed vault with current workspace path from docker-compose.yml
        if not _connector_vault.exists("shared_workspace.host_path"):
            try:
                import re as _re
                _compose_file = Path("/home/lancelot/app/docker-compose.yml")
                if _compose_file.exists():
                    _compose_text = _compose_file.read_text(encoding="utf-8")
                    _ws_match = _re.search(
                        r'-\s*["\']?(.+?):/home/lancelot/workspace',
                        _compose_text,
                    )
                    if _ws_match:
                        _ws_path = _ws_match.group(1).strip().strip('"').strip("'")
                        _connector_vault.store(
                            "shared_workspace.host_path", _ws_path, type="config",
                        )
                        logger.info("Seeded vault with workspace path: %s", _ws_path)
            except Exception as _e:
                logger.debug("Could not seed workspace path: %s", _e)

        logger.info("Connectors subsystem initialized (FEATURE_CONNECTORS=%s).", FEATURE_CONNECTORS)
    except Exception as e:
        logger.warning(f"Connectors initialization failed: {e}")

    # ===== MCP SUBSYSTEM =====
    try:
        from feature_flags import FEATURE_MCP
        if FEATURE_MCP:
            from src.mcp.api import init_mcp_api
            from src.mcp.argument_screen import MCPArgumentScreener
            from src.mcp.network_policy import MCPNetworkPolicy
            from src.mcp.permissions import MCPPermissionEvaluator
            from src.mcp.proxy import GovernedMCPProxy
            from src.mcp.receipts import MCPReceiptManager
            from src.mcp.registry import MCPServerRegistry
            from src.mcp.response_guard import MCPResponseGuard

            _mcp_vault = _connector_vault if '_connector_vault' in dir() else _boot_vault
            _mcp_registry = MCPServerRegistry(vault=_mcp_vault)
            _mcp_evaluator = MCPPermissionEvaluator()

            try:
                _mcp_soul = getattr(main_orchestrator, "soul", None)
                if _mcp_soul is not None:
                    if hasattr(_mcp_soul, "model_dump"):
                        _mcp_evaluator.load_from_soul(_mcp_soul.model_dump())
                    elif hasattr(_mcp_soul, "dict"):
                        _mcp_evaluator.load_from_soul(_mcp_soul.dict())
            except Exception as _mcp_soul_exc:
                logger.warning("MCP Soul permission load failed: %s", _mcp_soul_exc)

            _mcp_network_policy = MCPNetworkPolicy(
                network_interceptor=getattr(main_orchestrator, "network_interceptor", None),
            )

            _mcp_receipt_service = getattr(main_orchestrator, "receipt_service", None)
            _mcp_proxy = None
            if _mcp_receipt_service is not None:
                try:
                    _mcp_proxy = GovernedMCPProxy(
                        permission_evaluator=_mcp_evaluator,
                        registry=_mcp_registry,
                        receipt_manager=MCPReceiptManager(_mcp_receipt_service),
                        argument_screener=MCPArgumentScreener(
                            input_sanitizer=getattr(main_orchestrator, "sanitizer", None),
                        ),
                        response_guard=MCPResponseGuard(),
                        network_interceptor=getattr(main_orchestrator, "network_interceptor", None),
                        input_sanitizer=getattr(main_orchestrator, "sanitizer", None),
                    )
                except Exception as _mcp_proxy_exc:
                    logger.warning("MCP proxy initialization failed: %s", _mcp_proxy_exc)

            init_mcp_api(
                registry=_mcp_registry,
                evaluator=_mcp_evaluator,
                proxy=_mcp_proxy,
                vault=_mcp_vault,
                network_policy=_mcp_network_policy,
                receipt_service=_mcp_receipt_service,
            )
            logger.info("MCP subsystem initialized.")
    except Exception as e:
        logger.warning(f"MCP initialization failed: {e}")

    # ===== OAUTH TOKEN MANAGER (V28) =====
    try:
        from oauth_token_manager import OAuthTokenManager, set_oauth_manager
        from onboarding_snapshot import OnboardingState
        _oauth_vault = _connector_vault if '_connector_vault' in dir() else None
        if _oauth_vault:
            _oauth_mgr = OAuthTokenManager(vault=_oauth_vault)
            set_oauth_manager(_oauth_mgr)
            _oauth_mgr.start_background_refresh()
            logger.info("OAuth token manager initialized.")

            # V30: If OAuth token is now available and provider wasn't initialized
            # at module load (no API key at that point), re-init the provider.
            from oauth_token_manager import get_oauth_token as _get_oauth
            if main_orchestrator.provider is None and _get_oauth():
                logger.info("Re-initializing provider with OAuth token...")
                main_orchestrator._init_provider()
                if main_orchestrator.provider:
                    logger.info("Provider initialized via OAuth (post-startup recovery).")

            # V30: Update onboarding from oauth_pending if OAuth is connected
            if _get_oauth():
                try:
                    snap = onboarding_orch.snapshot
                    if snap.credential_status in ("oauth_pending", "none"):
                        snap.credential_status = "verified"
                        if snap.state != OnboardingState.READY:
                            snap.state = OnboardingState.READY
                        snap.save()
                        logger.info("Onboarding auto-updated to READY (OAuth token found).")
                except Exception as _e:
                    logger.warning("Onboarding OAuth recovery failed: %s", _e)
        else:
            logger.warning("OAuth token manager skipped — connector vault not available.")
    except Exception as e:
        logger.warning("OAuth token manager initialization failed: %s", e)

    # ===== OPENAI CODEX OAUTH TOKEN MANAGER =====
    try:
        from openai_codex_oauth_manager import OpenAICodexOAuthManager, set_openai_codex_manager
        _codex_vault = _connector_vault if '_connector_vault' in dir() else None
        if _codex_vault:
            _codex_mgr = OpenAICodexOAuthManager(vault=_codex_vault, port=1455)
            set_openai_codex_manager(_codex_mgr)
            _codex_mgr.start_background_refresh()
            logger.info("OpenAI Codex OAuth token manager initialized.")

            # If Codex OAuth token is now available and provider is openai-codex
            # but wasn't initialized at startup, re-init the provider.
            from openai_codex_oauth_manager import get_codex_oauth_token as _get_codex_token
            _current_provider = os.getenv("LANCELOT_PROVIDER", "gemini")
            if _current_provider == "openai-codex" and main_orchestrator.provider is None and _get_codex_token():
                logger.info("Re-initializing provider with Codex OAuth token...")
                main_orchestrator._init_provider()
                if main_orchestrator.provider:
                    logger.info("Provider initialized via Codex OAuth (post-startup recovery).")

            # Update onboarding from oauth_pending if Codex OAuth is connected
            if _current_provider == "openai-codex" and _get_codex_token():
                try:
                    from onboarding_snapshot import OnboardingState
                    snap = onboarding_orch.snapshot
                    if snap.credential_status in ("oauth_pending", "none"):
                        snap.credential_status = "verified"
                        if snap.state != OnboardingState.READY:
                            snap.state = OnboardingState.READY
                        snap.save()
                        logger.info("Onboarding auto-updated to READY (Codex OAuth token found).")
                except Exception as _e:
                    logger.warning("Onboarding Codex OAuth recovery failed: %s", _e)
        else:
            logger.warning("Codex OAuth token manager skipped — connector vault not available.")
    except Exception as e:
        logger.warning("Codex OAuth token manager initialization failed: %s", e)

    # ===== GOOGLE OAUTH MANAGER (V26) =====
    try:
        from google_oauth_manager import GoogleOAuthManager, set_google_oauth_manager
        from feature_flags import FEATURE_GOOGLE_OAUTH
        if FEATURE_GOOGLE_OAUTH and '_connector_vault' in dir() and _connector_vault:
            _google_mgr = GoogleOAuthManager(vault=_connector_vault)
            set_google_oauth_manager(_google_mgr)
            if _google_mgr.recover_from_vault():
                logger.info("Google OAuth tokens recovered on startup.")
            else:
                logger.info("Google OAuth: no existing tokens, awaiting user setup.")
        else:
            logger.info("Google OAuth disabled (FEATURE_GOOGLE_OAUTH=%s).", FEATURE_GOOGLE_OAUTH)
    except Exception as e:
        logger.warning("Google OAuth initialization failed: %s", e)

    # Start health monitoring after OAuth/provider recovery so the first
    # readiness snapshot reflects the settled provider state.
    if FEATURE_HEALTH_MONITOR:
        try:
            subsystem_manager.start("health_monitor")
        except Exception as e:
            logger.warning("Health monitor initialization failed: %s", e)

    # ===== AUTH API =====
    try:
        from src.core.auth_api import router as auth_router, init_auth_api
        init_auth_api(audit_logger=main_orchestrator.audit_logger)
        app.include_router(auth_router)
        logger.info("Auth API initialized.")
    except Exception as e:
        logger.warning(f"Auth API initialization failed: {e}")

    # ===== SETUP & RECOVERY API =====
    try:
        from setup_api import router as setup_router, init_setup_api
        from receipts_api import _receipt_service as _setup_receipt_svc
        init_setup_api(
            data_dir="/home/lancelot/data",
            startup_time=_startup_time or time.time(),
            audit_logger=main_orchestrator.audit_logger,
            connector_vault=_connector_vault if '_connector_vault' in dir() else None,
            receipt_service=_setup_receipt_svc,
            verify_request=verify_token,
        )
        app.include_router(setup_router)
        logger.info("Setup API initialized.")
    except Exception as e:
        logger.warning(f"Setup API initialization failed: {e}")

    # ===== UPDATE CHECKER + API =====
    try:
        from update_checker import UpdateChecker
        from update_api import router as update_router, init_update_api

        _update_checker = UpdateChecker()
        init_update_api(_update_checker)
        _update_checker.start()
        app.include_router(update_router)
        logger.info("Update checker started (version=%s).", _app_version)
    except Exception as e:
        logger.warning(f"Update checker initialization failed: {e}")

    # ===== PHASE 6b: USAGE TRACKER + PERSISTENCE =====
    try:
        from usage_tracker import UsageTracker
        from usage_persistence import UsagePersistence
        from src.core.control_plane import set_usage_tracker, set_usage_persistence

        _usage_persistence = UsagePersistence(data_dir="/home/lancelot/data")
        set_usage_persistence(_usage_persistence)

        if _bootstrap_model_router():
            logger.info("Usage tracker + model router initialized.")
        else:
            _usage_tracker = UsageTracker()
            _usage_tracker.set_persistence(_usage_persistence)
            set_usage_tracker(_usage_tracker)
            main_orchestrator.usage_tracker = _usage_tracker
            logger.info("Usage tracker + persistence initialized (router unavailable).")
    except Exception as e:
        logger.warning(f"Usage tracker initialization failed: {e}")

    # ===== PHASE 7: MODEL DISCOVERY + PROVIDER API =====
    try:
        from providers.api import router as provider_router, init_provider_api, load_persisted_config

        # If a persisted provider differs from current, hot-swap
        _persisted_config = load_persisted_config()
        _persisted_provider = _persisted_config.get("active_provider")
        if _persisted_provider and main_orchestrator.provider:
            _current_prov = main_orchestrator.provider.provider_name
            if _persisted_provider != _current_prov:
                try:
                    result_msg = main_orchestrator.switch_provider(_persisted_provider)
                    logger.info("Restored persisted provider: %s (%s)", _persisted_provider, result_msg)
                except Exception as _e:
                    logger.warning("Failed to restore persisted provider '%s': %s — keeping %s",
                                   _persisted_provider, _e, _current_prov)

        if not _bootstrap_model_discovery():
            init_provider_api(None, orchestrator=main_orchestrator)
            logger.warning("Provider not initialized — model discovery skipped")
        app.include_router(provider_router)
    except Exception as e:
        logger.warning(f"Model discovery initialization failed: {e}")

    # ===== V32: Telegram ToolFlow + ActionCard Bridges =====
    try:
        from feature_flags import FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS
        from event_bus import event_bus as _tg_event_bus

        # Wire ToolFlow progress streaming to Telegram
        if FEATURE_TOOL_FLOW_STREAMING and telegram_bot:
            from toolflow.telegram_bridge import TelegramProgressBridge
            _tg_bridge = TelegramProgressBridge(telegram_bot)
            _tg_event_bus.subscribe_all(_tg_bridge.on_toolflow_event)
            logger.info("Telegram ToolFlow progress bridge enabled")

        # Wire ActionCard events to Telegram
        if FEATURE_ACTION_CARDS and telegram_bot:
            _tg_event_bus.subscribe("actioncard_presented", telegram_bot._on_actioncard_event)
            _tg_event_bus.subscribe("actioncard_resolved", telegram_bot._on_actioncard_resolved_event)

            # Inject resolver and store references for callback handling
            if hasattr(app.state, "actioncard_resolver"):
                telegram_bot._action_card_resolver = app.state.actioncard_resolver
            if hasattr(app.state, "actioncard_store"):
                telegram_bot._action_card_store = app.state.actioncard_store

            logger.info("Telegram ActionCard event bridges enabled")
    except Exception as e:
        logger.warning(f"V32 Telegram event bridge initialization failed: {e}")

    # Start Communications Polling
    if telegram_bot:
        telegram_bot.start_polling()
        forge_dispatcher.register_platform(
            name="telegram",
            handler=lambda content: telegram_bot.send_message(
                telegram_bot._sanitize_for_telegram(content)
            ),
            mode="local"
        )
    elif chat_poller:
        chat_poller.start_polling()
        forge_dispatcher.register_platform(
            name="google_chat",
            handler=lambda content: chat_poller.send_message(content),
            mode="local"
        )
    
    # ===== Phase 2: SIGHUP Secret Reload Handler =====
    import platform as _plat
    if _plat.system() != "Windows":
        import signal
        def _sighup_handler(signum, frame):
            """Reload secrets from vault on SIGHUP (Linux/macOS only)."""
            global API_TOKEN
            try:
                if _boot_vault and secret_cache.is_bootstrapped():
                    changed = secret_cache.reload(_boot_vault)
                    changed_count = sum(1 for v in changed.values() if v)
                    if changed.get("LANCELOT_API_TOKEN"):
                        API_TOKEN = secret_cache.get("LANCELOT_API_TOKEN")
                    logger.info("SIGHUP: secrets reloaded (%d changed)", changed_count)
            except Exception as _e:
                logger.error("SIGHUP: secret reload failed: %s", _e)
        signal.signal(signal.SIGHUP, _sighup_handler)
        logger.info("SIGHUP handler registered for secret rotation.")

    # ===== OBSERVABILITY: OTel Export + Receipt Bridge + API =====
    try:
        from feature_flags import FEATURE_OBSERVABILITY
        if FEATURE_OBSERVABILITY:
            # Mount observability config API
            from observability.api import router as observability_router
            app.include_router(observability_router)
            logger.info("Observability API initialized.")
            from observability.config import load_config as _load_obs_config
            from observability.otel_provider import init_otel
            from observability.receipt_bridge import configure_bridge

            _obs_config = _load_obs_config()

            # Metrics API
            from observability.metrics_api import router as metrics_api_router, init_metrics_api
            try:
                from receipts_api import _receipt_service as _metrics_receipt_svc
                if _metrics_receipt_svc:
                    init_metrics_api(_metrics_receipt_svc, data_dir="/home/lancelot/data")
                    app.include_router(metrics_api_router)
                    logger.info("Metrics API initialized.")
            except Exception as _e:
                logger.warning("Metrics API initialization failed: %s", _e)

            # Webhooks
            if _obs_config.webhooks.enabled and _obs_config.webhooks.endpoints:
                from observability.webhook_engine import init_webhook_engine
                init_webhook_engine(
                    endpoints=_obs_config.webhooks.endpoints,
                    deployment_id=os.getenv("LANCELOT_DEPLOYMENT_ID", ""),
                    delivery_timeout_s=_obs_config.webhooks.delivery_timeout_s,
                    max_retries=_obs_config.webhooks.max_retries,
                    data_dir=main_orchestrator.data_dir,
                )
                logger.info("Webhook engine initialized (%d endpoints)",
                            len(_obs_config.webhooks.endpoints))

            # OTel
            if _obs_config.otel.enabled and _obs_config.otel.endpoint:
                _otel_ok = init_otel(
                    endpoint=_obs_config.otel.endpoint,
                    auth_header=_obs_config.otel.auth_header,
                    export_interval_ms=_obs_config.otel.export_interval_s * 1000,
                    resource_attributes=_obs_config.otel.resource_attributes,
                )
                configure_bridge(
                    enabled=_otel_ok,
                    sampling_rate=_obs_config.otel.sampling_rate_t0_t1,
                )
                if _otel_ok:
                    logger.info("OTel export initialized (endpoint=%s)", _obs_config.otel.endpoint)
                else:
                    logger.warning("OTel export initialization failed — bridge disabled")
            else:
                logger.info("FEATURE_OBSERVABILITY enabled but OTel exporter not configured")
    except Exception as e:
        logger.warning(f"Observability initialization failed: {e}")

    _optional_receipt_service = getattr(main_orchestrator, "receipt_service", None)

    # ── Time-Travel Debugging ────────────────────────────────────
    try:
        from feature_flags import FEATURE_TIME_TRAVEL
        if FEATURE_TIME_TRAVEL:
            from timetravel.api import router as timetravel_router, init_timetravel_api
            app.include_router(timetravel_router)

            # Initialize with receipt service and live Soul provider
            _tt_soul = lambda: getattr(main_orchestrator, "soul", None)

            def _apply_timetravel_modifications(graph_dict, modifications):
                for field_path, value in (modifications or {}).items():
                    parts = str(field_path).split(".")
                    cursor = graph_dict
                    for raw_part in parts[:-1]:
                        part = int(raw_part) if isinstance(cursor, list) and raw_part.isdigit() else raw_part
                        cursor = cursor[part]
                    leaf = parts[-1]
                    leaf_key = int(leaf) if isinstance(cursor, list) and leaf.isdigit() else leaf
                    cursor[leaf_key] = value
                return graph_dict

            def _execute_timetravel_quest(*, mode, source_quest_id, new_quest_id, modifications, operator_id, session_id):
                from datetime import datetime, timezone
                from src.core.tasking.schema import TaskGraph, TaskRun

                source_run = main_orchestrator.task_store.get_run_by_quest_id(source_quest_id)
                if source_run is None:
                    raise RuntimeError(f"Source quest is not replayable by TaskRun: {source_quest_id}")

                source_graph = main_orchestrator.task_store.get_graph(source_run.task_graph_id)
                if source_graph is None:
                    raise RuntimeError(
                        f"TaskGraph not found for source quest {source_quest_id}: {source_run.task_graph_id}"
                    )

                cloned_graph = source_graph.to_dict()
                cloned_graph["id"] = str(uuid.uuid4())
                cloned_graph["created_at"] = datetime.now(timezone.utc).isoformat()
                cloned_graph["session_id"] = session_id or source_graph.session_id or source_run.session_id

                if mode == "fork" and modifications:
                    cloned_graph = _apply_timetravel_modifications(cloned_graph, modifications)

                replay_graph = TaskGraph.from_dict(cloned_graph)
                main_orchestrator.task_store.save_graph(replay_graph)

                replay_run = TaskRun(
                    task_graph_id=replay_graph.id,
                    execution_token_id=source_run.execution_token_id,
                    session_id=session_id or source_run.session_id,
                    operator_id=operator_id or source_run.operator_id,
                    quest_id=new_quest_id,
                )
                main_orchestrator.task_store.create_run(replay_run)
                result = main_orchestrator.task_runner.run(replay_run.id)
                return {
                    "run_id": replay_run.id,
                    "task_graph_id": replay_graph.id,
                    "status": result.status,
                    "step_count": len(result.step_results),
                }

            if _optional_receipt_service is not None:
                init_timetravel_api(
                    receipt_service=_optional_receipt_service,
                    soul=_tt_soul,
                    soul_dir=None,
                    quest_executor=_execute_timetravel_quest,
                )
                logger.info("FEATURE_TIME_TRAVEL enabled — API mounted at /api/timetravel")
            else:
                logger.warning("Time-Travel: receipt service unavailable")
    except Exception as e:
        logger.warning(f"Time-Travel initialization failed: {e}")

    # ── A2A Protocol ────────────────────────────────────────────
    try:
        from feature_flags import FEATURE_A2A
        if FEATURE_A2A:
            from a2a.registry import A2ARegistry
            from a2a.server import a2a_server_router, init_a2a_server
            from a2a.api import router as a2a_api_router, init_a2a_api
            from a2a.inbound_pipeline import InboundPipeline
            from a2a.outbound_pipeline import OutboundPipeline
            from a2a.client import A2AClient
            from a2a.types import A2AArtifact, A2AMessagePart

            # Initialize registry
            _a2a_registry = A2ARegistry()

            # Load Soul for A2A permissions
            _a2a_soul_provider = lambda: getattr(main_orchestrator, "soul", None)

            _a2a_client = A2AClient(_optional_receipt_service)
            _a2a_vault = _connector_vault if '_connector_vault' in dir() else None

            # Initialize pipelines
            _a2a_inbound = InboundPipeline(
                _a2a_registry,
                _optional_receipt_service,
                _a2a_soul_provider,
                vault=_a2a_vault,
                a2a_client=_a2a_client,
            )
            _a2a_outbound = OutboundPipeline(
                _a2a_registry,
                _optional_receipt_service,
                _a2a_soul_provider,
                vault=_a2a_vault,
                a2a_client=_a2a_client,
            )

            def _execute_inbound_a2a_task(*, task, caller, quest_id):
                """Route inbound A2A work through the live orchestrator."""
                text_parts = []
                if task.message:
                    for part in task.message.parts:
                        if part.text:
                            text_parts.append(part.text)
                        elif part.data is not None:
                            text_parts.append(json.dumps(part.data, sort_keys=True))
                        elif part.file_uri:
                            text_parts.append(f"[file] {part.file_uri}")

                user_message = "\n".join(p for p in text_parts if p).strip()
                if not user_message:
                    raise ValueError("Inbound A2A task contained no executable content")

                envelope = (
                    f"[External A2A task from {caller.display_name or caller.agent_id}"
                    f" ({caller.agent_framework})]\n{user_message}"
                )
                response_text = main_orchestrator.chat(
                    envelope,
                    channel="api",
                    quest_id=quest_id,
                )
                artifacts = [
                    A2AArtifact(
                        parts=[A2AMessagePart(type="text", text=response_text)],
                        metadata={
                            "quest_id": quest_id,
                            "source": "lancelot",
                            "external_peer": caller.agent_id,
                        },
                    ).to_dict()
                ]
                return {
                    "status": "completed",
                    "artifacts": artifacts,
                    "message": "Task executed successfully.",
                }

            # Mount protocol-standard endpoints at root
            init_a2a_server(
                _a2a_soul_provider,
                _optional_receipt_service,
                _a2a_registry,
                _a2a_inbound,
                task_executor=_execute_inbound_a2a_task,
                data_dir="/home/lancelot/data",
            )
            app.include_router(a2a_server_router)

            # Mount management API
            init_a2a_api(_a2a_registry, _optional_receipt_service, _a2a_soul_provider, _a2a_outbound, _a2a_client)
            app.include_router(a2a_api_router)

            logger.info("FEATURE_A2A enabled — protocol at /a2a/, management at /api/a2a/")
    except Exception as e:
        logger.warning(f"A2A initialization failed: {e}")

    # ── Incident Response Playbooks ──────────────────────────────
    try:
        from feature_flags import FEATURE_INCIDENT_RESPONSE
        if FEATURE_INCIDENT_RESPONSE:
            from src.incidents.api import router as incidents_router, init_incidents_api
            from src.incidents.playbook_api import router as playbook_router, init_playbook_api
            from src.incidents.receipt_hook import configure as configure_incident_hook

            init_incidents_api(_optional_receipt_service, "/home/lancelot/data")
            app.include_router(incidents_router)

            _playbooks_dir = os.path.join(os.path.dirname(__file__), "..", "..", "playbooks")
            init_playbook_api(_playbooks_dir)
            app.include_router(playbook_router)

            configure_incident_hook(enabled=True, data_dir="/home/lancelot/data")

            logger.info("FEATURE_INCIDENT_RESPONSE enabled — API at /api/incidents/, /api/playbooks/")
    except Exception as e:
        logger.warning(f"Incident Response initialization failed: {e}")

    logger.info("Lancelot Gateway started.")


@app.on_event("shutdown")
async def shutdown_event():
    """F8: Graceful shutdown."""
    logger.info("Lancelot Gateway shutting down.")
    try:
        # Async shutdown for federation transport layer (must happen before subsystem_manager.stop_all)
        try:
            fed_entry = subsystem_manager._subsystems.get("federation")
            if fed_entry and fed_entry.running:
                fed_objs = fed_entry.objects or {}
                for _fname in ("cost_reporter", "heartbeat_mesh", "transport"):
                    _fobj = fed_objs.get(_fname)
                    if _fobj and hasattr(_fobj, "stop"):
                        try:
                            await _fobj.stop()
                        except Exception:
                            pass
        except Exception:
            pass

        # Stop all hot-toggleable subsystems (scheduler threads, health monitor, BAL DB, etc.)
        subsystem_manager.stop_all()

        librarian.stop()
        await antigravity.stop()
        if telegram_bot:
            telegram_bot.stop_polling()
        if chat_poller:
            chat_poller.stop_polling()
        # Flush usage persistence to disk
        try:
            if hasattr(main_orchestrator, 'usage_tracker') and main_orchestrator.usage_tracker:
                persistence = getattr(main_orchestrator.usage_tracker, '_persistence', None)
                if persistence:
                    persistence.flush()
        except Exception:
            pass
        # V28: Stop OAuth background refresh
        try:
            from oauth_token_manager import get_oauth_manager
            _oauth_mgr = get_oauth_manager()
            if _oauth_mgr:
                _oauth_mgr.stop_background_refresh()
        except Exception:
            pass
        # V26: Stop Google OAuth background refresh
        try:
            from google_oauth_manager import get_google_oauth_manager
            _google_mgr = get_google_oauth_manager()
            if _google_mgr:
                _google_mgr.stop_background_refresh()
        except Exception:
            pass
        # Observability: flush pending OTel spans and metrics
        try:
            from observability.otel_provider import shutdown_otel
            shutdown_otel()
        except Exception:
            pass
        main_orchestrator.audit_logger.log_event("GATEWAY_SHUTDOWN", "Graceful shutdown initiated")
    except Exception as e:
        logger.error(f"Shutdown error: {e}")


class ChatMessage(BaseModel):
    text: str
    user: str = "Unknown"


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
        data = await request.json()
        message = data.get("text", "")
        user = data.get("user", "Unknown")
        # V28: Allow clients to specify delivery channel for channel-aware output limits
        req_channel = data.get("channel", "warroom")

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
        data = await request.json()
        code = data.get("code")
        task_id = data.get("task_id", "default")
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


# ── Phase 2: Secret Rotation Endpoint ─────────────────────────────
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
                "error": "Vault not initialized — secret rotation unavailable",
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
        except Exception:
            pass

        logger.info("Secrets reloaded: %d changed", changed_count)
        return {"status": "ok", "changed_count": changed_count}
    except Exception as e:
        logger.error("Secret reload failed: %s", e)
        return JSONResponse(status_code=500, content={"error": "Reload failed"})


@app.get("/health")
def health_check():
    """F6: Enhanced health check with component status."""
    try:
        components = {
            "gateway": "ok",
            "orchestrator": "ok" if main_orchestrator.provider else "degraded",
            "sentry": "ok",
            "vault": "ok",
            "memory": "ok" if getattr(main_orchestrator, '_memory_enabled', False) else "disabled",
        }
        uptime = round(time.time() - _startup_time, 1) if _startup_time else 0
        return {
            "status": "online",
            "version": _app_version,
            "components": components,
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
    """F8: Readiness probe — checks all components are initialized."""
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


# Visual Receipt Helper (Simulated)
from receipt_service import ReceiptService
receipt_svc = ReceiptService()


@app.get("/receipt/{task_id}")
def get_receipt(task_id: str, request: Request):
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        path = receipt_svc.generate_receipt(task_id)
        return {"receipt_path": path, "message": "Receipt generated & sent to Chat.", "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/mcp_callback")
async def mcp_callback(request: Request):
    """
    Receives 'Approve' click from Google Chat Card.
    Payload: {"request_id": "...", "action": "APPROVE"}
    """
    req_request_id = make_request_id()
    auth_header = request.headers.get("authorization", "")
    webhook_authorized = webhook_auth.verify_remote_header(auth_header)
    if not webhook_authorized:
        authz_error = _require_request_capability(
            request, "governance.admin", request_id=req_request_id
        )
        if authz_error is not None:
            return authz_error
    try:
        data = await request.json()
        req_id = data.get("request_id")
        action = data.get("action")

        if action == "APPROVE":
            success = sentry.approve_request(req_id)
            if success:
                return {"status": "Request Approved. Agent resuming...", "request_id": req_request_id}
            else:
                return error_response(400, "Request ID not found or invalid.", request_id=req_request_id)
        return {"status": "Action ignored.", "request_id": req_request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=req_request_id)


# --- Forge of Innovation Endpoints ---

@app.post("/forge/discover")
async def forge_discover(request: Request):
    """
    Scrapes API documentation and generates a manifest + wrapper script.
    Payload: {"url": "https://... or raw doc text"}
    """
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request, "platform.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error
    try:
        data = await request.json()
        url_or_text = data.get("url", "")
        if not url_or_text:
            return error_response(400, "Missing 'url' field", request_id=request_id)

        doc_text = forge_discovery.scrape_docs(url_or_text)
        manifest = forge_discovery.generate_manifest(doc_text)
        script = forge_discovery.generate_wrapper_script(manifest)

        return {
            "manifest": manifest,
            "generated_script": script,
            "endpoint_count": len(manifest.get("endpoints", [])),
            "request_id": request_id,
        }
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/forge/dispatch")
async def forge_dispatch(request: Request):
    """
    Dispatches content to platforms based on tags in the prompt.
    Payload: {"content": "...", "prompt": "Post this [twitter:local:post]"}
    """
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request, "platform.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error
    try:
        data = await request.json()
        content = data.get("content", "")
        prompt = data.get("prompt", "")
        if not content:
            return error_response(400, "Missing 'content' field", request_id=request_id)

        results = forge_dispatcher.dispatch_from_prompt(prompt, content)
        return {"results": results, "dispatched_count": len(results), "request_id": request_id}
    except Exception as e:
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
        except Exception:
            pass
    finally:
        await session_mgr.close()


# --- UCP (Universal Commerce Protocol) ---

from ucp_connector import UCPConnector

ucp_connector = UCPConnector(audit_logger=main_orchestrator.audit_logger)


@app.post("/ucp/discover")
async def ucp_discover(request: Request):
    """Discovers UCP capabilities from a merchant URL."""
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request, "platform.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error
    try:
        data = await request.json()
        merchant_url = data.get("merchant_url", "")
        if not merchant_url:
            return error_response(400, "Missing 'merchant_url' field", request_id=request_id)

        manifest = ucp_connector.discover_merchant(merchant_url)
        return {"manifest": manifest, "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/ucp/search")
async def ucp_search(request: Request):
    """Searches products via a UCP-enabled merchant."""
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request, "platform.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error
    try:
        data = await request.json()
        merchant_url = data.get("merchant_url", "")
        query = data.get("query", "")
        if not merchant_url or not query:
            return error_response(400, "Missing 'merchant_url' or 'query' field", request_id=request_id)

        results = ucp_connector.search_products(merchant_url, query)
        return {"results": results, "result_count": len(results), "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/ucp/transact")
async def ucp_transact(request: Request):
    """Initiates a commerce transaction (requires Sentry approval)."""
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request, "platform.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error
    try:
        data = await request.json()
        merchant_url = data.get("merchant_url", "")
        product_id = data.get("product_id", "")
        params = data.get("params", {})
        if not merchant_url or not product_id:
            return error_response(400, "Missing 'merchant_url' or 'product_id' field", request_id=request_id)

        # Sentry permission check for UCP transactions (HIGH risk)
        perm = sentry.check_permission("ucp_transaction", {
            "merchant_url": merchant_url,
            "product_id": product_id,
        })
        if perm["status"] == "PENDING":
            return {
                "status": "pending_approval",
                "message": perm["message"],
                "sentry_request_id": perm["request_id"],
                "request_id": request_id,
            }
        elif perm["status"] == "DENIED":
            return error_response(403, perm["message"], request_id=request_id)

        result = ucp_connector.initiate_transaction(merchant_url, product_id, params)
        return {"transaction": result, "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/ucp/confirm")
async def ucp_confirm(request: Request):
    """Confirms a pending UCP transaction after user approval."""
    request_id = make_request_id()
    authz_error = _require_request_capability(
        request, "platform.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error
    try:
        data = await request.json()
        transaction_id = data.get("transaction_id", "")
        if not transaction_id:
            return error_response(400, "Missing 'transaction_id' field", request_id=request_id)

        result = ucp_connector.confirm_transaction(transaction_id)
        return {"result": result, "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


# --- V28: Anthropic OAuth Callback (unauthenticated — browser redirect) ---

@app.get("/callback")
async def oauth_anthropic_callback(request: Request):
    """Receive OAuth authorization code from browser redirect after user authorises."""
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        return render_callback_page("Authorization Failed", desc, status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return render_callback_page(
            "Missing Parameters",
            "No authorization code received.",
            status_code=400,
        )

    try:
        from oauth_token_manager import get_oauth_manager
        manager = get_oauth_manager()
        if manager is None:
            raise RuntimeError("OAuth manager not initialized")
        success = manager.exchange_code(code, state)
        if success:
            # V30: Re-init provider if it wasn't initialized at startup
            if main_orchestrator.provider is None:
                main_orchestrator._init_provider()
                if main_orchestrator.provider:
                    logger.info("Provider hot-initialized via OAuth callback.")

            # V31: Bootstrap model discovery so lanes appear in War Room
            _bootstrap_model_discovery()

            # V30: Update onboarding snapshot from oauth_pending → verified
            try:
                from onboarding_snapshot import OnboardingState
                snap = onboarding_orch.snapshot
                if snap.credential_status in ("oauth_pending", "none"):
                    snap.credential_status = "verified"
                    if snap.state != OnboardingState.READY:
                        snap.state = OnboardingState.READY
                    snap.save()
                    logger.info("Onboarding credential_status updated to verified (OAuth complete).")
            except Exception as _e:
                logger.warning("Could not update onboarding after OAuth: %s", _e)

            return HTMLResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                "<h2 style='color:#22c55e'>Authorization Successful</h2>"
                "<p>Lancelot is now connected to your Anthropic account via OAuth.</p>"
                "<p style='color:#888'>You may close this tab.</p>"
                "<script>setTimeout(function(){window.close()},3000)</script>"
                "</body></html>"
            )
        else:
            return render_callback_page(
                "Authorization Failed",
                "Invalid or expired authorization state. Please try again from Lancelot.",
                status_code=400,
            )
    except Exception as e:
        logger.error("OAuth callback error: %s", e)
        return render_callback_exception_page("OAuth")


# --- OpenAI Codex OAuth Callback (unauthenticated — browser redirect) ---

@app.get("/auth/callback")
async def oauth_codex_callback(request: Request):
    """Receive OAuth authorization code from browser redirect after user authorises with ChatGPT.

    Path must be /auth/callback to match OpenAI's registered redirect URI for the Codex public client.
    """
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        return render_callback_page("Authorization Failed", desc, status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return render_callback_page(
            "Missing Parameters",
            "No authorization code received.",
            status_code=400,
        )

    try:
        from openai_codex_oauth_manager import get_openai_codex_manager
        manager = get_openai_codex_manager()
        if manager is None:
            raise RuntimeError("Codex OAuth manager not initialized")
        success = manager.exchange_code(code, state)
        if success:
            # Re-init provider if it wasn't initialized at startup
            if main_orchestrator.provider is None or main_orchestrator._provider_name != "openai-codex":
                # Switch to openai-codex provider
                os.environ["LANCELOT_PROVIDER"] = "openai-codex"
                os.environ["LANCELOT_AUTH_MODE"] = "OAUTH"
                main_orchestrator._init_provider()
                if main_orchestrator.provider:
                    logger.info("Provider hot-initialized via Codex OAuth callback.")

            # Persist provider selection so restarts recover onto Codex OAuth.
            try:
                from providers.api import _read_current_config, _save_config, _update_env_file

                _update_env_file("LANCELOT_PROVIDER", "openai-codex")
                _update_env_file("LANCELOT_AUTH_MODE", "OAUTH")

                _provider_cfg = _read_current_config()
                _provider_cfg["active_provider"] = "openai-codex"
                _save_config(_provider_cfg)
            except Exception as _e:
                logger.warning("Could not persist Codex OAuth provider selection: %s", _e)

            # Bootstrap model discovery so lanes appear in War Room
            _bootstrap_model_discovery()

            # Update onboarding snapshot
            try:
                from onboarding_snapshot import OnboardingState
                snap = onboarding_orch.snapshot
                if snap.credential_status in ("oauth_pending", "none"):
                    snap.credential_status = "verified"
                    if snap.state != OnboardingState.READY:
                        snap.state = OnboardingState.READY
                    snap.save()
                    logger.info("Onboarding credential_status updated to verified (Codex OAuth complete).")
            except Exception as _e:
                logger.warning("Could not update onboarding after Codex OAuth: %s", _e)

            return HTMLResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                "<h2 style='color:#22c55e'>Authorization Successful</h2>"
                "<p>Lancelot is now connected to your ChatGPT account via Codex OAuth.</p>"
                "<p style='color:#888'>Your Pro subscription will be used for API access. You may close this tab.</p>"
                "<script>setTimeout(function(){window.close()},3000)</script>"
                "</body></html>"
            )
        else:
            return render_callback_page(
                "Authorization Failed",
                "Invalid or expired authorization state. Please try again from Lancelot.",
                status_code=400,
            )
    except Exception as e:
        logger.error("Codex OAuth callback error: %s", e)
        return render_callback_exception_page("Codex OAuth")


# --- V26: Google OAuth 2.0 Endpoints (Gmail + Calendar) ---

@app.post("/api/google-oauth/start")
async def google_oauth_start(request: Request):
    """Accept client_id + client_secret, store in vault, return Google consent URL."""
    request_id = str(uuid.uuid4())[:8]
    authz_error = _require_request_capability(
        request, "connectors.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error

    from feature_flags import FEATURE_GOOGLE_OAUTH
    if not FEATURE_GOOGLE_OAUTH:
        return error_response(
            403, "Google OAuth is disabled. Set FEATURE_GOOGLE_OAUTH=true.",
            request_id=request_id,
        )

    try:
        from google_oauth_manager import (
            GoogleOAuthManager, get_google_oauth_manager, set_google_oauth_manager,
        )
        manager = get_google_oauth_manager()
        # Lazy-init: if flag was toggled at runtime (via War Room), create manager now
        if not manager:
            try:
                from credential_api import _vault as _lazy_vault
                if _lazy_vault:
                    manager = GoogleOAuthManager(vault=_lazy_vault)
                    set_google_oauth_manager(manager)
                    logger.info("Google OAuth manager lazy-initialized (flag toggled at runtime)")
            except Exception as _e:
                logger.warning("Google OAuth lazy-init failed: %s", _e)
        if not manager:
            return error_response(500, "Google OAuth manager not initialized", request_id=request_id)

        data = await request.json()
        client_id = data.get("client_id", "").strip()
        client_secret = data.get("client_secret", "").strip()

        if not client_id or not client_secret:
            return error_response(400, "Both client_id and client_secret are required", request_id=request_id)

        auth_url = manager.generate_auth_url(client_id, client_secret)
        return {
            "auth_url": auth_url,
            "message": "Open this URL in your browser to authorize Gmail and Calendar access.",
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Google OAuth start error: %s", request_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@app.get("/google/callback")
async def google_oauth_callback(request: Request):
    """Receive Google OAuth authorization code from browser redirect.

    V26: Unauthenticated route — browser redirect from Google after consent.
    Protected by state nonce validation in exchange_code().
    """
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        return render_callback_page("Authorization Failed", desc, status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return render_callback_page(
            "Missing Parameters",
            "No authorization code received.",
            status_code=400,
        )

    try:
        from google_oauth_manager import get_google_oauth_manager
        manager = get_google_oauth_manager()
        if manager is None:
            raise RuntimeError("Google OAuth manager not initialized")
        success = manager.exchange_code(code, state)
        if success:
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                "<h2 style='color:#22c55e'>Google Authorization Successful</h2>"
                "<p>Lancelot is now connected to your Google account.</p>"
                "<p>Gmail and Calendar access is now active.</p>"
                "<p style='color:#888'>You may close this tab.</p>"
                "<script>setTimeout(function(){window.close()},3000)</script>"
                "</body></html>"
            )
        else:
            return render_callback_page(
                "Authorization Failed",
                "Invalid or expired authorization state. Please try again from Lancelot.",
                status_code=400,
            )
    except Exception as e:
        logger.error("Google OAuth callback error: %s", e)
        return render_callback_exception_page("Google OAuth")


@app.get("/api/google-oauth/status")
async def google_oauth_status(request: Request):
    """Return current Google OAuth token health."""
    request_id = str(uuid.uuid4())[:8]
    authz_error = _require_request_capability(
        request, "connectors.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error

    try:
        from google_oauth_manager import get_google_oauth_manager
        from feature_flags import FEATURE_GOOGLE_OAUTH
        manager = get_google_oauth_manager()
        if not manager:
            return {
                "status": "not_configured",
                "feature_enabled": FEATURE_GOOGLE_OAUTH,
                "request_id": request_id,
            }
        status = manager.get_status()
        status["feature_enabled"] = FEATURE_GOOGLE_OAUTH
        status["request_id"] = request_id
        return status
    except Exception as e:
        logger.error("[%s] Google OAuth status error: %s", request_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/api/google-oauth/revoke")
async def google_oauth_revoke(request: Request):
    """Revoke Google OAuth tokens and clear stored credentials."""
    request_id = str(uuid.uuid4())[:8]
    authz_error = _require_request_capability(
        request, "connectors.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error

    try:
        from google_oauth_manager import get_google_oauth_manager
        manager = get_google_oauth_manager()
        if not manager:
            return error_response(500, "Google OAuth manager not initialized", request_id=request_id)

        manager.revoke()
        return {
            "status": "revoked",
            "message": "All Google OAuth tokens have been cleared.",
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Google OAuth revoke error: %s", request_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


# --- V29: Workspace File Download Endpoint ---
# Serves documents from the workspace directory so chat can include download links

_WORKSPACE_ROOT = Path(os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace"))

@app.get("/api/files/{file_path:path}")
async def serve_workspace_file(file_path: str, request: Request):
    """Serve a file from the workspace for download.

    V29: Enables chat responses to include clickable download links
    for documents created by document_creator, research reports, etc.
    Path traversal is blocked — only files under the workspace directory are served.
    """
    if not verify_token(request):
        return error_response(401, "Unauthorized")

    # Resolve and validate path (block traversal)
    try:
        target = (_WORKSPACE_ROOT / file_path).resolve()
        if not str(target).startswith(str(_WORKSPACE_ROOT.resolve())):
            return error_response(403, "Path traversal blocked")
    except Exception:
        return error_response(400, "Invalid file path")

    if not target.is_file():
        return error_response(404, f"File not found: {file_path}")

    # Determine Content-Disposition (inline for viewable types, attachment for others)
    suffix = target.suffix.lower()
    inline_types = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".txt", ".md", ".csv", ".html"}
    disposition = "inline" if suffix in inline_types else "attachment"

    return FileResponse(
        path=str(target),
        filename=target.name,
        content_disposition_type=disposition,
    )


# --- War Room React SPA Static Mount ---

_warroom_dist = Path(__file__).resolve().parent.parent / "warroom" / "dist"

if _warroom_dist.is_dir():
    def _serve_warroom_index():
        """Serve index.html for the War Room SPA."""
        html = (_warroom_dist / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/war-room/{full_path:path}")
    async def warroom_spa(full_path: str):
        """Serve War Room SPA — serve static files or fall back to index.html for client-side routing."""
        file_path = _warroom_dist / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return _serve_warroom_index()

    @app.get("/war-room")
    async def warroom_root():
        """Redirect /war-room to /war-room/."""
        return _serve_warroom_index()

    logger.info("War Room SPA mounted at /war-room/ from %s", _warroom_dist)
else:
    logger.info("War Room SPA not found at %s — skipping mount", _warroom_dist)
