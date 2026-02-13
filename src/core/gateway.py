from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, File, UploadFile, Form
from fastapi.responses import JSONResponse, FileResponse
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
import logging

# F1: Configurable log level
LOG_LEVEL = os.getenv("LANCELOT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger("lancelot.gateway")

# S11: Request size limit (20 MB for file uploads)
MAX_REQUEST_SIZE = 20_971_520

# F8: Startup timestamp for uptime tracking
_startup_time = None


# F2: Structured error response helper
def error_response(status_code: int, message: str, detail: str = None, request_id: str = None) -> JSONResponse:
    content = {"error": message, "status": status_code}
    if detail:
        content["detail"] = detail
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=content)


class RateLimiter:
    """S11: Sliding-window rate limiter per IP address."""

    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = {}  # ip -> [timestamps]

    def check(self, ip: str) -> bool:
        """Returns True if request is allowed."""
        now = time.time()
        if ip not in self._requests:
            self._requests[ip] = []
        self._requests[ip] = [t for t in self._requests[ip] if t > now - self.window]
        if len(self._requests[ip]) >= self.max_requests:
            return False
        self._requests[ip].append(now)
        return True


app = FastAPI()

# S11: CORS middleware
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501,http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Authentication ---
API_TOKEN = os.getenv("LANCELOT_API_TOKEN")


def verify_token(request: Request) -> bool:
    """Validates Bearer token from Authorization header."""
    if not API_TOKEN:
        logger.warning(
            "SECURITY: Gateway running in dev mode — no authentication token configured. "
            "Set LANCELOT_API_TOKEN for production."
        )
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return hmac.compare_digest(auth_header[7:], API_TOKEN)
    return False


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

if COMMS_TYPE == "telegram":
    telegram_bot = TelegramBot(orchestrator=main_orchestrator)
    logger.info("Comms backend: Telegram")
else:
    chat_poller = ChatPoller(data_dir="/home/lancelot/data", orchestrator=main_orchestrator)
    logger.info("Comms backend: Google Chat")


