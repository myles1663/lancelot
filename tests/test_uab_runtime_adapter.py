from __future__ import annotations

import types

import src.core.uab_runtime_adapter as adapter


class _Logger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)

    def info(self, message, *args):
        self.infos.append(message % args if args else message)


class _Fabric:
    def __init__(self):
        self.registered = []
        self.unregistered = []
        self.updated = 0

    def register_provider(self, provider):
        self.registered.append(provider)

    def unregister_provider(self, provider_id):
        self.unregistered.append(provider_id)

    def update_router_preferences(self):
        self.updated += 1


def test_create_uab_provider_passes_receipt_service_when_supported(monkeypatch):
    captured = {}

    class Provider:
        def __init__(self, receipt_service=None):
            captured["receipt_service"] = receipt_service

    import src.tools.providers.uab_bridge as bridge

    monkeypatch.setattr(bridge, "UABProvider", Provider)

    service = object()
    provider = adapter.create_uab_provider(receipt_service=service)

    assert isinstance(provider, Provider)
    assert captured["receipt_service"] is service


def test_create_uab_provider_preserves_legacy_provider_without_receipt_kwarg(monkeypatch):
    class Provider:
        def __init__(self):
            self.created = True

    import src.tools.providers.uab_bridge as bridge

    monkeypatch.setattr(bridge, "UABProvider", Provider)

    provider = adapter.create_uab_provider(receipt_service=object())

    assert provider.created is True


def test_init_and_shutdown_register_through_tool_fabric(monkeypatch):
    fabric = _Fabric()
    provider = types.SimpleNamespace(config=types.SimpleNamespace(daemon_url="http://uab.test"))
    logger = _Logger()

    import src.tools.fabric as fabric_module

    monkeypatch.setattr(fabric_module, "get_tool_fabric", lambda: fabric)
    monkeypatch.setattr(adapter, "create_uab_provider", lambda: provider)

    result = adapter.init_uab_provider(logger)
    adapter.shutdown_uab_provider(logger)

    assert result == {"provider": provider}
    assert fabric.registered == [provider]
    assert fabric.unregistered == ["uab_bridge"]
    assert fabric.updated == 2
    assert "http://uab.test" in logger.warnings[0]
    assert logger.infos == ["UAB Bridge provider unregistered."]


def test_summarize_uab_provider_health_prefers_provider_contract():
    provider = types.SimpleNamespace(
        summarize_health=lambda health: {"state": "healthy", "source": health.source}
    )
    health = types.SimpleNamespace(source="provider")

    assert adapter.summarize_uab_provider_health(provider, health) == {
        "state": "healthy",
        "source": "provider",
    }


def test_summarize_uab_provider_health_fallback_uses_metadata_and_config():
    provider = types.SimpleNamespace(config=types.SimpleNamespace(daemon_url="http://fallback"))
    health = types.SimpleNamespace(
        state=types.SimpleNamespace(value="offline"),
        metadata={},
        error_message="daemon unavailable",
    )

    assert adapter.summarize_uab_provider_health(provider, health) == {
        "state": "offline",
        "daemon_url": "http://fallback",
        "error": "daemon unavailable",
    }


def test_get_uab_provider_returns_provider_for_healthy_and_offline(monkeypatch):
    logger = _Logger()

    healthy_provider = types.SimpleNamespace(
        health_check=lambda: types.SimpleNamespace(
            state=types.SimpleNamespace(value="healthy"),
            metadata={"daemon_url": "http://healthy"},
            error_message=None,
        )
    )
    monkeypatch.setattr(adapter, "create_uab_provider", lambda: healthy_provider)

    provider, status = adapter.get_uab_provider(logger)

    assert provider is healthy_provider
    assert status["state"] == "healthy"

    offline_provider = types.SimpleNamespace(
        health_check=lambda: types.SimpleNamespace(
            state=types.SimpleNamespace(value="offline"),
            metadata={"daemon_url": "http://offline"},
            error_message="offline",
        )
    )
    monkeypatch.setattr(adapter, "create_uab_provider", lambda: offline_provider)

    provider, status = adapter.get_uab_provider(logger)

    assert provider is offline_provider
    assert status["state"] == "offline"
    assert "offline at startup" in logger.warnings[-1]


def test_get_uab_provider_returns_unavailable_on_initialization_error(monkeypatch):
    logger = _Logger()
    monkeypatch.setattr(
        adapter,
        "create_uab_provider",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    provider, status = adapter.get_uab_provider(logger)

    assert provider is None
    assert status == {"state": "unavailable", "daemon_url": None, "error": "boom"}
    assert "failed to initialize" in logger.warnings[0]
