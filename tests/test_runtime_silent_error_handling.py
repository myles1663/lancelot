import asyncio
import importlib
import logging
import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.auth_api as auth_api
import src.core.setup_api as setup_api_module
import src.observability.metrics as metrics_module
import src.observability.receipt_bridge as receipt_bridge_module
import src.observability.webhook_engine as webhook_engine_module
from src.core.event_bus import Event, EventBus
from src.core.operator_identity import OperatorIdentity
from src.core.security_bridge import WebhookAuthenticator
from src.shared.live_session import LiveSessionManager
from src.shared.receipts import ActionType, Receipt, ReceiptService, ReceiptStatus


def _insert_session(token: str, capabilities: set[str]) -> None:
    auth_api._sessions[token] = {
        "expires_at": 9999999999,
        "username": "Arthur",
        "operator_identity": OperatorIdentity(
            operator_id="op-arthur",
            display_name="Arthur",
            session_id="session-1",
            session_started_at="2026-04-10T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        ),
        "capabilities": sorted(capabilities),
        "groups": [],
    }


def _build_setup_client(tmp_path):
    importlib.reload(setup_api_module)
    app = FastAPI()
    setup_api_module.init_setup_api(
        data_dir=str(tmp_path),
        startup_time=0.0,
        audit_logger=None,
        connector_vault=None,
        receipt_service=None,
        verify_request=lambda request: True,
    )
    app.include_router(setup_api_module.router)
    client = TestClient(app)
    auth_api._sessions.clear()
    _insert_session("setup-admin", {"warroom.login", "setup.admin"})
    client.cookies.set(auth_api.get_warroom_session_cookie_name(), "setup-admin")
    return client


def test_provider_oauth_and_profile_fallbacks_log_warning(caplog, monkeypatch):
    module = importlib.reload(importlib.import_module("src.core.providers.api"))

    anthropic_mod = types.ModuleType("oauth_token_manager")
    anthropic_mod.get_oauth_manager = lambda: (_ for _ in ()).throw(RuntimeError("anthropic exploded"))
    monkeypatch.setitem(sys.modules, "oauth_token_manager", anthropic_mod)

    codex_mod = types.ModuleType("openai_codex_oauth_manager")
    codex_mod.get_openai_codex_manager = lambda: (_ for _ in ()).throw(RuntimeError("codex exploded"))
    monkeypatch.setitem(sys.modules, "openai_codex_oauth_manager", codex_mod)

    profile_mod = types.ModuleType("provider_profile")
    class _Registry:
        def __init__(self):
            raise RuntimeError("profile exploded")
    profile_mod.ProfileRegistry = _Registry
    monkeypatch.setitem(sys.modules, "provider_profile", profile_mod)

    with caplog.at_level(logging.WARNING):
        assert module._anthropic_oauth_status() is None
        assert module._codex_oauth_token() == ""
        assert module._provider_profile_lane_overrides("anthropic") == {}

    assert "Failed to inspect Anthropic OAuth status" in caplog.text
    assert "Failed to retrieve Codex OAuth token" in caplog.text
    assert "Failed to seed provider lane overrides from profile 'anthropic'" in caplog.text


def test_factory_reset_logs_subsystem_stop_failure(caplog, monkeypatch, tmp_path):
    client = _build_setup_client(tmp_path)
    subsystem_module = types.ModuleType("subsystem_manager")

    class _SubsystemManager:
        def stop_all(self):
            raise RuntimeError("stop failed")

    subsystem_module.subsystem_manager = _SubsystemManager()
    monkeypatch.setitem(sys.modules, "subsystem_manager", subsystem_module)

    with caplog.at_level(logging.WARNING):
        response = client.post(
            "/api/setup/factory-reset",
            json={"confirm": True, "confirmation_text": "RESET"},
        )

    assert response.status_code == 200
    assert "Factory reset: failed to stop subsystems cleanly" in caplog.text


def test_reset_flags_logs_reload_failure(caplog, monkeypatch, tmp_path):
    client = _build_setup_client(tmp_path)
    flag_state = tmp_path / ".flag_state.json"
    flag_state.write_text("{}", encoding="utf-8")

    feature_flags_module = types.ModuleType("feature_flags")
    feature_flags_module.clear_persisted_flag_state = lambda flag_name=None: None
    def _reload_flags():
        raise RuntimeError("reload failed")
    feature_flags_module.reload_flags = _reload_flags
    monkeypatch.setitem(sys.modules, "feature_flags", feature_flags_module)

    with caplog.at_level(logging.WARNING):
        response = client.post("/api/setup/flags/reset", json={"confirm": True})

    assert response.status_code == 200
    assert "Reset flags: failed to reload feature flags" in caplog.text


def test_webhook_authenticator_logs_secret_cache_failure(caplog, monkeypatch):
    cache_module = types.ModuleType("secret_cache")
    def _get(_key, _default=""):
        raise RuntimeError("cache exploded")
    cache_module.get = _get
    monkeypatch.setitem(sys.modules, "secret_cache", cache_module)
    monkeypatch.setenv("LANCELOT_WEBHOOK_BEARER", "fallback-secret")

    auth = WebhookAuthenticator()

    with caplog.at_level(logging.DEBUG):
        assert auth._expected_bearer() == "fallback-secret"

    assert "Failed to read bonded webhook bearer from secret cache" in caplog.text


def test_receipt_service_logs_bridge_failure(caplog, monkeypatch, tmp_path):
    bridge_module = importlib.import_module("src.observability.receipt_bridge")
    monkeypatch.setattr(
        bridge_module,
        "on_receipt_written",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bridge exploded")),
    )

    service = ReceiptService(data_dir=str(tmp_path))
    try:
        receipt = Receipt(
            action_type=ActionType.SYSTEM.value,
            action_name="runtime-test",
            status=ReceiptStatus.SUCCESS.value,
        )
        with caplog.at_level(logging.WARNING):
            service.create(receipt)
    finally:
        service.close()

    assert "Observability receipt bridge emission failed for receipt" in caplog.text


def test_live_session_close_logs_failure(caplog):
    class _BrokenSession:
        async def close(self):
            raise RuntimeError("close exploded")

    manager = LiveSessionManager(client=object(), model_name="test-model")
    manager._session = _BrokenSession()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(manager.close())

    assert "Failed to close live session cleanly" in caplog.text


def test_event_bus_publish_sync_logs_loop_fallback(caplog, monkeypatch):
    bus = EventBus()
    monkeypatch.setattr(
        "src.core.event_bus.asyncio.get_running_loop",
        lambda: (_ for _ in ()).throw(RuntimeError("no loop")),
    )

    with caplog.at_level(logging.DEBUG):
        bus.publish_sync(Event(type="runtime.test"))

    assert "Falling back to captured event loop for sync publish of runtime.test" in caplog.text


def test_receipt_bridge_logs_missing_incident_hook(caplog, monkeypatch):
    import builtins

    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src.incidents.receipt_hook":
            raise ImportError("incident hook missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    with caplog.at_level(logging.DEBUG):
        receipt_bridge_module._evaluate_incident_triggers({"id": "rcpt-1"})

    assert "Incident receipt hook unavailable; skipping incident evaluation" in caplog.text


def test_webhook_engine_logs_secret_cache_failure(caplog, monkeypatch):
    cache_module = types.ModuleType("secret_cache")
    def _is_bootstrapped():
        raise RuntimeError("cache exploded")
    cache_module.is_bootstrapped = _is_bootstrapped
    monkeypatch.setitem(sys.modules, "secret_cache", cache_module)
    monkeypatch.setenv("WH_SECRET", "fallback-secret")

    endpoint = webhook_engine_module.WebhookEndpoint(
        id="ep-1",
        url="https://example.test/webhook",
        categories=["ALL"],
        secret_vault_key="WH_SECRET",
        enabled=True,
    )
    engine = webhook_engine_module.WebhookEngine(endpoints=[endpoint])

    with caplog.at_level(logging.DEBUG):
        assert engine._get_secret(endpoint) == "fallback-secret"

    assert "Failed to read webhook secret 'WH_SECRET' from secret cache" in caplog.text


def test_metrics_update_logs_malformed_cost(monkeypatch):
    debug_calls = []
    monkeypatch.setattr(metrics_module, "_meter", object())
    monkeypatch.setattr(
        metrics_module.logger,
        "debug",
        lambda message, *args: debug_calls.append(message % args if args else message),
    )

    metrics_module.update_metrics_from_receipt(
        {"outputs": {"cost_usd": "not-a-number", "provider": "gemini", "model": "test"}}
    )

    assert any("Ignoring malformed receipt cost_usd value" in call for call in debug_calls)
