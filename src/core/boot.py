"""Gateway boot sequence and subsystem lifecycle registration."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

from boot_actioncards import init_toolflow_actioncards
from boot_connectors import init_connector_runtime
from boot_core_apis import init_core_api_surfaces
from boot_routes import mount_startup_routers
from boot_spine_extensions import register_spine_extension_subsystems
from boot_subsystems import start_core_subsystems

logger = logging.getLogger("lancelot.gateway.boot")

_PROVIDER_CREDENTIAL_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex": "CODEX_OAUTH",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "local-openai": "LOCAL_OPENAI_BASE_URL",
}

def bind_gateway_globals(**kwargs):
    globals().update(kwargs)

@dataclass
class BootEnvironment:
    provider: str = ""
    credential_var: str = ""
    api_token_configured: bool = False
    startup_time: float = 0.0

@dataclass
class BootCore:
    started_steps: list[str] = field(default_factory=list)
    optional_failures: list[str] = field(default_factory=list)

@dataclass
class BootResult:
    core: BootCore
    env: BootEnvironment


@dataclass
class BootConfig:
    api_token: str | None = None
    app_version: str = ""
    boot_vault: object | None = None
    verify_token: object | None = None
    secret_cache: object | None = None


def _publish_local_model_runtime_status(main_orchestrator) -> None:
    """Publish local model and role readiness after policy initialization."""
    from src.core.model_usage_policy import (
        set_local_model_availability,
        set_local_model_roles_status,
    )

    local_model = getattr(main_orchestrator, "local_model", None)
    if local_model is not None:
        try:
            local_health = local_model.health()
            local_ready = bool(local_health.get("ready"))
            local_loaded = bool(local_health.get("loaded", local_ready))
            local_reason = local_health.get("last_error") or (
                "Local model ready" if local_ready else "Local model not ready"
            )
            set_local_model_availability(
                local_ready,
                local_reason,
                loaded=local_loaded,
                ready=local_ready,
                last_verified_at=local_health.get("last_verified_at"),
                last_checked_at=local_health.get("last_checked_at"),
                last_error=local_health.get("last_error"),
                consecutive_failures=local_health.get("consecutive_failures"),
                last_smoke_elapsed_ms=local_health.get("last_smoke_elapsed_ms"),
            )
            if local_ready:
                logger.info("Local model connected and ready")
            else:
                logger.warning("Local model loaded but not ready: %s", local_reason)
        except Exception as local_exc:
            set_local_model_availability(
                False,
                f"Local model readiness check failed: {local_exc}",
                loaded=False,
                ready=False,
                last_error=str(local_exc),
            )
            logger.warning("Local model readiness check failed: %s", local_exc)

    local_roles = getattr(main_orchestrator, "local_model_roles", None)
    if local_roles is not None:
        try:
            set_local_model_roles_status(local_roles.status())
        except Exception as role_status_exc:
            logger.warning("Local model role status check failed: %s", role_status_exc)


@dataclass(frozen=True)
class BootTask:
    """Reviewable boot step metadata for the gateway composition root."""
    name: str
    criticality: str
    dependencies: tuple[str, ...] = ()


BOOT_MANIFEST: tuple[BootTask, ...] = (
    BootTask("event_bus_loop_capture", "critical"),
    BootTask("environment_validation", "critical", ("event_bus_loop_capture",)),
    BootTask("core_runtime_services", "critical", ("environment_validation",)),
    BootTask("orchestrator_runtime_wiring", "critical", ("core_runtime_services",)),
    BootTask("feature_flag_snapshot", "optional", ("orchestrator_runtime_wiring",)),
    BootTask("shared_api_auth", "optional", ("orchestrator_runtime_wiring",)),
    BootTask("runtime_soul_refresh_hooks", "optional", ("orchestrator_runtime_wiring",)),
    BootTask("subsystem_router_mounts", "optional", ("shared_api_auth",)),
    BootTask("subsystem_registration", "critical", ("subsystem_router_mounts",)),
    BootTask("core_subsystem_startup", "critical", ("subsystem_registration",)),
    BootTask("local_model_boot", "optional", ("core_subsystem_startup",)),
    BootTask("control_plane_api", "optional", ("core_subsystem_startup",)),
    BootTask("war_room_apis", "optional", ("control_plane_api",)),
    BootTask("tools_and_flags_apis", "optional", ("war_room_apis",)),
    BootTask("connector_runtime", "optional", ("tools_and_flags_apis",)),
    BootTask("mcp_subsystem", "optional", ("connector_runtime",)),
    BootTask("oauth_managers", "optional", ("connector_runtime",)),
    BootTask("health_monitor", "optional", ("oauth_managers",)),
    BootTask("setup_and_recovery_apis", "optional", ("health_monitor",)),
    BootTask("usage_tracking", "optional", ("setup_and_recovery_apis",)),
    BootTask("provider_discovery", "optional", ("usage_tracking",)),
    BootTask("channel_bridges", "optional", ("provider_discovery",)),
    BootTask("secret_reload_handler", "optional", ("provider_discovery",)),
    BootTask("observability", "optional", ("provider_discovery",)),
    BootTask("time_travel", "optional", ("observability",)),
    BootTask("a2a_protocol", "optional", ("time_travel",)),
    BootTask("incident_response", "optional", ("a2a_protocol",)),
)


def _capture_event_bus_loop() -> None:
    """Bind cross-thread event publishing to the active FastAPI loop."""
    try:
        from event_bus import event_bus as _eb

        _eb.set_loop(asyncio.get_running_loop())
    except Exception as exc:
        logger.debug("Event bus loop capture skipped during startup: %s", exc)


def _codex_auth_available() -> bool:
    """Return True when the container can see mounted Codex CLI auth."""
    try:
        from providers.codex_cli_client import has_codex_cli_auth

        return has_codex_cli_auth()
    except Exception as exc:
        logger.debug("Codex CLI auth availability check failed during boot validation: %s", exc)
        return False


def _validate_boot_environment(api_token: str | None) -> BootEnvironment:
    """Validate required boot environment and return the operator-facing summary."""
    provider = (os.getenv("LANCELOT_PROVIDER", "gemini").strip().lower() or "gemini")
    key_var = _PROVIDER_CREDENTIAL_VARS.get(provider, "")
    if provider == "openai-codex":
        if _codex_auth_available():
            logger.info("OpenAI Codex provider credential source available: mounted Codex CLI auth.")
        else:
            logger.info("OpenAI Codex provider selected; waiting for Codex OAuth or mounted CLI auth.")
    elif key_var:
        has_google_oauth = provider == "gemini" and bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        if not os.getenv(key_var) and not has_google_oauth:
            logger.warning("No %s set. LLM features may be unavailable.", key_var)
    else:
        logger.warning("Unknown LANCELOT_PROVIDER=%s. Provider initialization may fail.", provider)
    if not api_token:
        logger.warning("LANCELOT_API_TOKEN not set. Running in dev mode (no auth required).")
    return BootEnvironment(
        provider=provider,
        credential_var=key_var,
        api_token_configured=bool(api_token),
    )


async def _start_core_runtime_services() -> None:
    """Start gateway-owned services that critical subsystems depend on."""
    librarian.start()
    try:
        from feature_flags import FEATURE_TOOLS_ANTIGRAVITY
    except Exception as exc:
        FEATURE_TOOLS_ANTIGRAVITY = False
        logger.warning("Antigravity feature flag lookup failed; skipping browser startup: %s", exc)

    if FEATURE_TOOLS_ANTIGRAVITY:
        await antigravity.start()
    else:
        logger.info("Antigravity browser startup skipped (FEATURE_TOOLS_ANTIGRAVITY=false).")


def _wire_orchestrator_runtime_services() -> None:
    """Attach long-lived gateway services to the orchestrator runtime."""
    main_orchestrator.sentry = sentry
    main_orchestrator.mfa_guard = mfa_guard
    main_orchestrator.antigravity = antigravity


def _log_boot_feature_flags() -> None:
    try:
        from feature_flags import log_feature_flags

        log_feature_flags()
    except Exception as exc:
        logger.warning("Feature flag logging failed: %s", exc)


def _init_shared_api_auth() -> None:
    try:
        from src.core.api_auth import init_api_auth

        init_api_auth(verify_token)
    except Exception as exc:
        logger.warning("Shared API auth initialization failed: %s", exc)


def _build_runtime_soul_applier(app):
    """Create the callback that refreshes runtime policy after Soul activation."""
    def _apply_runtime_soul(active_soul):
        main_orchestrator.refresh_soul_policy(active_soul)

        try:
            from src.a2a.agent_card import invalidate_card

            invalidate_card()
        except Exception as exc:
            logger.warning("A2A agent card invalidation failed after Soul update: %s", exc)

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
            hive_entry = subsystem_manager.get("hive")
            lifecycle = hive_entry.objects.get("lifecycle") if hive_entry and hive_entry.running else None
            if lifecycle is not None:
                lifecycle.update_parent_soul(active_soul)
        except Exception as exc:
            logger.warning("HIVE Soul refresh failed: %s", exc)

        app.state.active_soul = active_soul

        try:
            federation_entry = subsystem_manager.get("federation")
            emitter = federation_entry.objects.get("emitter") if federation_entry and federation_entry.running else None
            if emitter is not None:
                emitter.emit_once()
        except Exception as exc:
            logger.warning("Federation heartbeat Soul refresh failed: %s", exc)

    return _apply_runtime_soul


async def boot(app, config):
        global _startup_time
        _startup_time = time.time()
        _boot_env = _validate_boot_environment(API_TOKEN)
        _boot_env.startup_time = _startup_time

        _capture_event_bus_loop()
        await _start_core_runtime_services()
        _wire_orchestrator_runtime_services()
        _log_boot_feature_flags()
        _init_shared_api_auth()

        _apply_runtime_soul = _build_runtime_soul_applier(app)
        app.state.apply_runtime_soul = _apply_runtime_soul
    
        try:
            from soul.api import init_soul_runtime
            init_soul_runtime(_apply_runtime_soul)
        except Exception as e:
            logger.warning("Soul runtime refresh initialization failed: %s", e)

        mount_startup_routers(app, logger=logger)

        start_core_subsystems(
            app,
            subsystem_manager=subsystem_manager,
            main_orchestrator=main_orchestrator,
            logger=logger,
            init_memory=_init_memory,
            shutdown_memory=_shutdown_memory,
            init_soul=_init_soul,
            shutdown_soul=_shutdown_soul,
            init_skills=_init_skills,
            shutdown_skills=_shutdown_skills,
            init_scheduler=_init_scheduler,
            shutdown_scheduler=_shutdown_scheduler,
            init_health_monitor=_init_health_monitor,
            shutdown_health_monitor=_shutdown_health_monitor,
            init_host_bridge=_init_host_bridge,
            shutdown_host_bridge=_shutdown_host_bridge,
            init_uab=_init_uab,
            shutdown_uab=_shutdown_uab,
            init_hive=_init_hive,
            shutdown_hive=_shutdown_hive,
            init_federation=_init_federation,
            shutdown_federation=_shutdown_federation,
        )

        try:
            from feature_flags import FEATURE_LOCAL_AGENTIC
            from src.core.local_model_roles import LocalModelRoleRouter
            from local_model_client import LocalModelClient
    
            _local_model = LocalModelClient()
            main_orchestrator.local_model = _local_model
            _local_model_roles = LocalModelRoleRouter.from_env()
            main_orchestrator.local_model_roles = _local_model_roles
            _publish_local_model_runtime_status(main_orchestrator)
    
            if not FEATURE_LOCAL_AGENTIC:
                logger.info("Local execution feature disabled; local model remains installed for scrub and health checks")
        except Exception as e:
            try:
                from src.core.model_usage_policy import set_local_model_availability
    
                set_local_model_availability(
                    False,
                    f"Local model initialization failed: {e}",
                    loaded=False,
                    ready=False,
                    last_error=str(e),
                )
            except Exception as availability_exc:
                logger.warning(
                    "Failed to publish local model initialization failure to usage policy state: %s",
                    availability_exc,
                )
            logger.warning("Local model client initialization failed: %s", e)
    
        init_core_api_surfaces(
            app,
            main_orchestrator=main_orchestrator,
            subsystem_manager=subsystem_manager,
            sentry=sentry,
            logger=logger,
            publish_local_model_runtime_status=_publish_local_model_runtime_status,
        )
    
        wire_actioncard_telegram_runtime = init_toolflow_actioncards(
            app,
            main_orchestrator=main_orchestrator,
            subsystem_manager=subsystem_manager,
            telegram_bot=telegram_bot,
            logger=logger,
        )
        connector_state = init_connector_runtime(
            app,
            main_orchestrator=main_orchestrator,
            boot_vault=_boot_vault,
            logger=logger,
        )
        _connector_vault = connector_state.vault
        _connector_vault_error = connector_state.error
    
        try:
            from feature_flags import FEATURE_MCP

            def _init_mcp_subsystem():
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
                return {
                    "registry": _mcp_registry,
                    "evaluator": _mcp_evaluator,
                    "proxy": _mcp_proxy,
                    "vault": _mcp_vault,
                    "network_policy": _mcp_network_policy,
                    "receipt_service": _mcp_receipt_service,
                }

            def _shutdown_mcp_subsystem(objects):
                from src.mcp.api import shutdown_mcp_api

                shutdown_mcp_api()

            subsystem_manager.register(
                "mcp",
                "FEATURE_MCP",
                _init_mcp_subsystem,
                _shutdown_mcp_subsystem,
                ["/api/mcp"],
            )
            if FEATURE_MCP:
                subsystem_manager.start("mcp")
        except Exception as e:
            logger.warning(f"MCP initialization failed: {e}")
    
        try:
            from oauth_token_manager import OAuthTokenManager, set_oauth_manager
            from onboarding_snapshot import OnboardingState
            _oauth_vault = _connector_vault if '_connector_vault' in dir() else None
            if _oauth_vault:
                _oauth_mgr = OAuthTokenManager(vault=_oauth_vault)
                set_oauth_manager(_oauth_mgr)
                _oauth_mgr.start_background_refresh()
                logger.info("OAuth token manager initialized.")
    
                # Recover the provider when OAuth becomes available after import-time boot.
                from oauth_token_manager import get_oauth_token as _get_oauth
                if main_orchestrator.provider is None and _get_oauth():
                    logger.info("Re-initializing provider with OAuth token...")
                    main_orchestrator.initialize_provider()
                    if main_orchestrator.provider:
                        logger.info("Provider initialized via OAuth (post-startup recovery).")
    
                # Advance onboarding if a valid OAuth token is already present.
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
                logger.warning("OAuth token manager skipped; connector vault not available.")
        except Exception as e:
            logger.warning("OAuth token manager initialization failed: %s", e)
    
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
                    main_orchestrator.initialize_provider()
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
                logger.warning("Codex OAuth token manager skipped; connector vault not available.")
        except Exception as e:
            logger.warning("Codex OAuth token manager initialization failed: %s", e)
    
        try:
            from google_oauth_manager import GoogleOAuthManager, set_google_oauth_manager
            from feature_flags import FEATURE_GOOGLE_OAUTH

            def _init_google_oauth_subsystem():
                if '_connector_vault' not in dir() or not _connector_vault:
                    raise RuntimeError("Connector vault not available for Google OAuth")
                _google_mgr = GoogleOAuthManager(vault=_connector_vault)
                set_google_oauth_manager(_google_mgr)
                if _google_mgr.recover_from_vault():
                    logger.info("Google OAuth tokens recovered on startup.")
                else:
                    logger.info("Google OAuth: no existing tokens, awaiting user setup.")
                return {"manager": _google_mgr}

            def _shutdown_google_oauth_subsystem(objects):
                manager = objects.get("manager")
                if manager is not None and hasattr(manager, "stop_background_refresh"):
                    manager.stop_background_refresh()
                set_google_oauth_manager(None)
                logger.info("Google OAuth manager stopped.")

            subsystem_manager.register(
                "google_oauth",
                "FEATURE_GOOGLE_OAUTH",
                _init_google_oauth_subsystem,
                _shutdown_google_oauth_subsystem,
                [],
            )
            if FEATURE_GOOGLE_OAUTH:
                subsystem_manager.start("google_oauth")
            else:
                logger.info("Google OAuth disabled (FEATURE_GOOGLE_OAUTH=%s).", FEATURE_GOOGLE_OAUTH)
        except Exception as e:
            logger.warning("Google OAuth initialization failed: %s", e)
    
        # Start health monitoring after OAuth/provider recovery so the first
        # readiness snapshot reflects the settled provider state.
        from feature_flags import FEATURE_HEALTH_MONITOR

        if FEATURE_HEALTH_MONITOR:
            try:
                subsystem_manager.start("health_monitor")
            except Exception as e:
                logger.warning("Health monitor initialization failed: %s", e)
    
        try:
            from src.core.auth_api import router as auth_router, init_auth_api
            init_auth_api(audit_logger=main_orchestrator.audit_logger)
            app.include_router(auth_router)
            logger.info("Auth API initialized.")
        except Exception as e:
            logger.warning(f"Auth API initialization failed: {e}")
    
        try:
            from setup_api import router as setup_router, init_setup_api
            from receipts_api import _receipt_service as _setup_receipt_svc
            init_setup_api(
                data_dir="/home/lancelot/data",
                startup_time=_startup_time or time.time(),
                audit_logger=main_orchestrator.audit_logger,
                connector_vault=_connector_vault if '_connector_vault' in dir() else None,
                connector_vault_error=_connector_vault_error,
                connector_vault_config_path="config/vault.yaml",
                receipt_service=_setup_receipt_svc,
                verify_request=verify_token,
            )
            app.include_router(setup_router)
            logger.info("Setup API initialized.")
        except Exception as e:
            logger.warning(f"Setup API initialization failed: {e}")
    
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
    
        try:
            from providers.api import router as provider_router, init_provider_api, load_persisted_config
    
            # If a persisted provider exists, restore it even when boot env selected
            # a provider with no credentials and left the orchestrator uninitialized.
            _persisted_config = load_persisted_config()
            _persisted_provider = _persisted_config.get("active_provider")
            if _persisted_provider:
                _restore_persisted_provider(_persisted_provider, main_orchestrator)
    
            if not _bootstrap_model_discovery():
                init_provider_api(None, orchestrator=main_orchestrator)
                logger.warning("Provider not initialized; model discovery skipped")
            app.include_router(provider_router)
        except Exception as e:
            logger.warning(f"Model discovery initialization failed: {e}")
    
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
                wire_actioncard_telegram_runtime(
                    resolver=getattr(app.state, "actioncard_resolver", None),
                    store=getattr(app.state, "actioncard_store", None),
                )
    
                logger.info("Telegram ActionCard event bridges enabled")
        except Exception as e:
            logger.warning("Telegram event bridge initialization failed: %s", e)
    
        # Start Communications Polling
        if telegram_bot:
            telegram_bot.start_polling()
            forge_dispatcher.register_platform(
                name="telegram",
                handler=lambda content: telegram_bot.send_message(
                    telegram_bot.sanitize_for_telegram(content)
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
    
        try:
            from boot_observability_support import (
                init_observability_runtime,
                shutdown_observability,
            )
            from feature_flags import FEATURE_OBSERVABILITY

            def _init_observability_subsystem():
                return init_observability_runtime(
                    main_orchestrator=main_orchestrator,
                    logger=logger,
                )

            def _shutdown_observability_subsystem(objects):
                shutdown_observability(objects, logger=logger)

            subsystem_manager.register(
                "observability",
                "FEATURE_OBSERVABILITY",
                _init_observability_subsystem,
                _shutdown_observability_subsystem,
                ["/api/observability", "/api/metrics"],
            )
            if FEATURE_OBSERVABILITY:
                subsystem_manager.start("observability")
        except Exception as e:
            logger.warning(f"Observability initialization failed: {e}")
    
        register_spine_extension_subsystems(
            main_orchestrator=main_orchestrator,
            subsystem_manager=subsystem_manager,
            logger=logger,
            connector_vault=_connector_vault,
        )
        logger.info("Lancelot Gateway started.")
        return BootResult(
            core=BootCore(started_steps=["gateway_boot"]),
            env=BootEnvironment(
                provider=_boot_env.provider,
                credential_var=_boot_env.credential_var,
                api_token_configured=_boot_env.api_token_configured,
                startup_time=_boot_env.startup_time,
            ),
        )
