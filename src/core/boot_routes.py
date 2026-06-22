"""Startup router mounting helpers for the gateway boot coordinator."""

from __future__ import annotations


def mount_startup_routers(app, *, logger) -> None:
    """Mount optional API routers during boot without stopping startup."""
    try:
        from memory.api import router as memory_router
        app.include_router(memory_router)
    except Exception as exc:
        logger.warning("Memory API router mount failed: %s", exc)

    try:
        from soul.api import router as soul_router
        app.include_router(soul_router)
    except Exception as exc:
        logger.warning("Soul API router mount failed: %s", exc)

    try:
        from soul.template_api import router as template_router
        app.include_router(template_router)
    except Exception as exc:
        logger.warning("Soul Template API router mount failed: %s", exc)

    try:
        from scheduler_api import router as scheduler_router
        app.include_router(scheduler_router)
    except Exception as exc:
        logger.warning("Scheduler API router mount failed: %s", exc)

    try:
        from health.api import router as health_api_router
        app.include_router(health_api_router)
    except Exception as exc:
        logger.warning("Health API router mount failed: %s", exc)

    try:
        from src.hive.api import router as hive_router
        app.include_router(hive_router)
    except Exception as exc:
        logger.warning("HIVE Agent Mesh API router mount failed: %s", exc)

    try:
        from src.federation.api import router as federation_router
        app.include_router(federation_router)
    except Exception as exc:
        logger.warning("Federation API router mount failed: %s", exc)

    try:
        from src.federation.graph_api import graph_router
        app.include_router(graph_router)
    except Exception as exc:
        logger.warning("Graph Builder API router mount failed: %s", exc)

    try:
        from src.mcp.api import router as mcp_router
        app.include_router(mcp_router)
    except Exception as exc:
        logger.warning("MCP API router mount failed: %s", exc)

    try:
        from actioncard_api import router as actioncard_router
        app.include_router(actioncard_router)
    except Exception as exc:
        logger.warning("ActionCard API router mount failed: %s", exc)

    try:
        from procedural_recommendations_api import router as procedural_recommendations_router
        app.include_router(procedural_recommendations_router)
    except Exception as exc:
        logger.warning("Procedural recommendations API router mount failed: %s", exc)

    try:
        from boot_observability_support import mount_observability_routers
        mount_observability_routers(app, logger=logger)
    except Exception as exc:
        logger.warning("Observability router mount failed: %s", exc)

    try:
        from timetravel.api import router as timetravel_router
        app.include_router(timetravel_router)
    except Exception as exc:
        logger.warning("Time-Travel API router mount failed: %s", exc)

    try:
        from a2a.server import a2a_server_router
        from a2a.api import router as a2a_api_router
        app.include_router(a2a_server_router)
        app.include_router(a2a_api_router)
    except Exception as exc:
        logger.warning("A2A API router mount failed: %s", exc)

    try:
        from src.incidents.api import router as incidents_router
        from src.incidents.playbook_api import router as playbook_router
        app.include_router(incidents_router)
        app.include_router(playbook_router)
    except Exception as exc:
        logger.warning("Incident Response API router mount failed: %s", exc)