@app.on_event("startup")
async def startup_event():
    global _startup_time
    _startup_time = time.time()

    # F8: Validate environment on startup
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        logger.warning("No GEMINI_API_KEY or GOOGLE_APPLICATION_CREDENTIALS set. LLM features may be unavailable.")
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

    # ===== PHASE 2: MEMORY vNEXT =====
    try:
        from feature_flags import FEATURE_MEMORY_VNEXT
        if FEATURE_MEMORY_VNEXT:
            from pathlib import Path
            from memory.store import CoreBlockStore
            from memory.sqlite_store import MemoryStoreManager
            from memory.compiler import ContextCompilerService
            from memory.api import router as memory_router

            mem_data_dir = Path("/home/lancelot/data")
            core_store = CoreBlockStore(data_dir=mem_data_dir)
            core_store.initialize()
            # Bootstrap human block from USER.md if it exists
            user_md = mem_data_dir / "USER.md"
            if user_md.exists():
                core_store.bootstrap_from_user_file(str(user_md))

            store_manager = MemoryStoreManager(data_dir=mem_data_dir)
            compiler_svc = ContextCompilerService(
                data_dir=mem_data_dir,
                core_store=core_store,
                memory_manager=store_manager,
            )

            app.include_router(memory_router)
            # Wire memory into orchestrator
            main_orchestrator._memory_enabled = True
            main_orchestrator.context_compiler = compiler_svc
            logger.info("Memory vNext initialized and wired.")
        else:
            logger.info("Memory vNext disabled by feature flag.")
    except Exception as e:
        logger.error(f"Memory vNext initialization failed: {e}")
        main_orchestrator._memory_enabled = False

    # ===== PHASE 3: SOUL SYSTEM =====
    try:
        from feature_flags import FEATURE_SOUL
        if FEATURE_SOUL:
            from soul.store import load_active_soul, SoulStoreError
            try:
                from soul.api import router as soul_router
                active_soul = load_active_soul()

                # BAL: Apply composable soul overlays if FEATURE_BAL is enabled
                try:
                    from feature_flags import FEATURE_BAL
                    if FEATURE_BAL:
                        from soul.layers import load_overlays, merge_soul
                        overlays = load_overlays()
                        if overlays:
                            active_soul = merge_soul(active_soul, overlays)
                            logger.info("Soul overlays applied: %s",
                                        [o.overlay_name for o in overlays])
                except Exception as exc:
                    logger.warning(f"Soul overlay loading failed: {exc} — using base soul")

                main_orchestrator.soul = active_soul
                app.include_router(soul_router)
                logger.info(f"Soul loaded: version={active_soul.version}")
            except SoulStoreError as exc:
                logger.error(f"Soul load failed: {exc} — running without soul constraints")
            except Exception as exc:
                logger.warning(f"Soul subsystem not available: {exc}")
        else:
            logger.info("Soul disabled by feature flag.")
    except ImportError:
        logger.info("Soul module not available.")

    # ===== PHASE 4: SCHEDULER + SKILLS =====
    _scheduler_service = None
    _skill_executor = None
    job_executor = None
    try:
        from feature_flags import FEATURE_SKILLS
        if FEATURE_SKILLS:
            from skills.registry import SkillRegistry
            from skills.executor import SkillExecutor
            skill_registry = SkillRegistry(data_dir="/home/lancelot/data")
            # Fix Pack V5: Register builtins so executor can find them
            from skills.registry import SkillEntry, SkillOwnership
            for builtin_name in ("echo", "command_runner", "repo_writer", "service_runner", "network_client"):
                if not skill_registry.get_skill(builtin_name):
                    skill_registry._skills[builtin_name] = SkillEntry(
                        name=builtin_name, version="1.0.0",
                        enabled=True, ownership=SkillOwnership.SYSTEM,
                    )
            skill_registry._save()
            _skill_executor = SkillExecutor(registry=skill_registry)
            main_orchestrator.skill_executor = _skill_executor
            # Fix Pack V5: Also wire into TaskRunner (created before gateway sets skill_executor)
            if main_orchestrator.task_runner:
                main_orchestrator.task_runner.skill_executor = _skill_executor
            logger.info(f"Skills initialized: {len(skill_registry.list_skills())} skills")
    except Exception as e:
        logger.warning(f"Skills initialization failed: {e}")

    try:
        from feature_flags import FEATURE_SCHEDULER
        if FEATURE_SCHEDULER:
            from scheduler.service import SchedulerService
            _scheduler_service = SchedulerService(
                data_dir="/home/lancelot/data/scheduler",
                config_dir="config",
            )
            count = _scheduler_service.register_from_config()
            main_orchestrator.scheduler_service = _scheduler_service
            logger.info(f"Scheduler initialized: {count} jobs registered")
            # Connect job executor if skills available
            if _skill_executor:
                from scheduler.executor import JobExecutor
                job_executor = JobExecutor(
                    scheduler_service=_scheduler_service,
                    skill_execute_fn=lambda name, inputs: _skill_executor.run(name, inputs),
                )
                main_orchestrator.job_executor = job_executor
                logger.info("Job executor wired to skill executor.")
    except Exception as e:
        logger.warning(f"Scheduler initialization failed: {e}")

    # ===== SCHEDULER API =====
    try:
        from scheduler_api import router as scheduler_router, init_scheduler_api
        if _scheduler_service:
            init_scheduler_api(
                service=_scheduler_service,
                executor=job_executor,
            )
            app.include_router(scheduler_router)
            logger.info("Scheduler API initialized.")
    except Exception as e:
        logger.warning(f"Scheduler API initialization failed: {e}")

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
        logger.warning(f"Local model client initialization failed: {e}")

    # ===== PHASE 5: HEALTH MONITOR =====
    try:
        from feature_flags import FEATURE_HEALTH_MONITOR
        if FEATURE_HEALTH_MONITOR:
            from health.monitor import HealthMonitor, HealthCheck
            from health.api import router as health_api_router, set_snapshot_provider
            checks = [
                HealthCheck(
                    name="gemini_client",
                    check_fn=lambda: main_orchestrator.client is not None,
                    degraded_reason="Gemini client not initialized",
                ),
                HealthCheck(
                    name="onboarding_ready",
                    check_fn=lambda: onboarding_orch._determine_state() == "READY",
                    degraded_reason="Onboarding not complete",
                ),
            ]
            if _scheduler_service:
                checks.append(HealthCheck(
                    name="scheduler",
                    check_fn=lambda: _scheduler_service is not None,
                    degraded_reason="Scheduler not available",
                ))
            health_monitor = HealthMonitor(checks=checks, interval_s=30.0)
            health_monitor.start_monitor()
            set_snapshot_provider(lambda: health_monitor.latest_snapshot)
            app.include_router(health_api_router)
            logger.info("Health monitor started.")
    except Exception as e:
        logger.warning(f"Health monitor initialization failed: {e}")

    # ===== PHASE 6: CONTROL PLANE =====
    try:
        from control_plane import init_control_plane
        from control_plane import router as cp_router
        init_control_plane(data_dir="/home/lancelot/data")
        app.include_router(cp_router)
        logger.info("Control plane initialized.")
    except Exception as e:
        logger.warning(f"Control plane initialization failed: {e}")

    # ===== WAR ROOM APIs =====
    # Receipts API
    try:
        from receipts_api import router as receipts_router, init_receipts_api
        init_receipts_api(data_dir="/home/lancelot/data")
        app.include_router(receipts_router)
        logger.info("Receipts API initialized.")
    except Exception as e:
        logger.warning(f"Receipts API initialization failed: {e}")

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
        from flags_api import router as flags_router
        app.include_router(flags_router)
        logger.info("Flags API initialized.")
    except Exception as e:
        logger.warning(f"Flags API initialization failed: {e}")

    # ===== CONNECTORS SUBSYSTEM =====
    # Always mount the management API so War Room can list/configure connectors.
    # Connector registration in the runtime registry is gated by FEATURE_CONNECTORS.
    try:
        from connectors.registry import ConnectorRegistry
        from connectors.vault import CredentialVault as ConnectorVault
        from connectors.credential_api import router as cred_router, init_credential_api
        from connectors_api import router as connectors_mgmt_router, init_connectors_api

        _connector_registry = ConnectorRegistry(config_path="config/connectors.yaml")
        _connector_vault = ConnectorVault(config_path="config/vault.yaml")

        # Register enabled connectors if FEATURE_CONNECTORS is on
        from feature_flags import FEATURE_CONNECTORS
        if FEATURE_CONNECTORS:
            _conn_config = _connector_registry._config.get("connectors", {})
            for _cid, _ccfg in _conn_config.items():
                if _ccfg.get("enabled", False):
                    try:
                        from connectors_api import _instantiate_connector
                        _conn = _instantiate_connector(_cid, _ccfg)
                        if _conn:
                            _connector_registry.register(_conn)
                            logger.info(f"Connector registered: {_cid}")
                    except Exception as _e:
                        logger.warning(f"Failed to register connector {_cid}: {_e}")

        init_credential_api(_connector_registry, _connector_vault)
        init_connectors_api(_connector_registry, _connector_vault)
        app.include_router(cred_router)
        app.include_router(connectors_mgmt_router)
        logger.info("Connectors subsystem initialized (FEATURE_CONNECTORS=%s).", FEATURE_CONNECTORS)
    except Exception as e:
        logger.warning(f"Connectors initialization failed: {e}")

    # ===== BUSINESS AUTOMATION LAYER (BAL) =====
    try:
        from feature_flags import FEATURE_BAL
        if FEATURE_BAL:
            from bal.config import load_bal_config
            from bal.database import BALDatabase
            from bal.receipts import emit_bal_receipt

            _bal_config = load_bal_config()
            _bal_db = BALDatabase(data_dir=_bal_config.bal_data_dir)

            # Attach to orchestrator for later use by BAL subsystems
            main_orchestrator._bal_config = _bal_config
            main_orchestrator._bal_db = _bal_db

            # Emit startup receipt
            emit_bal_receipt(
                event_type="client",
                action_name="bal_startup",
                inputs={
                    "phase": "1_foundation",
                    "intake_enabled": _bal_config.bal_intake,
                    "repurpose_enabled": _bal_config.bal_repurpose,
                    "delivery_enabled": _bal_config.bal_delivery,
                    "billing_enabled": _bal_config.bal_billing,
                },
            )
            logger.info(
                "BAL initialized: intake=%s, repurpose=%s, delivery=%s, billing=%s",
                _bal_config.bal_intake, _bal_config.bal_repurpose,
                _bal_config.bal_delivery, _bal_config.bal_billing,
            )
        else:
            logger.info("BAL disabled by feature flag.")
    except Exception as e:
        logger.warning(f"BAL initialization failed: {e}")

    # ===== PHASE 6b: USAGE TRACKER + PERSISTENCE =====
    try:
        from usage_tracker import UsageTracker
        from usage_persistence import UsagePersistence
        from control_plane import set_usage_tracker, set_usage_persistence

        _usage_persistence = UsagePersistence(data_dir="/home/lancelot/data")
        _usage_tracker = UsageTracker()
        _usage_tracker.set_persistence(_usage_persistence)

        set_usage_tracker(_usage_tracker)
        set_usage_persistence(_usage_persistence)

        # Wire into orchestrator so every LLM call is recorded
        main_orchestrator.usage_tracker = _usage_tracker
        logger.info("Usage tracker + persistence initialized.")
    except Exception as e:
        logger.warning(f"Usage tracker initialization failed: {e}")

    # Start Communications Polling
    if telegram_bot:
        telegram_bot.start_polling()
        forge_dispatcher.register_platform(
            name="telegram",
            handler=lambda content: telegram_bot.send_message(content),
            mode="local"
        )
    elif chat_poller:
        chat_poller.start_polling()
        forge_dispatcher.register_platform(
            name="google_chat",
            handler=lambda content: chat_poller.send_message(content),
            mode="local"
        )
    
    logger.info("Lancelot Gateway started.")


