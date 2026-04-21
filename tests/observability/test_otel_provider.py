import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

import src.observability.otel_provider as otel_provider


@pytest.fixture(autouse=True)
def reset_provider_module():
    importlib.reload(otel_provider)
    yield
    importlib.reload(otel_provider)


def _install_fake_opentelemetry(monkeypatch, *, trace_shutdown_error=None, meter_shutdown_error=None):
    state = {}

    class FakeTracerProvider:
        def __init__(self, resource):
            self.resource = resource
            self.span_processors = []
            self.shutdown_calls = 0
            state["tracer_provider"] = self

        def add_span_processor(self, processor):
            self.span_processors.append(processor)

        def shutdown(self):
            self.shutdown_calls += 1
            if trace_shutdown_error is not None:
                raise trace_shutdown_error

    class FakeBatchSpanProcessor:
        def __init__(self, exporter, schedule_delay_millis, max_export_batch_size, max_queue_size):
            self.exporter = exporter
            self.schedule_delay_millis = schedule_delay_millis
            self.max_export_batch_size = max_export_batch_size
            self.max_queue_size = max_queue_size
            state["span_processor"] = self

    class FakeMeterProvider:
        def __init__(self, resource, metric_readers):
            self.resource = resource
            self.metric_readers = metric_readers
            self.shutdown_calls = 0
            state["meter_provider"] = self

        def shutdown(self):
            self.shutdown_calls += 1
            if meter_shutdown_error is not None:
                raise meter_shutdown_error

    class FakeMetricReader:
        def __init__(self, exporter, export_interval_millis):
            self.exporter = exporter
            self.export_interval_millis = export_interval_millis
            state["metric_reader"] = self

    class FakeResource:
        @staticmethod
        def create(attrs):
            state["resource_attributes"] = attrs
            return {"attrs": attrs}

    class FakeSpanExporter:
        def __init__(self, **kwargs):
            state["trace_exporter_kwargs"] = kwargs

    class FakeMetricExporter:
        def __init__(self, **kwargs):
            state["metric_exporter_kwargs"] = kwargs

    trace_api = SimpleNamespace()
    metrics_api = SimpleNamespace()

    def set_tracer_provider(provider):
        state["current_tracer_provider"] = provider

    def get_tracer_provider():
        return state.get("current_tracer_provider")

    def get_tracer(name, version):
        tracer = {"name": name, "version": version}
        state["tracer"] = tracer
        return tracer

    def set_meter_provider(provider):
        state["current_meter_provider"] = provider

    def get_meter_provider():
        return state.get("current_meter_provider")

    def get_meter(name, version):
        meter = {"name": name, "version": version}
        state["meter"] = meter
        return meter

    trace_api.set_tracer_provider = set_tracer_provider
    trace_api.get_tracer_provider = get_tracer_provider
    trace_api.get_tracer = get_tracer

    metrics_api.set_meter_provider = set_meter_provider
    metrics_api.get_meter_provider = get_meter_provider
    metrics_api.get_meter = get_meter

    modules = {
        "opentelemetry": ModuleType("opentelemetry"),
        "opentelemetry.sdk": ModuleType("opentelemetry.sdk"),
        "opentelemetry.sdk.trace": ModuleType("opentelemetry.sdk.trace"),
        "opentelemetry.sdk.trace.export": ModuleType("opentelemetry.sdk.trace.export"),
        "opentelemetry.sdk.metrics": ModuleType("opentelemetry.sdk.metrics"),
        "opentelemetry.sdk.metrics.export": ModuleType("opentelemetry.sdk.metrics.export"),
        "opentelemetry.sdk.resources": ModuleType("opentelemetry.sdk.resources"),
        "opentelemetry.exporter": ModuleType("opentelemetry.exporter"),
        "opentelemetry.exporter.otlp": ModuleType("opentelemetry.exporter.otlp"),
        "opentelemetry.exporter.otlp.proto": ModuleType("opentelemetry.exporter.otlp.proto"),
        "opentelemetry.exporter.otlp.proto.http": ModuleType("opentelemetry.exporter.otlp.proto.http"),
        "opentelemetry.exporter.otlp.proto.http.trace_exporter": ModuleType("opentelemetry.exporter.otlp.proto.http.trace_exporter"),
        "opentelemetry.exporter.otlp.proto.http.metric_exporter": ModuleType("opentelemetry.exporter.otlp.proto.http.metric_exporter"),
    }

    modules["opentelemetry"].trace = trace_api
    modules["opentelemetry"].metrics = metrics_api
    modules["opentelemetry.sdk.trace"].TracerProvider = FakeTracerProvider
    modules["opentelemetry.sdk.trace.export"].BatchSpanProcessor = FakeBatchSpanProcessor
    modules["opentelemetry.sdk.metrics"].MeterProvider = FakeMeterProvider
    modules["opentelemetry.sdk.metrics.export"].PeriodicExportingMetricReader = FakeMetricReader
    modules["opentelemetry.sdk.resources"].Resource = FakeResource
    modules["opentelemetry.exporter.otlp.proto.http.trace_exporter"].OTLPSpanExporter = FakeSpanExporter
    modules["opentelemetry.exporter.otlp.proto.http.metric_exporter"].OTLPMetricExporter = FakeMetricExporter

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    return state


