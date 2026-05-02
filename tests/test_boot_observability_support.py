from __future__ import annotations

import sys
import types

from src.core import boot_observability_support as obs


def _module(**attrs):
    mod = types.ModuleType("fake")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _App:
    def __init__(self) -> None:
        self.routers = []

    def include_router(self, router) -> None:
        self.routers.append(router)


class _Logger:
    def __init__(self) -> None:
        self.info_messages = []
        self.warning_messages = []

    def info(self, message, *args):
        self.info_messages.append(message % args if args else message)

    def warning(self, message, *args):
        self.warning_messages.append(message % args if args else message)


def test_init_observability_returns_when_feature_disabled(monkeypatch) -> None:
    app = _App()
    monkeypatch.setitem(sys.modules, "feature_flags", _module(FEATURE_OBSERVABILITY=False))

    obs.init_observability(
        app=app,
        main_orchestrator=types.SimpleNamespace(data_dir="/tmp"),
        logger=_Logger(),
    )

    assert app.routers == []


def test_mount_observability_routers_only_once(monkeypatch) -> None:
    app = _App()
    logger = _Logger()
    obs._mounted_observability_routes.clear()
    monkeypatch.setitem(sys.modules, "observability.api", _module(router="observability"))
    monkeypatch.setitem(sys.modules, "observability.metrics_api", _module(router="metrics"))

    obs.mount_observability_routers(app, logger=logger)
    obs.mount_observability_routers(app, logger=logger)

    assert app.routers == ["observability", "metrics"]
    assert logger.info_messages == ["Observability routers mounted."]


def test_start_observability_runtime_configures_all_active_sinks(monkeypatch) -> None:
    calls = []
    logger = _Logger()
    config = types.SimpleNamespace(
        webhooks=types.SimpleNamespace(
            enabled=True,
            endpoints=["https://example.invalid/hook"],
            delivery_timeout_s=2,
            max_retries=1,
        ),
        otel=types.SimpleNamespace(
            enabled=True,
            endpoint="https://otel.invalid",
            auth_header="Bearer test",
            export_interval_s=3,
            resource_attributes={"service.name": "lancelot"},
            sampling_rate_t0_t1=0.5,
        ),
    )
    monkeypatch.setenv("LANCELOT_DEPLOYMENT_ID", "dep-1")
    monkeypatch.setitem(
        sys.modules,
        "observability.config",
        _module(
            load_config=lambda: config,
            describe_otel_export_status=lambda otel, initialized, span_export_active: {
                "state": "active",
                "export_destination": otel.endpoint,
                "message": "active",
            },
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "observability.otel_provider",
        _module(init_otel=lambda **kwargs: calls.append(("otel", kwargs)) or True),
    )
    monkeypatch.setitem(
        sys.modules,
        "observability.receipt_bridge",
        _module(configure_bridge=lambda **kwargs: calls.append(("bridge", kwargs))),
    )
    monkeypatch.setitem(
        sys.modules,
        "observability.webhook_engine",
        _module(init_webhook_engine=lambda **kwargs: calls.append(("webhook", kwargs))),
    )
    monkeypatch.setitem(
        sys.modules,
        "observability.metrics_api",
        _module(init_metrics_api=lambda *args, **kwargs: calls.append(("metrics", args, kwargs))),
    )
    monkeypatch.setitem(sys.modules, "receipts_api", _module(_receipt_service=object()))
    monkeypatch.setitem(sys.modules, "feature_flags", _module(FEATURE_INCIDENT_RESPONSE=True))

    result = obs.init_observability_runtime(
        main_orchestrator=types.SimpleNamespace(data_dir="/data"),
        logger=logger,
    )

    assert result == {
        "metrics_active": True,
        "webhook_bridge_active": True,
        "otel_export_active": True,
        "incident_bridge_active": True,
        "receipt_bridge_active": True,
    }
    assert ("bridge", {"enabled": True, "sampling_rate": 0.5, "otel_enabled": True}) in calls
    assert any(call[0] == "webhook" for call in calls)
    assert any(call[0] == "otel" for call in calls)


def test_start_observability_runtime_handles_inactive_sinks(monkeypatch) -> None:
    calls = []
    logger = _Logger()
    config = types.SimpleNamespace(
        webhooks=types.SimpleNamespace(enabled=False, endpoints=[], delivery_timeout_s=1, max_retries=0),
        otel=types.SimpleNamespace(
            enabled=True,
            endpoint="",
            auth_header="",
            export_interval_s=1,
            resource_attributes={},
            sampling_rate_t0_t1=1.0,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "observability.config",
        _module(
            load_config=lambda: config,
            describe_otel_export_status=lambda *args, **kwargs: {
                "state": "missing_endpoint",
                "export_destination": "",
                "message": "missing",
            },
        ),
    )
    monkeypatch.setitem(sys.modules, "observability.otel_provider", _module(init_otel=lambda **kwargs: True))
    monkeypatch.setitem(sys.modules, "observability.metrics_api", _module(init_metrics_api=lambda *args, **kwargs: None))
    monkeypatch.setitem(sys.modules, "receipts_api", _module(_receipt_service=None))
    monkeypatch.setitem(
        sys.modules,
        "observability.receipt_bridge",
        _module(configure_bridge=lambda **kwargs: calls.append(kwargs)),
    )
    monkeypatch.setitem(sys.modules, "feature_flags", _module(FEATURE_INCIDENT_RESPONSE=False))

    result = obs.init_observability_runtime(
        main_orchestrator=types.SimpleNamespace(data_dir="/data"),
        logger=logger,
    )

    assert result["receipt_bridge_active"] is False
    assert calls == [{"enabled": False, "sampling_rate": 1.0, "otel_enabled": False}]
    assert any("enabled without an OTLP/HTTP endpoint" in message for message in logger.warning_messages)


def test_shutdown_observability_stops_all_sinks_and_logs_failures(monkeypatch) -> None:
    logger = _Logger()

    def fail(name):
        def _inner(*args, **kwargs):
            raise RuntimeError(f"{name} failed")

        return _inner

    monkeypatch.setitem(sys.modules, "observability.receipt_bridge", _module(configure_bridge=fail("bridge")))
    monkeypatch.setitem(sys.modules, "observability.webhook_engine", _module(shutdown_webhook_engine=fail("webhook")))
    monkeypatch.setitem(sys.modules, "observability.otel_provider", _module(shutdown_otel=fail("otel")))
    monkeypatch.setitem(sys.modules, "observability.metrics_api", _module(shutdown_metrics_api=fail("metrics")))

    obs.shutdown_observability(logger=logger)

    assert any("Receipt bridge shutdown failed" in message for message in logger.warning_messages)
    assert any("Webhook engine shutdown failed" in message for message in logger.warning_messages)
    assert any("OTel shutdown failed" in message for message in logger.warning_messages)
    assert any("Metrics API shutdown failed" in message for message in logger.warning_messages)
    assert logger.info_messages[-1] == "Observability runtime shutdown complete."


def test_log_otel_export_status_handles_non_active_states() -> None:
    logger = _Logger()

    obs._log_otel_export_status(
        otel_status={"state": "disabled", "message": "disabled", "export_destination": ""},
        logger=logger,
    )

    assert logger.info_messages == ["Observability initialized with OTel export state=disabled; disabled"]
