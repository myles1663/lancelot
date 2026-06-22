"""Core subsystem registration and startup helpers for gateway boot."""

from __future__ import annotations


def start_core_subsystems(
    app,
    *,
    subsystem_manager,
    main_orchestrator,
    logger,
    init_memory,
    shutdown_memory,
    init_soul,
    shutdown_soul,
    init_skills,
    shutdown_skills,
    init_scheduler,
    shutdown_scheduler,
    init_health_monitor,
    shutdown_health_monitor,
    init_host_bridge,
    shutdown_host_bridge,
    init_uab,
    shutdown_uab,
    init_hive,
    shutdown_hive,
    init_federation,
    shutdown_federation,
) -> None:
    """Register and start the core feature-flagged subsystems."""
    subsystem_manager.register("memory", "FEATURE_MEMORY_VNEXT", init_memory, shutdown_memory, ["/memory"])
    subsystem_manager.register("soul", "FEATURE_SOUL", init_soul, shutdown_soul, ["/soul"])
    subsystem_manager.register("skills", "FEATURE_SKILLS", init_skills, shutdown_skills, [])
    subsystem_manager.register("scheduler", "FEATURE_SCHEDULER", init_scheduler, shutdown_scheduler, ["/api/scheduler"])
    subsystem_manager.register(
        "health_monitor",
        "FEATURE_HEALTH_MONITOR",
        init_health_monitor,
        shutdown_health_monitor,
        ["/health"],
    )
    subsystem_manager.register("host_bridge", "FEATURE_TOOLS_HOST_BRIDGE", init_host_bridge, shutdown_host_bridge, [])
    subsystem_manager.register("uab_bridge", "FEATURE_TOOLS_UAB", init_uab, shutdown_uab, [])
    subsystem_manager.register("hive", "FEATURE_HIVE", init_hive, shutdown_hive, ["/api/hive"])
    subsystem_manager.register(
        "federation",
        "FEATURE_FEDERATION",
        init_federation,
        shutdown_federation,
        ["/api/federation"],
    )

    from feature_flags import (
        FEATURE_FEDERATION,
        FEATURE_HIVE,
        FEATURE_MEMORY_VNEXT,
        FEATURE_SCHEDULER,
        FEATURE_SKILLS,
        FEATURE_SOUL,
        FEATURE_TOOLS_HOST_BRIDGE,
        FEATURE_TOOLS_UAB,
    )

    if FEATURE_MEMORY_VNEXT:
        try:
            subsystem_manager.start("memory")
        except Exception as exc:
            logger.error("Structured memory initialization failed: %s", exc)
            main_orchestrator.set_memory_enabled(False)
    else:
        logger.info("Structured memory disabled by feature flag.")

    if FEATURE_SOUL:
        try:
            subsystem_manager.start("soul")
        except Exception as exc:
            logger.warning("Soul initialization failed: %s", exc)
    else:
        logger.info("Soul disabled by feature flag.")

    if FEATURE_SKILLS:
        try:
            subsystem_manager.start("skills")
            from skills_api import init_skills_api, router as skills_api_router

            init_skills_api(
                factory=main_orchestrator.skill_factory,
                registry=main_orchestrator.skill_registry,
                executor=main_orchestrator.skill_executor,
            )
            app.include_router(skills_api_router)
            logger.info("Skills API initialized.")
        except Exception as exc:
            logger.warning("Skills initialization failed: %s", exc)

    if FEATURE_SCHEDULER:
        try:
            subsystem_manager.start("scheduler")
        except Exception as exc:
            logger.warning("Scheduler initialization failed: %s", exc)

    # Tool Fabric registers these providers at initialization; avoid double-init.
    if FEATURE_TOOLS_HOST_BRIDGE:
        entry = subsystem_manager.get("host_bridge")
        if entry and not entry.running:
            entry.running = True
            logger.info("Host Bridge provider marked running (booted at init)")
    if FEATURE_TOOLS_UAB:
        entry = subsystem_manager.get("uab_bridge")
        if entry and not entry.running:
            entry.running = True
            logger.info("UAB Bridge provider marked running (booted at init)")

    if FEATURE_HIVE:
        try:
            subsystem_manager.start("hive")
        except Exception as exc:
            logger.warning("HIVE Agent Mesh initialization failed: %s", exc)
    else:
        logger.info("HIVE Agent Mesh disabled by feature flag.")

    if FEATURE_FEDERATION:
        try:
            subsystem_manager.start("federation")
        except Exception as exc:
            logger.warning("Federation initialization failed: %s", exc)
    else:
        logger.info("Federation disabled by feature flag.")