def test_init_otel_returns_false_when_packages_missing(monkeypatch, caplog):
    real_import = __import__

    def raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("opentelemetry"):
            raise ImportError("missing otel")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", raising_import)

    with caplog.at_level("ERROR"):
        ok = otel_provider.init_otel("https://collector.example")

    assert ok is False
    assert otel_provider.is_initialized() is False
    assert "OpenTelemetry packages not installed" in caplog.text


def test_init_otel_success_sets_tracer_meter_and_metrics(monkeypatch):
    state = _install_fake_opentelemetry(monkeypatch)
    metrics_calls = []
    monkeypatch.setattr(
        "src.observability.metrics.init_metrics",
        lambda meter: metrics_calls.append(meter),
    )

    ok = otel_provider.init_otel(
        "https://collector.example/",
        auth_header="X-OTel-Token: secret-value",
        export_interval_ms=7000,
        resource_attributes={"deployment_id": "dep-1"},
    )

    assert ok is True
    assert otel_provider.is_initialized() is True
    assert otel_provider.get_tracer() == {"name": "lancelot.governance", "version": "1.0.0"}
    assert otel_provider.get_meter() == {"name": "lancelot.governance", "version": "1.0.0"}
    assert state["trace_exporter_kwargs"] == {
        "endpoint": "https://collector.example/v1/traces",
        "headers": {"X-OTel-Token": "secret-value"},
        "timeout": 10,
    }
    assert state["metric_exporter_kwargs"] == {
        "endpoint": "https://collector.example/v1/metrics",
        "headers": {"X-OTel-Token": "secret-value"},
        "timeout": 10,
    }
    assert state["resource_attributes"]["service.name"] == "lancelot"
    assert state["resource_attributes"]["deployment_id"] == "dep-1"
    assert state["metric_reader"].export_interval_millis == 7000
    assert metrics_calls == [{"name": "lancelot.governance", "version": "1.0.0"}]


def test_init_otel_bearer_auth_header_defaults_to_authorization(monkeypatch):
    state = _install_fake_opentelemetry(monkeypatch)
    monkeypatch.setattr("src.observability.metrics.init_metrics", lambda meter: None)

    ok = otel_provider.init_otel(
        "https://collector.example",
        auth_header="token-123",
    )

    assert ok is True
    assert state["trace_exporter_kwargs"]["headers"] == {"Authorization": "Bearer token-123"}
    assert state["metric_exporter_kwargs"]["headers"] == {"Authorization": "Bearer token-123"}


def test_init_otel_is_idempotent_after_success(monkeypatch):
    state = _install_fake_opentelemetry(monkeypatch)
    monkeypatch.setattr("src.observability.metrics.init_metrics", lambda meter: None)

    assert otel_provider.init_otel("https://collector.example") is True
    first_provider = state["tracer_provider"]
    first_meter = state["meter_provider"]

    assert otel_provider.init_otel("https://another.example") is True
    assert state["tracer_provider"] is first_provider
    assert state["meter_provider"] is first_meter


def test_shutdown_otel_clears_cached_handles(monkeypatch):
    state = _install_fake_opentelemetry(monkeypatch)
    monkeypatch.setattr("src.observability.metrics.init_metrics", lambda meter: None)
    assert otel_provider.init_otel("https://collector.example") is True

    otel_provider.shutdown_otel()

    assert state["tracer_provider"].shutdown_calls == 1
    assert state["meter_provider"].shutdown_calls == 1
    assert otel_provider.is_initialized() is False
    assert otel_provider.get_tracer() is None
    assert otel_provider.get_meter() is None


def test_init_otel_failure_resets_partial_state(monkeypatch, caplog):
    _install_fake_opentelemetry(monkeypatch)

    def explode(_meter):
        raise RuntimeError("metrics bootstrap failed")

    monkeypatch.setattr("src.observability.metrics.init_metrics", explode)

    with caplog.at_level("ERROR"):
        ok = otel_provider.init_otel("https://collector.example")

    assert ok is False
    assert otel_provider.is_initialized() is False
    assert otel_provider.get_tracer() is None
    assert otel_provider.get_meter() is None
    assert "OTel initialization failed" in caplog.text


def test_shutdown_otel_logs_warning_but_still_clears_state(monkeypatch, caplog):
    _install_fake_opentelemetry(
        monkeypatch,
        trace_shutdown_error=RuntimeError("trace shutdown failed"),
    )
    monkeypatch.setattr("src.observability.metrics.init_metrics", lambda meter: None)
    assert otel_provider.init_otel("https://collector.example") is True

    with caplog.at_level("WARNING"):
        otel_provider.shutdown_otel()

    assert otel_provider.is_initialized() is False
    assert otel_provider.get_tracer() is None
    assert otel_provider.get_meter() is None
    assert "OTel shutdown error" in caplog.text
