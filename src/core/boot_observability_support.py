"""Observability subsystem wiring for the gateway boot sequence."""

from __future__ import annotations

import os


def init_observability(*, app, main_orchestrator, logger) -> None:
    """Initialize observability APIs, sinks, and receipt bridge wiring."""
    from feature_flags import FEATURE_OBSERVABILITY

    if not FEATURE_OBSERVABILITY:
        return

    from observability.api import router as observability_router
    from observability.config import (
        describe_otel_export_status,
        load_config as load_observability_config,
    )
    from observability.otel_provider import init_otel
    from observability.receipt_bridge import configure_bridge

    app.include_router(observability_router)
    logger.info("Observability API initialized.")

    observability_config = load_observability_config()
    _init_metrics_api(app=app, logger=logger)
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


def _init_metrics_api(*, app, logger) -> None:
    from observability.metrics_api import router as metrics_api_router, init_metrics_api

    try:
        from receipts_api import _receipt_service as metrics_receipt_service

        if metrics_receipt_service:
            init_metrics_api(metrics_receipt_service, data_dir="/home/lancelot/data")
            app.include_router(metrics_api_router)
            logger.info("Metrics API initialized.")
    except Exception as exc:
        logger.warning("Metrics API initialization failed: %s", exc)


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