@app.on_event("shutdown")
async def shutdown_event():
    """F8: Graceful shutdown."""
    logger.info("Lancelot Gateway shutting down.")
    try:
        librarian.stop()
        await antigravity.stop()
        if telegram_bot:
            telegram_bot.stop_polling()
        if chat_poller:
            chat_poller.stop_polling()
        # Stop health monitor if running
        try:
            if 'health_monitor' in dir() and health_monitor:
                health_monitor.stop_monitor()
        except Exception:
            pass
        # Flush usage persistence to disk
        try:
            if hasattr(main_orchestrator, 'usage_tracker') and main_orchestrator.usage_tracker:
                persistence = getattr(main_orchestrator.usage_tracker, '_persistence', None)
                if persistence:
                    persistence.flush()
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
        data = await request.json()
        message = data.get("text", "")
        user = data.get("user", "Unknown")

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
                    response_text = crusader_mode.activate()
                    main_orchestrator.audit_logger.log_event(
                        "CRUSADER_MODE_ACTIVATED",
                        "User activated Crusader Mode",
                        user
                    )
                else:
                    response_text = crusader_mode.deactivate()
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
                        message, crusader_mode=True
                    )
                    response_text = crusader_adapter.format_response(
                        response_text
                    )
            else:
                # Standard mode
                response_text = main_orchestrator.chat(message)

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

        logger.info(f"[{request_id}] Upload from {user}: text={text[:50]}... files={len(attachments)}")

        # Route through onboarding/crusader/orchestrator
        onboarding_orch.state = onboarding_orch._determine_state()
        if onboarding_orch.state != "READY":
            response_text = onboarding_orch.process(user, text)
        else:
            is_trigger, action = crusader_mode.should_intercept(text)
            if is_trigger:
                if action == "activate":
                    response_text = crusader_mode.activate()
                else:
                    response_text = crusader_mode.deactivate()
            elif crusader_mode.is_active:
                if crusader_adapter.check_auto_pause(text):
                    response_text = "Authority required.\nThis operation is restricted even in Crusader Mode."
                else:
                    response_text = main_orchestrator.chat(text, crusader_mode=True, attachments=attachments)
                    response_text = crusader_adapter.format_response(response_text)
            else:
                response_text = main_orchestrator.chat(text, attachments=attachments)

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
        data = await request.json()
        code = data.get("code")
        task_id = data.get("task_id", "default")
        
        logger.info(f"[{request_id}] MFA Code Received for Task {task_id}")
        
        success = mfa_guard.submit_code(task_id, code)
        
        if success:
            return {"status": "Code Accepted. Bridge Released.", "request_id": request_id}
        else:
            return error_response(404, "Unknown Task ID or no pending challenge.", request_id=request_id)
            
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.get("/health")
def health_check():
    """F6: Enhanced health check with component status."""
    try:
        components = {
            "gateway": "ok",
            "orchestrator": "ok" if main_orchestrator.client else "degraded",
            "sentry": "ok",
            "vault": "ok",
            "memory": "ok" if getattr(main_orchestrator, '_memory_enabled', False) else "disabled",
        }
        uptime = round(time.time() - _startup_time, 1) if _startup_time else 0
        return {
            "status": "online",
            "version": "8.0",
            "components": components,
            "crusader_mode": crusader_mode.is_active,
            "uptime_seconds": uptime,
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
        "orchestrator": "ok" if main_orchestrator.client else "degraded",
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
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    if crusader_mode.is_active:
        return {"status": "already_active", **crusader_mode.get_status()}
    response_text = crusader_mode.activate()
    main_orchestrator.audit_logger.log_event(
        "CRUSADER_MODE_ACTIVATED",
        "User activated Crusader Mode via API",
        "Commander"
    )
    return {"status": "activated", "message": response_text, **crusader_mode.get_status()}


@app.post("/api/crusader/deactivate")
def api_crusader_deactivate(request: Request):
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    if not crusader_mode.is_active:
        return {"status": "already_inactive", **crusader_mode.get_status()}
    response_text = crusader_mode.deactivate()
    main_orchestrator.audit_logger.log_event(
        "CRUSADER_MODE_DEACTIVATED",
        "User deactivated Crusader Mode via API",
        "Commander"
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
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=req_request_id)
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
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
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
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
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
    Auth via query param: ws://host:8000/live?token=<LANCELOT_API_TOKEN>
    """
    await websocket.accept()

    # Auth check via query param (deprecated — tokens in URLs are logged)
    token = websocket.query_params.get("token", "")
    if token:
        logger.warning(
            "SECURITY: WebSocket auth via URL query parameter is deprecated. "
            "Token may appear in server logs and browser history."
        )
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
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
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
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
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
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
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
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        transaction_id = data.get("transaction_id", "")
        if not transaction_id:
            return error_response(400, "Missing 'transaction_id' field", request_id=request_id)

        result = ucp_connector.confirm_transaction(transaction_id)
        return {"result": result, "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


# --- War Room React SPA Static Mount ---

_warroom_dist = Path(__file__).resolve().parent.parent / "warroom" / "dist"

if _warroom_dist.is_dir():
    @app.get("/war-room/{full_path:path}")
    async def warroom_spa(full_path: str):
        """Serve War Room SPA — serve static files or fall back to index.html for client-side routing."""
        file_path = _warroom_dist / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_warroom_dist / "index.html")

    @app.get("/war-room")
    async def warroom_root():
        """Redirect /war-room to /war-room/."""
        return FileResponse(_warroom_dist / "index.html")

    logger.info("War Room SPA mounted at /war-room/ from %s", _warroom_dist)
else:
    logger.info("War Room SPA not found at %s — skipping mount", _warroom_dist)
