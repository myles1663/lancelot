import builtins
import importlib
import logging
import os
import types
import urllib.error

from src.connectors.connectors import generic_rest
from src.core.skills.registry import SkillRegistry
from src.integrations.ucp_connector import UCPConnector
from src.ui.onboarding import OnboardingOrchestrator


def test_generic_rest_logs_non_ip_hostname_debug(caplog):
    with caplog.at_level(logging.DEBUG):
        assert generic_rest._is_private_host("example.com") is False

    assert "Generic REST hostname example.com is not a literal IP address" in caplog.text


def test_ucp_connector_logs_pending_transaction_load_and_save_failures(caplog, monkeypatch, tmp_path):
    state_file = tmp_path / "ucp.json"
    state_file.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_UCP_STATE_FILE", str(state_file))

    with caplog.at_level(logging.WARNING):
        connector = UCPConnector()

    assert "Failed to load pending UCP transactions; starting empty" in caplog.text

    class _BrokenPath:
        def write_text(self, *_args, **_kwargs):
            raise RuntimeError("write exploded")

    connector._state_file = _BrokenPath()

    with caplog.at_level(logging.WARNING):
        connector._save_pending_transactions()

    assert "Failed to persist pending UCP transactions" in caplog.text


def test_warroom_ws_logs_missing_auth_import(monkeypatch, caplog):
    module = importlib.import_module("src.core.warroom_ws")
    original_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "src.core.auth_api":
            raise ImportError("auth unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    with caplog.at_level(logging.DEBUG):
        assert module._verify_ws_token("token") is False

    assert "War Room WS session-token verification unavailable" in caplog.text


def test_onboarding_logs_secret_cache_failure(monkeypatch, caplog, tmp_path):
    cache_module = types.ModuleType("secret_cache")
    def _is_bootstrapped():
        raise RuntimeError("cache exploded")
    cache_module.is_bootstrapped = _is_bootstrapped
    cache_module.get = lambda *_args, **_kwargs: ""
    monkeypatch.setitem(importlib.import_module("sys").modules, "secret_cache", cache_module)
    for key in ("LANCELOT_OWNER_TOKEN", "LANCELOT_API_TOKEN", "LANCELOT_VAULT_KEY"):
        monkeypatch.delenv(key, raising=False)

    with caplog.at_level(logging.WARNING):
        orchestrator = OnboardingOrchestrator(data_dir=str(tmp_path))
        monkeypatch.setattr(orchestrator, "_get_env_value", lambda _key: "")
        assert orchestrator._has_security_tokens() is False

    assert "Onboarding failed to read security token" in caplog.text


def test_onboarding_logs_env_write_failure(monkeypatch, caplog, tmp_path):
    orchestrator = OnboardingOrchestrator(data_dir=str(tmp_path))
    monkeypatch.setattr(orchestrator, "_get_env_value", lambda _key: "")

    def _raising_open(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(builtins, "open", _raising_open)

    with caplog.at_level(logging.WARNING):
        orchestrator._write_env_values({"OPENAI_API_KEY": "secret"}, "Providers")

    assert "Onboarding failed to write .env values" in caplog.text


def test_onboarding_logs_complete_failure(monkeypatch, caplog, tmp_path):
    orchestrator = OnboardingOrchestrator(data_dir=str(tmp_path))

    def _raising_open(*_args, **_kwargs):
        raise OSError("readonly")

    monkeypatch.setattr(builtins, "open", _raising_open)

    with caplog.at_level(logging.WARNING):
        orchestrator._complete_onboarding()

    assert "Onboarding failed to mark onboarding complete" in caplog.text


def test_lancelot_launcher_logs_start_engine_failure(monkeypatch, caplog, tmp_path):
    module = importlib.import_module("src.ui.lancelot_gui")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.os.path, "exists", lambda _path: False)

    def _raising_popen(*_args, **_kwargs):
        raise RuntimeError("compose exploded")

    monkeypatch.setattr(module.subprocess, "Popen", _raising_popen)

    with caplog.at_level(logging.WARNING):
        launcher = module.LancelotLauncher()
        launcher.start_engine()

    assert "Failed to start engine: compose exploded" in caplog.text


def test_lancelot_launcher_logs_health_check_failures(monkeypatch, caplog, tmp_path):
    module = importlib.import_module("src.ui.lancelot_gui")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(module.requests.ConnectionError("offline")),
    )
    monkeypatch.setattr(module.time, "sleep", lambda *_args, **_kwargs: None)

    launcher = module.LancelotLauncher()

    with caplog.at_level(logging.DEBUG):
        launcher.monitor_health()

    assert "War Room health check attempt failed: offline" in caplog.text


