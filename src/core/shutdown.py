from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger("lancelot.gateway.shutdown")


def bind_gateway_globals(**kwargs):
    globals().update(kwargs)


@dataclass(frozen=True)
class ShutdownTask:
    """Reviewable shutdown step metadata for lifecycle cleanup."""
    name: str
    timeout_seconds: float


SUBSYSTEM_SHUTDOWN_MANIFEST: tuple[ShutdownTask, ...] = (
    ShutdownTask("federation", 10.0),
    ShutdownTask("hive", 10.0),
    ShutdownTask("uab_bridge", 5.0),
    ShutdownTask("host_bridge", 5.0),
    ShutdownTask("bal", 5.0),
    ShutdownTask("health_monitor", 5.0),
    ShutdownTask("scheduler", 5.0),
    ShutdownTask("skills", 5.0),
    ShutdownTask("soul", 5.0),
    ShutdownTask("memory", 5.0),
)


async def _run_sync_shutdown_step(name: str, timeout_seconds: float, fn) -> None:
    try:
        await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("Shutdown step '%s' exceeded timeout %.1fs", name, timeout_seconds)
    except Exception as exc:
        logger.warning("Shutdown step '%s' failed: %s", name, exc)


async def _shutdown_federation_transport() -> None:
    fed_entry = subsystem_manager.get("federation")
    if not fed_entry or not fed_entry.running:
        return

    fed_objs = fed_entry.objects or {}
    for component_name in ("cost_reporter", "heartbeat_mesh", "transport"):
        component = fed_objs.get(component_name)
        if component and hasattr(component, "stop"):
            try:
                await component.stop()
            except Exception as exc:
                logger.warning(
                    "Federation component %s stop failed during shutdown: %s",
                    component_name,
                    exc,
                )


async def shutdown(app, boot_result):
    logger.info("Lancelot Gateway shutting down.")

    try:
        await _run_sync_shutdown_step(
            "federation_transport_pre_stop",
            10.0,
            lambda: asyncio.run(_shutdown_federation_transport()),
        )

        for task in SUBSYSTEM_SHUTDOWN_MANIFEST:
            entry = subsystem_manager.get(task.name)
            if entry and entry.running:
                await _run_sync_shutdown_step(
                    f"subsystem:{task.name}",
                    task.timeout_seconds,
                    lambda task_name=task.name: subsystem_manager.stop(task_name),
                )

        await _run_sync_shutdown_step("librarian", 5.0, librarian.stop)
        await _run_sync_shutdown_step("antigravity", 10.0, lambda: asyncio.run(antigravity.stop()))

        if telegram_bot:
            await _run_sync_shutdown_step("telegram_bot", 5.0, telegram_bot.stop_polling)
        if chat_poller:
            await _run_sync_shutdown_step("chat_poller", 5.0, chat_poller.stop_polling)

        def _flush_usage_persistence() -> None:
            if hasattr(main_orchestrator, "usage_tracker") and main_orchestrator.usage_tracker:
                persistence = getattr(main_orchestrator.usage_tracker, "_persistence", None)
                if persistence:
                    persistence.flush()

        await _run_sync_shutdown_step("usage_persistence_flush", 5.0, _flush_usage_persistence)

        def _stop_anthropic_oauth_refresh() -> None:
            from oauth_token_manager import get_oauth_manager

            oauth_manager = get_oauth_manager()
            if oauth_manager:
                oauth_manager.stop_background_refresh()

        await _run_sync_shutdown_step(
            "anthropic_oauth_refresh",
            5.0,
            _stop_anthropic_oauth_refresh,
        )

        def _stop_google_oauth_refresh() -> None:
            from google_oauth_manager import get_google_oauth_manager

            google_manager = get_google_oauth_manager()
            if google_manager:
                google_manager.stop_background_refresh()

        await _run_sync_shutdown_step(
            "google_oauth_refresh",
            5.0,
            _stop_google_oauth_refresh,
        )

        def _shutdown_otel() -> None:
            from observability.otel_provider import shutdown_otel

            shutdown_otel()

        await _run_sync_shutdown_step("otel", 5.0, _shutdown_otel)
        main_orchestrator.audit_logger.log_event("GATEWAY_SHUTDOWN", "Graceful shutdown initiated")
    except Exception as exc:
        logger.error("Shutdown error: %s", exc)
