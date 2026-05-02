import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import gateway
import providers.api as runtime_providers_api

from src.core.orchestrator import LancelotOrchestrator


def test_resolve_peer_key_logs_warning_and_uses_topology_fallback(caplog):
    peer_registry = MagicMock()
    peer_registry.get_peer_public_key.side_effect = RuntimeError("registry down")
    topology = MagicMock()
    topology.get_peer.return_value = SimpleNamespace(public_key_hex="aa")

    with caplog.at_level(logging.WARNING):
        key = gateway._resolve_peer_key(peer_registry, topology, "peer-1")

    assert key == bytes.fromhex("aa")
    assert "Peer registry public key lookup failed for peer-1" in caplog.text


def test_shutdown_hive_logs_warning_on_shutdown_failure(caplog):
    lifecycle = MagicMock()
    lifecycle.shutdown.side_effect = RuntimeError("shutdown failed")

    with caplog.at_level(logging.WARNING):
        gateway._shutdown_hive({"lifecycle": lifecycle})

    assert "HIVE lifecycle shutdown failed" in caplog.text


def test_shutdown_federation_logs_warning_on_emitter_failure(caplog):
    emitter = MagicMock()
    emitter.stop.side_effect = RuntimeError("stop failed")

    with caplog.at_level(logging.WARNING):
        gateway._shutdown_federation({"emitter": emitter})

    assert "Federation emitter shutdown failed" in caplog.text


def test_orchestrator_anthropic_oauth_lookup_logs_warning(caplog, monkeypatch):
    manager_module = SimpleNamespace(
        get_oauth_manager=lambda: (_ for _ in ()).throw(RuntimeError("oauth missing"))
    )
    monkeypatch.setitem(sys.modules, "oauth_token_manager", manager_module)
    orchestrator = LancelotOrchestrator.__new__(LancelotOrchestrator)

    with caplog.at_level(logging.WARNING):
        token = orchestrator._get_anthropic_oauth_token()

    assert token == ""
    assert "Anthropic OAuth token lookup failed" in caplog.text


def test_orchestrator_codex_oauth_lookup_logs_warning(caplog, monkeypatch):
    manager_module = SimpleNamespace(
        get_openai_codex_manager=lambda: (_ for _ in ()).throw(RuntimeError("codex missing"))
    )
    monkeypatch.setitem(sys.modules, "openai_codex_oauth_manager", manager_module)
    orchestrator = LancelotOrchestrator.__new__(LancelotOrchestrator)

    with caplog.at_level(logging.WARNING):
        token = orchestrator._get_openai_codex_oauth_token()

    assert token == ""
    assert "OpenAI Codex OAuth token lookup failed" in caplog.text


def test_orchestrator_codex_cli_auth_allows_provider_init(monkeypatch):
    provider = MagicMock()
    orchestrator = LancelotOrchestrator.__new__(LancelotOrchestrator)
    orchestrator.provider = None
    orchestrator.model_name = "gpt-5.4"

    monkeypatch.setenv("LANCELOT_PROVIDER", "openai-codex")
    monkeypatch.setenv("LANCELOT_PROVIDER_MODE", "sdk")
    monkeypatch.delenv("LANCELOT_AUTH_MODE", raising=False)
    monkeypatch.setattr(
        LancelotOrchestrator,
        "_has_openai_codex_cli_auth",
        lambda self: True,
    )
    monkeypatch.setattr(
        LancelotOrchestrator,
        "_get_openai_codex_oauth_token",
        lambda self: "",
    )

    with patch("providers.factory.create_provider", return_value=provider) as mock_create:
        orchestrator._init_provider()

    assert orchestrator.provider is provider
    mock_create.assert_called_once()


def test_restore_persisted_provider_recovers_from_empty_startup_state():
    orchestrator = SimpleNamespace(
        provider=None,
        switch_calls=[],
    )

    def _switch(provider_name):
        orchestrator.switch_calls.append(provider_name)
        orchestrator.provider = SimpleNamespace(provider_name=provider_name)
        return f"switched:{provider_name}"

    orchestrator.switch_provider = _switch

    restored = gateway._restore_persisted_provider("openai-codex", orchestrator)

    assert restored is True
    assert orchestrator.switch_calls == ["openai-codex"]
    assert orchestrator.provider.provider_name == "openai-codex"


def test_restore_persisted_provider_keeps_current_when_same():
    orchestrator = SimpleNamespace(
        provider=SimpleNamespace(provider_name="openai-codex"),
        switch_calls=[],
        switch_provider=lambda provider_name: orchestrator.switch_calls.append(provider_name),
    )

    restored = gateway._restore_persisted_provider("openai-codex", orchestrator)

    assert restored is False
    assert orchestrator.switch_calls == []


def test_bootstrap_model_discovery_persists_active_provider(monkeypatch):
    persisted = []
    init_calls = []

    class _FakeDiscovery:
        def __init__(self, provider, profiles_path, lane_overrides):
            self.provider = provider
            self.profiles_path = profiles_path
            self.lane_assignments = dict(lane_overrides)
            self.discovered_models = []

        def refresh(self):
            return None

    class _FakeRegistry:
        def has_provider(self, provider_name):
            return False

    orchestrator = SimpleNamespace(
        provider=SimpleNamespace(provider_name="openai-codex"),
        set_lane_model=lambda lane, model_id: None,
        local_model=None,
        usage_tracker=None,
    )

    monkeypatch.setattr(gateway, "main_orchestrator", orchestrator)
    monkeypatch.setattr(gateway, "_bootstrap_model_router", lambda: True)
    monkeypatch.setattr("model_discovery.ModelDiscovery", _FakeDiscovery)
    monkeypatch.setattr("provider_profile.ProfileRegistry", _FakeRegistry)
    monkeypatch.setattr(runtime_providers_api, "load_persisted_config", lambda: {})
    monkeypatch.setattr(
        runtime_providers_api,
        "init_provider_api",
        lambda discovery, orchestrator=None: init_calls.append((discovery, orchestrator)),
    )
    monkeypatch.setattr(
        runtime_providers_api,
        "ensure_persisted_active_provider",
        lambda provider_name: persisted.append(provider_name) or True,
    )

    bootstrapped = gateway._bootstrap_model_discovery()

    assert bootstrapped is True
    assert persisted == ["openai-codex"]
    assert len(init_calls) == 1
    assert init_calls[0][1] is orchestrator


def test_orchestrator_trust_summary_logs_warning(caplog):
    trust_ledger = MagicMock()
    trust_ledger.get_record.side_effect = RuntimeError("trust unavailable")
    orchestrator = LancelotOrchestrator.__new__(LancelotOrchestrator)
    orchestrator.trust_ledger = trust_ledger

    with caplog.at_level(logging.WARNING):
        summary = orchestrator._get_trust_summary("command_runner", {"path": "x"})

    assert summary == "Trust data unavailable"
    assert "Failed to read trust summary for command_runner" in caplog.text


def test_orchestrator_receipt_probe_logs_warning(caplog):
    skill_executor = MagicMock()
    type(skill_executor).receipts = property(lambda _self: (_ for _ in ()).throw(RuntimeError("receipts unavailable")))
    orchestrator = LancelotOrchestrator.__new__(LancelotOrchestrator)
    orchestrator.skill_executor = skill_executor
    orchestrator.task_store = None

    with caplog.at_level(logging.WARNING):
        cleaned = orchestrator._apply_honesty_gate("ok")

    assert cleaned == "ok"
    assert "Failed to inspect skill executor receipts during response cleanup" in caplog.text