def test_lancelot_launcher_reenters_monitor_health_after_runtime_restart(monkeypatch, caplog, tmp_path):
    module = importlib.import_module("src.ui.lancelot_gui")
    monkeypatch.chdir(tmp_path)

    launcher = module.LancelotLauncher()
    launcher.first_run = False

    class _Window:
        def __init__(self):
            self.loaded_urls = []
            self.loaded_html = []

        def load_url(self, url):
            self.loaded_urls.append(url)

        def load_html(self, html):
            self.loaded_html.append(html)

    launcher.window = _Window()

    flags_path = os.path.join("lancelot_data", "FLAGS", "RESTART_REQUIRED")
    exists_calls = {"count": 0}

    def _exists(path):
        if path == flags_path:
            exists_calls["count"] += 1
            return exists_calls["count"] == 2
        return False

    monkeypatch.setattr(module.os.path, "exists", _exists)
    monkeypatch.setattr(module.requests, "get", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module.time, "sleep", lambda *_args, **_kwargs: None)

    actions = []
    monkeypatch.setattr(module.os, "remove", lambda path: actions.append(("remove", path)))
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **_kwargs: actions.append(("restart", args[0] if args else None)),
    )

    original_monitor_health = module.LancelotLauncher.monitor_health.__get__(launcher, module.LancelotLauncher)
    reentered = []
    launcher.monitor_health = lambda: reentered.append("reentered")

    with caplog.at_level(logging.INFO):
        original_monitor_health()

    assert launcher.window.loaded_urls == [module.WAR_ROOM_URL]
    assert launcher.window.loaded_html
    assert reentered == ["reentered"]
    assert ("remove", flags_path) in actions
    assert ("restart", "docker-compose restart") in actions
    assert "Runtime restart signal detected. Rebooting." in caplog.text


def test_skill_registry_logs_backup_load_failure(caplog, tmp_path):
    backup_path = tmp_path / "skills_registry.json.bak"
    backup_path.write_text("{not-json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        registry = SkillRegistry(data_dir=str(tmp_path))

    assert registry.list_skills() == []
    assert "Failed to load backup registry" in caplog.text


def test_verifier_logs_generation_failure(caplog):
    module = importlib.import_module("src.agents.verifier")
    verifier = module.Verifier.__new__(module.Verifier)
    verifier.client = types.SimpleNamespace(
        models=types.SimpleNamespace(
            generate_content=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("verify exploded"))
        )
    )
    verifier.model_name = "gemini-test"

    with caplog.at_level(logging.WARNING):
        result = verifier.verify_step("goal", "output", "context")

    assert result.success is False
    assert "Verification Error: verify exploded" in result.reason
    assert "Verifier Error: verify exploded" in caplog.text


def test_github_search_logs_unreadable_http_error_body(monkeypatch):
    module = importlib.import_module("src.core.skills.builtins.github_search")
    debug_calls = []
    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda message, *args: debug_calls.append(message % args if args else message),
    )

    class _BrokenHttpError(urllib.error.HTTPError):
        def read(self):
            raise RuntimeError("body exploded")

    def _raise_http_error(*_args, **_kwargs):
        raise _BrokenHttpError("https://example.test", 404, "boom", hdrs=None, fp=None)

    monkeypatch.setattr(module, "urlopen", _raise_http_error)

    try:
        module._github_request("https://example.test")
    except RuntimeError:
        pass

    assert any("github_search: failed to read HTTP error body" in call for call in debug_calls)


def test_network_client_logs_unreadable_http_error_body(monkeypatch):
    module = importlib.import_module("src.core.skills.builtins.network_client")
    debug_calls = []
    monkeypatch.setattr(
        module.logger,
        "debug",
        lambda message, *args: debug_calls.append(message % args if args else message),
    )

    class _BrokenHttpError(urllib.error.HTTPError):
        def read(self):
            raise RuntimeError("body exploded")

    def _raise_http_error(*_args, **_kwargs):
        raise _BrokenHttpError("https://example.test", 500, "boom", hdrs=None, fp=None)

    monkeypatch.setattr(module, "urlopen", _raise_http_error)

    result = module.execute(None, {"method": "GET", "url": "https://example.test"})

    assert result["status_code"] == 500
    assert any("network_client: failed to read HTTP error body" in call for call in debug_calls)
