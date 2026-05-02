"""Observability subsystem wiring for the gateway boot sequence."""

from __future__ import annotations

import os

_mounted_observability_routes: set[int] = set()


def init_observability(*, app, main_orchestrator, logger) -> None:
    """Initialize observability APIs, sinks, and receipt bridge wiring."""
    from feature_flags import FEATURE_OBSERVABILITY

    if not FEATURE_OBSERVABILITY:
        return

    mount_observability_routers(app, logger=logger)

    _start_observability_runtime(main_orchestrator=main_orchestrator, logger=logger)


def mount_observability_routers(app, *, logger) -> None:
    """Mount observability routers once; middleware gates disabled routes."""
    from observability.api import router as observability_router
    from observability.metrics_api import router as metrics_api_router

    app_id = id(app)
    if app_id in _mounted_observability_routes:
        return
    app.include_router(observability_router)
    app.include_router(metrics_api_router)
    _mounted_observability_routes.add(app_id)
    logger.info("Observability routers mounted.")


def _start_observability_runtime(*, main_orchestrator, logger) -> dict:
    from observability.config import (
        describe_otel_export_status,
        load_config as load_observability_config,
    )
    from observability.otel_provider import init_otel
    from observability.receipt_bridge import configure_bridge

    observability_config = load_observability_config()
    metrics_active = _init_metrics_api(logger=logger)
    webhook_bridge_active = _init_webhook_engine(
        config=observability_config,
        main_orchestrator=main_orchestrator,
        logger=logger,
    )

    otel_export_active = _init_otel_export(
        config=observability_config,
        init_otel=init_otel,
        logger=logger,
    )
    incident_bridge_active = _incident_response_enabled()
    receipt_bridge_active = bool(
        otel_export_active or webhook_bridge_active or incident_bridge_active
    )
    configure_bridge(
        enabled=receipt_bridge_active,
        sampling_rate=observability_config.otel.sampling_rate_t0_t1,
        otel_enabled=otel_export_active,
    )

    otel_status = describe_otel_export_status(
        observability_config.otel,
        initialized=otel_export_active,
        span_export_active=otel_export_active,
    )
    _log_otel_export_status(otel_status=otel_status, logger=logger)
    logger.info(
        "Observability receipt bridge configured: active=%s, otel_export=%s, "
        "webhooks=%s, incident_triggers=%s",
        receipt_bridge_active,
        otel_export_active,
        webhook_bridge_active,
        incident_bridge_active,
    )
    return {
        "metrics_active": metrics_active,
        "webhook_bridge_active": webhook_bridge_active,
        "otel_export_active": otel_export_active,
        "incident_bridge_active": incident_bridge_active,
        "receipt_bridge_active": receipt_bridge_active,
    }


def shutdown_observability(objects=None, *, logger) -> None:
    """Stop observability sinks while leaving gated routers mounted."""
    try:
        from observability.receipt_bridge import configure_bridge

        configure_bridge(enabled=False, otel_enabled=False)
    except Exception as exc:
        logger.warning("Receipt bridge shutdown failed: %s", exc)

    try:
        from observability.webhook_engine import shutdown_webhook_engine

        shutdown_webhook_engine()
    except Exception as exc:
        logger.warning("Webhook engine shutdown failed: %s", exc)

    try:
        from observability.otel_provider import shutdown_otel

        shutdown_otel()
    except Exception as exc:
        logger.warning("OTel shutdown failed: %s", exc)

    try:
        from observability.metrics_api import shutdown_metrics_api

        shutdown_metrics_api()
    except Exception as exc:
        logger.warning("Metrics API shutdown failed: %s", exc)

    logger.info("Observability runtime shutdown complete.")


def init_observability_runtime(*, main_orchestrator, logger) -> dict:
    """Start observability runtime sinks for subsystem_manager hot toggles."""
    return _start_observability_runtime(main_orchestrator=main_orchestrator, logger=logger)


def _init_metrics_api(*, logger) -> bool:
    from observability.metrics_api import init_metrics_api

    try:
        from receipts_api import _receipt_service as metrics_receipt_service

        if metrics_receipt_service:
            init_metrics_api(metrics_receipt_service, data_dir="/home/lancelot/data")
            logger.info("Metrics API initialized.")
            return True
    except Exception as exc:
        logger.warning("Metrics API initialization failed: %s", exc)
    return False


def _init_webhook_engine(*, config, main_orchestrator, logger) -> bool:
    if not (config.webhooks.enabled and config.webhooks.endpoints):
        return False

    from observability.webhook_engine import init_webhook_engine

    init_webhook_engine(
        endpoints=config.webhooks.endpoints,
        deployment_id=os.getenv("LANCELOT_DEPLOYMENT_ID", ""),
        delivery_timeout_s=config.webhooks.delivery_timeout_s,
        max_retries=config.webhooks.max_retries,
        data_dir=main_orchestrator.data_dir,
    )
    logger.info("Webhook engine initialized (%d endpoints)", len(config.webhooks.endpoints))
    return True


def _init_otel_export(*, config, init_otel, logger) -> bool:
    if not (config.otel.enabled and config.otel.endpoint):
        return False

    otel_ok = init_otel(
        endpoint=config.otel.endpoint,
        auth_header=config.otel.auth_header,
        export_interval_ms=config.otel.export_interval_s * 1000,
        resource_attributes=config.otel.resource_attributes,
    )
    if otel_ok:
        logger.info("OTel export initialized (endpoint=%s)", config.otel.endpoint)
    else:
        logger.warning("OTel export initialization failed; span export disabled")
    return bool(otel_ok)


def _incident_response_enabled() -> bool:
    try:
        from feature_flags import FEATURE_INCIDENT_RESPONSE

        return bool(FEATURE_INCIDENT_RESPONSE)
    except Exception:
        return False


def _log_otel_export_status(*, otel_status, logger) -> None:
    if otel_status["state"] == "active":
        logger.info("OTel export active; spans sent to %s", otel_status["export_destination"])
    elif otel_status["state"] == "missing_endpoint":
        logger.warning(
            "OTel export enabled without an OTLP/HTTP endpoint; no spans will be exported. "
            "Set /api/observability/config/otel endpoint or disable OTel export."
        )
    else:
        logger.info(
            "Observability initialized with OTel export state=%s; %s",
            otel_status["state"],
            otel_status["message"],
        )
