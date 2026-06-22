"""Core API surface initialization for gateway boot."""

from __future__ import annotations


def init_core_api_surfaces(
    app,
    *,
    main_orchestrator,
    subsystem_manager,
    sentry,
    logger,
    publish_local_model_runtime_status,
) -> None:
    """Initialize core runtime, governance, receipt, and factory APIs."""
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
            hive_entry = subsystem_manager.get("hive")
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
        publish_local_model_runtime_status(main_orchestrator)
        logger.info("Control plane initialized.")
    except Exception as exc:
        logger.warning("Control plane initialization failed: %s", exc)

    try:
        import receipts_api as _receipts_api_module
        from receipts_api import init_receipts_api, router as receipts_router
        from src.core.governance_receipts import init_governance_receipts

        init_receipts_api(data_dir="/home/lancelot/data")
        receipt_service_getter = getattr(_receipts_api_module, "get_receipt_service_instance", None)
        receipt_service = (
            receipt_service_getter()
            if callable(receipt_service_getter)
            else getattr(_receipts_api_module, "_receipt_service", None)
        )
        if receipt_service is not None:
            init_governance_receipts(receipt_service)
        app.include_router(receipts_router)
        logger.info("Receipts API initialized.")
    except Exception as exc:
        logger.warning("Receipts API initialization failed: %s", exc)

    try:
        from compliance.api import init_compliance_api, router as compliance_router
        from receipts_api import _receipt_service as _compliance_receipt_svc

        if _compliance_receipt_svc is not None:
            init_compliance_api(receipt_service=_compliance_receipt_svc, data_dir="/home/lancelot/data")
            app.include_router(compliance_router)
            logger.info("Compliance Export API initialized.")
        else:
            logger.warning("Compliance Export API skipped: receipt service not available")
    except Exception as exc:
        logger.warning("Compliance Export API initialization failed: %s", exc)

    try:
        from feature_flags import FEATURE_PROCEDURAL_RECOMMENDATIONS

        if FEATURE_PROCEDURAL_RECOMMENDATIONS:
            from procedural_recommendations import ProceduralRecommendationStore
            from procedural_recommendations_api import init_procedural_recommendations_api

            recommendation_store = ProceduralRecommendationStore(data_dir=main_orchestrator.data_dir)
            main_orchestrator.procedural_recommendation_store = recommendation_store
            init_procedural_recommendations_api(recommendation_store)
            app.state.procedural_recommendation_store = recommendation_store
            logger.info("Procedural recommendations initialized.")
        else:
            logger.info("Procedural recommendations disabled by feature flag.")
    except Exception as exc:
        logger.warning("Procedural recommendations initialization failed: %s", exc)

    trust_ledger = getattr(main_orchestrator, "trust_ledger", None)
    rule_engine = None
    decision_log = None
    try:
        from governance.approval_learning.rule_engine import RuleEngine  # noqa: F401

        rule_engine = getattr(main_orchestrator, "rule_engine", None)
        decision_log = getattr(main_orchestrator, "decision_log", None)
    except ImportError as exc:
        logger.debug("Governance rule engine unavailable during API wiring: %s", exc)

    try:
        from governance_api import init_governance_api, router as gov_router

        init_governance_api(
            trust_ledger=trust_ledger,
            rule_engine=rule_engine,
            decision_log=decision_log,
            mcp_sentry=sentry,
        )
        app.include_router(gov_router)
        logger.info("Governance API initialized.")
    except Exception as exc:
        logger.warning("Governance API initialization failed: %s", exc)

    try:
        from trust_api import init_trust_api, router as trust_router

        init_trust_api(trust_ledger=trust_ledger)
        app.include_router(trust_router)
        logger.info("Trust API initialized.")
    except Exception as exc:
        logger.warning("Trust API initialization failed: %s", exc)

    try:
        from apl_api import init_apl_api, router as apl_router

        init_apl_api(rule_engine=rule_engine, decision_log=decision_log)
        app.include_router(apl_router)
        logger.info("APL API initialized.")
    except Exception as exc:
        logger.warning("APL API initialization failed: %s", exc)

    try:
        from tools_api import init_tools_api, router as tools_router

        init_tools_api()
        app.include_router(tools_router)
        logger.info("Tools API initialized.")
    except Exception as exc:
        logger.warning("Tools API initialization failed: %s", exc)

    try:
        from flags_api import init_flags_api, router as flags_router

        init_flags_api(audit_logger=main_orchestrator.audit_logger)
        app.include_router(flags_router)
        logger.info("Flags API initialized.")
    except Exception as exc:
        logger.warning("Flags API initialization failed: %s", exc)
