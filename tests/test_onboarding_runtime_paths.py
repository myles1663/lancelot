import json
import os
import sys
import types

import pytest

from src.core.onboarding_snapshot import OnboardingState
from src.ui import onboarding as onboarding_module
from src.ui.onboarding import OnboardingOrchestrator


ENV_KEYS = [
    "LANCELOT_PROVIDER",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "NVIDIA_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "LANCELOT_PROVIDER_MODE",
    "LANCELOT_COMMS_TYPE",
    "LANCELOT_CHAT_SPACE_NAME",
    "LANCELOT_TELEGRAM_TOKEN",
    "LANCELOT_TELEGRAM_CHAT_ID",
    "LANCELOT_AUTH_PROVIDER",
    "WARROOM_USERNAME",
    "WARROOM_PASSWORD",
    "WARROOM_PASSWORD_RESET_CODE",
    "OIDC_ISSUER_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "LANCELOT_OWNER_TOKEN",
    "LANCELOT_API_TOKEN",
    "LANCELOT_VAULT_KEY",
    "UAB_DAEMON_URL",
]


@pytest.fixture(autouse=True)
def clean_onboarding_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(onboarding_module, "_load_persisted_provider", lambda: "")
    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: False)


def new_orchestrator(tmp_path, *, bonded=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if bonded:
        (tmp_path / "USER.md").write_text("# User Profile\n- Name: Arthur", encoding="utf-8")
    orch = OnboardingOrchestrator(data_dir=str(tmp_path))
    orch.env_file = str(tmp_path / ".env")
    return orch


def test_determine_state_walks_core_runtime_gates(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path)
    assert orch.determine_state() == "WELCOME"

    (tmp_path / "USER.md").write_text("# User Profile", encoding="utf-8")
    assert orch.determine_state() == "FLAGSHIP_SELECTION"

    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    assert orch.determine_state() == "HANDSHAKE"

    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    assert orch.determine_state() == "PROVIDER_MODE_SELECTION"

    monkeypatch.setenv("LANCELOT_PROVIDER_MODE", "sdk")
    assert orch.determine_state() == "LOCAL_UTILITY_SETUP"

    orch.snapshot.local_model_status = "verified"
    assert orch.determine_state() == "COMMS_SELECTION"

    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "none")
    assert orch.determine_state() == "AUTH_MODEL_SELECTION"

    monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
    assert orch.determine_state() == "LOCAL_AUTH_SETUP"

    monkeypatch.setenv("WARROOM_USERNAME", "arthur")
    monkeypatch.setenv("WARROOM_PASSWORD", "long-password")
    assert orch.determine_state() == "FINAL_CHECKS"

    monkeypatch.setenv("LANCELOT_OWNER_TOKEN", "owner")
    monkeypatch.setenv("LANCELOT_API_TOKEN", "api")
    monkeypatch.setenv("LANCELOT_VAULT_KEY", "vault")
    assert orch.determine_state() == "READY"


def test_sync_snapshot_records_ready_provider_and_respects_cooldown(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path)
    orch.state = "READY"
    monkeypatch.setenv("LANCELOT_PROVIDER", "openai")

    orch._sync_snapshot()

    assert orch.snapshot.state == OnboardingState.READY
    assert orch.snapshot.flagship_provider == "openai"
    assert orch.snapshot.credential_status == "verified"

    orch.snapshot.enter_cooldown(300, "test")
    orch.state = "FLAGSHIP_SELECTION"
    orch._sync_snapshot()

    assert orch.snapshot.state == OnboardingState.COOLDOWN


def test_write_env_values_appends_missing_keys_once(tmp_path):
    orch = new_orchestrator(tmp_path)
    orch._write_env_values({"A": "1", "B": "2"}, "Section")
    orch._write_env_values({"A": "changed", "C": "3"}, "Second")

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")

    assert env_text.count("A=") == 1
    assert "A=1" in env_text
    assert "B=2" in env_text
    assert "C=3" in env_text


def test_bond_identity_success_and_file_error(tmp_path):
    orch = new_orchestrator(tmp_path)

    response = orch._bond_identity("Arthur")

    assert orch.state == "FLAGSHIP_SELECTION"
    assert "Welcome, Arthur" in response
    assert "Bonded: True" in (tmp_path / "USER.md").read_text(encoding="utf-8")

    failing = new_orchestrator(tmp_path / "failing")
    blocked_path = tmp_path / "failing" / "USER-as-directory"
    blocked_path.mkdir()
    failing.user_file = str(blocked_path)
    response = failing._bond_identity("Arthur")
    assert response.startswith("Error bonding identity:")


def test_provider_selection_invalid_api_key_and_oauth_branches(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path, bonded=True)

    assert "Invalid selection" in orch._handle_flagship_selection("bogus")

    gemini_prompt = orch._handle_flagship_selection("gemini")
    assert orch.temp_data["provider"] == "gemini"
    assert orch.state == "HANDSHAKE"
    assert "scan" in gemini_prompt

    orch.state = "FLAGSHIP_SELECTION"
    anthropic_prompt = orch._handle_flagship_selection("anthropic")
    assert "oauth" in anthropic_prompt.lower()

    orch.state = "FLAGSHIP_SELECTION"
    monkeypatch.setitem(sys.modules, "openai_codex_oauth_manager", types.SimpleNamespace(get_openai_codex_manager=lambda: None))
    codex_prompt = orch._handle_flagship_selection("codex")
    assert "Codex OAuth manager not available" in codex_prompt
    assert orch.state == "FLAGSHIP_SELECTION"


def test_api_key_verification_success_failure_and_cooldown(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)
    orch.temp_data["provider"] = "openai"
    orch._validate_api_key_live = lambda provider, key: {"valid": False, "error": "rejected"}

    assert "Expected prefix" in orch._verify_api_key("bad-key")

    for _ in range(4):
        assert "API Key Invalid" in orch._verify_api_key("sk-test")
    response = orch._verify_api_key("sk-test")
    assert "cooldown" in response.lower()
    assert orch.state == "COOLDOWN"

    orch = new_orchestrator(tmp_path / "success", bonded=True)
    orch.temp_data["provider"] = "openai"
    orch._validate_api_key_live = lambda provider, key: {"valid": True, "warning": "limited scope"}

    response = orch._verify_api_key("sk-valid")

    assert orch.state == "PROVIDER_MODE_SELECTION"
    assert "limited scope" in response
    assert "OPENAI_API_KEY=sk-valid" in (tmp_path / "success" / ".env").read_text(encoding="utf-8")


def test_anthropic_oauth_waiting_handles_cancel_pending_success_and_errors(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path, bonded=True)

    class Manager:
        configured = False

        def generate_auth_url(self):
            return "https://auth.example/authorize", "state-1"

        def get_token_status(self):
            return {"configured": self.configured}

    manager = Manager()
    monkeypatch.setitem(sys.modules, "oauth_token_manager", types.SimpleNamespace(get_oauth_manager=lambda: manager))

    prompt = orch._initiate_anthropic_oauth()
    assert orch.state == "ANTHROPIC_OAUTH_WAITING"
    assert "https://auth.example/authorize" in prompt
    assert "done" in orch._handle_anthropic_oauth_waiting("later")
    assert "not detected" in orch._handle_anthropic_oauth_waiting("done")

    manager.configured = True
    response = orch._handle_anthropic_oauth_waiting("done")
    assert orch.state == "PROVIDER_MODE_SELECTION"
    assert "OAuth Authorized" in response

    orch.state = "ANTHROPIC_OAUTH_WAITING"
    response = orch._handle_anthropic_oauth_waiting("cancel")
    assert orch.state == "HANDSHAKE"
    assert "OAuth cancelled" in response


def test_openai_codex_oauth_waiting_handles_cli_oauth_cancel_and_errors(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path, bonded=True)

    class Manager:
        configured = False

        def generate_auth_url(self):
            return "https://codex.example/auth", "state-2"

        def get_token_status(self):
            return {"configured": self.configured}

    manager = Manager()
    monkeypatch.setitem(
        sys.modules,
        "openai_codex_oauth_manager",
        types.SimpleNamespace(get_openai_codex_manager=lambda: manager),
    )

    prompt = orch._initiate_openai_codex_oauth()
    assert orch.state == "OPENAI_CODEX_OAUTH_WAITING"
    assert "https://codex.example/auth" in prompt
    assert "credentials not detected" in orch._handle_openai_codex_oauth_waiting("done").lower()

    manager.configured = True
    response = orch._handle_openai_codex_oauth_waiting("done")
    assert "OAuth Authorized" in response
    assert orch.state == "PROVIDER_MODE_SELECTION"

    orch.state = "OPENAI_CODEX_OAUTH_WAITING"
    assert "Codex OAuth cancelled" in orch._handle_openai_codex_oauth_waiting("cancel")

    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: True)
    response = orch._handle_openai_codex_oauth_waiting("done")
    assert "Codex CLI Auth Detected" in response


def test_provider_mode_selection_advances_to_current_runtime_state(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)
    orch.temp_data["provider"] = "gemini"

    assert "Invalid selection" in orch._handle_provider_mode("bad")

    response = orch._handle_provider_mode("api")

    assert "API mode selected" in response
    assert "LANCELOT_PROVIDER_MODE=api" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_google_adc_verification_success_and_missing_credentials(tmp_path, monkeypatch):
    adc_file = tmp_path / "adc.json"
    adc_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc_file))
    orch = new_orchestrator(tmp_path, bonded=True)

    response = orch._verify_oauth_creds()

    assert orch.state == "COMMS_CHAT_SCAN"
    assert "Identity Verified" in response
    assert "LANCELOT_PROVIDER=gemini" in (tmp_path / ".env").read_text(encoding="utf-8")

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    missing = new_orchestrator(tmp_path / "missing", bonded=True)
    response = missing._verify_oauth_creds()
    assert "Google Credentials Not Found" in response


def test_comms_selection_telegram_skip_google_and_invalid(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)

    assert "Invalid selection" in orch._handle_comms_selection("unknown")

    telegram = orch._handle_comms_selection("telegram")
    assert orch.state == "COMMS_TELEGRAM_TOKEN"
    assert "Telegram Selected" in telegram

    orch = new_orchestrator(tmp_path / "skip", bonded=True)
    orch._handle_final_checks = lambda: "final checks"
    assert orch._handle_comms_selection("skip") == "final checks"
    assert orch.state == "FINAL_CHECKS"

    orch = new_orchestrator(tmp_path / "google", bonded=True)
    orch._verify_oauth_creds = lambda: "adc prompt"
    assert orch._handle_comms_selection("google") == "adc prompt"
    assert orch.state == "COMMS_ADC_CHECK"


def test_guided_setup_finish_writes_restart_flag_for_connector(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)
    orch.temp_data["comms_type"] = "slack"
    orch.temp_data["guided_step"] = 0

    assert "Expected value" in orch._handle_guided_setup("bad-token")
    prompt = orch._handle_guided_setup("xoxb-token")
    assert "Channel ID" in prompt

    orch._handle_final_checks = lambda: "final checks"
    response = orch._handle_guided_setup("C123")

    assert "Slack Configured" in response
    assert "final checks" in response
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LANCELOT_COMMS_TYPE=slack" in env_text
    assert "SLACK_BOT_TOKEN=xoxb-token" in env_text
    assert (tmp_path / "FLAGS" / "RESTART_REQUIRED").read_text(encoding="utf-8") == "CONFIG_UPDATED"


def test_chat_scan_select_and_telegram_handshake_success(tmp_path, monkeypatch):
    spaces = [{"name": "spaces/1", "displayName": "Ops", "type": "SPACE"}]
    sent = []

    class ChatPoller:
        def __init__(self, data_dir):
            self.data_dir = data_dir

        def list_spaces(self):
            return spaces

        def send_message(self, message, space_name):
            sent.append((message, space_name))

    monkeypatch.setitem(sys.modules, "chat_poller", types.SimpleNamespace(ChatPoller=ChatPoller))
    orch = new_orchestrator(tmp_path, bonded=True)

    response = orch._handle_chat_scan("scan")
    assert orch.state == "COMMS_CHAT_SELECT"
    assert "Ops" in response
    assert "Please enter a number" in orch._handle_chat_select("x")
    assert "Invalid number" in orch._handle_chat_select("9")

    response = orch._handle_chat_select("1")
    assert orch.state == "COMMS_VERIFY"
    assert sent[0][1] == "spaces/1"
    assert orch.temp_data["verification_code"] in sent[0][0]

    orch = new_orchestrator(tmp_path / "telegram", bonded=True)
    orch.temp_data.update(
        {
            "comms_type": "telegram",
            "telegram_token": "123456789:TESTTOKEN",
            "telegram_chat_id": "999",
        }
    )
    monkeypatch.setattr(onboarding_module.assert_url_allowed, "__call__", lambda *args, **kwargs: args[0], raising=False)


def test_telegram_token_chat_and_handshake_failures(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path, bonded=True)

    assert "Invalid Token format" in orch._handle_telegram_token("short")
    assert "Token Accepted" in orch._handle_telegram_token("123456789:TESTTOKENLONG")
    assert orch.state == "COMMS_TELEGRAM_CHAT"

    class TimeoutRequests:
        class exceptions:
            class Timeout(Exception):
                pass

        @staticmethod
        def post(*args, **kwargs):
            raise TimeoutRequests.exceptions.Timeout()

    monkeypatch.setitem(sys.modules, "requests", TimeoutRequests)
    response = orch._handle_telegram_chat("999")
    assert "Connection Timeout" in response

    class FailedRequests:
        class exceptions:
            class Timeout(Exception):
                pass

        @staticmethod
        def post(*args, **kwargs):
            return types.SimpleNamespace(
                status_code=401,
                text="bad",
                json=lambda: {"description": "Unauthorized"},
            )

    monkeypatch.setitem(sys.modules, "requests", FailedRequests)
    response = orch._initiate_handshake("telegram")
    assert "Telegram Send Failed" in response
    assert "Unauthorized" in response


def test_verify_handshake_success_and_cooldown(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)
    orch._handle_final_checks = lambda: "final checks"
    orch.temp_data.update(
        {
            "verification_code": "ABC123",
            "comms_type": "telegram",
            "telegram_token": "123456789:TESTTOKEN",
            "telegram_chat_id": "999",
        }
    )

    assert "Verification Failed" in orch._verify_handshake("wrong")
    for _ in range(4):
        response = orch._verify_handshake("wrong")
    assert "cooldown" in response.lower()
    assert orch.state == "COOLDOWN"

    orch = new_orchestrator(tmp_path / "success", bonded=True)
    orch._handle_final_checks = lambda: "final checks"
    orch.temp_data.update(
        {
            "verification_code": "ABC123",
            "comms_type": "telegram",
            "telegram_token": "123456789:TESTTOKEN",
            "telegram_chat_id": "999",
        }
    )
    response = orch._verify_handshake("abc123")
    assert "Handshake Verified" in response
    assert "final checks" in response
    env_text = (tmp_path / "success" / ".env").read_text(encoding="utf-8")
    assert "LANCELOT_TELEGRAM_TOKEN=123456789:TESTTOKEN" in env_text
    assert (tmp_path / "success" / "FLAGS" / "RESTART_REQUIRED").read_text(encoding="utf-8") == "CONFIG_UPDATED"


def test_local_auth_setup_validation_and_success(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)

    assert "Invalid selection" in orch._handle_auth_model_selection("bad")
    assert "Local authentication selected" in orch._handle_auth_model_selection("local")
    assert "Username must" in orch._handle_local_auth_setup("!")
    assert "password" in orch._handle_local_auth_setup("arthur").lower()
    assert "at least 8" in orch._handle_local_auth_setup("short")
    assert "Confirm" in orch._handle_local_auth_setup("long-password")
    assert "do not match" in orch._handle_local_auth_setup("other-password")
    assert "Confirm" in orch._handle_local_auth_setup("long-password")

    orch._handle_final_checks = lambda: "final checks"
    response = orch._handle_local_auth_setup("long-password")

    assert "Local authentication configured" in response
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LANCELOT_AUTH_PROVIDER=local" in env_text
    assert "WARROOM_USERNAME=arthur" in env_text


def test_enterprise_auth_setup_validation_and_success(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)

    assert "Enterprise SSO selected" in orch._handle_auth_model_selection("oidc")
    assert "must start" in orch._handle_enterprise_auth_setup("issuer")
    assert "client ID" in orch._handle_enterprise_auth_setup("https://issuer.example")
    assert "Client ID is required" in orch._handle_enterprise_auth_setup("")
    assert "client secret" in orch._handle_enterprise_auth_setup("client-id")
    assert "Client secret is required" in orch._handle_enterprise_auth_setup("")
    assert "allowed OIDC groups" in orch._handle_enterprise_auth_setup("secret")
    assert "At least one" in orch._handle_enterprise_auth_setup("")

    orch._handle_final_checks = lambda: "final checks"
    response = orch._handle_enterprise_auth_setup("admins,operators")

    assert "Enterprise SSO configured" in response
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LANCELOT_AUTH_PROVIDER=oidc" in env_text
    assert "OIDC_ALLOWED_GROUPS=admins,operators" in env_text

    orch = new_orchestrator(tmp_path / "open", bonded=True)
    orch._handle_auth_model_selection("oidc")
    orch._handle_enterprise_auth_setup("https://issuer.example")
    orch._handle_enterprise_auth_setup("client-id")
    orch._handle_enterprise_auth_setup("secret")
    orch._handle_final_checks = lambda: "final checks"
    orch._handle_enterprise_auth_setup("open")
    assert "OIDC_ALLOW_ANY_AUTHENTICATED=true" in (tmp_path / "open" / ".env").read_text(encoding="utf-8")


def test_final_checks_generates_tokens_flags_summary_and_restart(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path, bonded=True)
    orch._write_env_values(
        {
            "LANCELOT_PROVIDER": "gemini",
            "LANCELOT_PROVIDER_MODE": "sdk",
            "LANCELOT_COMMS_TYPE": "none",
            "LANCELOT_AUTH_PROVIDER": "local",
            "WARROOM_USERNAME": "arthur",
            "WARROOM_PASSWORD": "long-password",
        }
    )
    monkeypatch.setattr(onboarding_module.secrets, "token_urlsafe", lambda n: f"token-{n}")

    class FakeUrlLib:
        class request:
            class Request:
                def __init__(self, *args, **kwargs):
                    pass

            @staticmethod
            def urlopen(*args, **kwargs):
                class Response:
                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        return False

                    def read(self):
                        return json.dumps({"result": {"ok": True}}).encode("utf-8")

                return Response()

    monkeypatch.setitem(sys.modules, "urllib.request", FakeUrlLib.request)

    response = orch._handle_final_checks()

    assert orch.state == "READY"
    assert "Final Configuration Complete" in response
    assert "UAB Daemon: Running" in response
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LANCELOT_OWNER_TOKEN=token-32" in env_text
    assert "WARROOM_PASSWORD_RESET_CODE=token-16" in env_text
    assert "FEATURE_SOUL=true" in env_text
    assert (tmp_path / "FLAGS" / "RESTART_REQUIRED").read_text(encoding="utf-8") == "ONBOARDING_COMPLETE"


def test_final_checks_redirects_missing_auth_setup(tmp_path):
    orch = new_orchestrator(tmp_path, bonded=True)
    assert "War Room Authentication" in orch._handle_final_checks()
    assert orch.state == "AUTH_MODEL_SELECTION"

    local = new_orchestrator(tmp_path / "local", bonded=True)
    local._write_env_values({"LANCELOT_AUTH_PROVIDER": "local"})
    assert "Choose your War Room username" in local._handle_final_checks()
    assert local.state == "LOCAL_AUTH_SETUP"

    oidc = new_orchestrator(tmp_path / "oidc", bonded=True)
    oidc._write_env_values({"LANCELOT_AUTH_PROVIDER": "oidc"})
    assert "Enter your OIDC issuer URL" in oidc._handle_final_checks()
    assert oidc.state == "ENTERPRISE_AUTH_SETUP"


def test_process_dispatches_states_and_ready_fallback(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path)
    assert "Welcome, Arthur" in orch.process("Arthur", "hello")

    orch.state = "COOLDOWN"
    orch.snapshot.cooldown_until = 0
    orch._determine_state = lambda: "READY"
    assert "ready" in orch.process("Arthur", "continue").lower()

    orch.state = "LOCAL_UTILITY_SETUP"
    monkeypatch.setattr(onboarding_module, "handle_local_utility_setup", lambda text, snapshot: "local handled")
    assert orch.process("Arthur", "skip") == "local handled"

    handlers = {
        "HANDSHAKE": "_handle_auth_options",
        "ANTHROPIC_OAUTH_WAITING": "_handle_anthropic_oauth_waiting",
        "OPENAI_CODEX_OAUTH_WAITING": "_handle_openai_codex_oauth_waiting",
        "PROVIDER_MODE_SELECTION": "_handle_provider_mode",
        "COMMS_SELECTION": "_handle_comms_selection",
        "COMMS_GUIDED_SETUP": "_handle_guided_setup",
        "COMMS_CHAT_SCAN": "_handle_chat_scan",
        "COMMS_CHAT_SELECT": "_handle_chat_select",
        "COMMS_TELEGRAM_TOKEN": "_handle_telegram_token",
        "COMMS_TELEGRAM_CHAT": "_handle_telegram_chat",
        "COMMS_VERIFY": "_verify_handshake",
        "AUTH_MODEL_SELECTION": "_handle_auth_model_selection",
        "LOCAL_AUTH_SETUP": "_handle_local_auth_setup",
        "ENTERPRISE_AUTH_SETUP": "_handle_enterprise_auth_setup",
    }
    for state, handler_name in handlers.items():
        orch.state = state
        setattr(orch, handler_name, lambda text, _state=state: f"{_state} handled")
        assert orch.process("Arthur", "next") == f"{state} handled"

    orch.state = "FINAL_CHECKS"
    orch._handle_final_checks = lambda: "final checks handled"
    assert orch.process("Arthur", "next") == "final checks handled"

    orch.state = "READY"
    assert orch.process("Arthur", "hello") == "Lancelot is ready. How may I serve you?"


def test_final_checks_enterprise_summary_vault_token_bootstrap_and_uab_down(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path, bonded=True)
    orch._write_env_values(
        {
            "LANCELOT_PROVIDER": "openai",
            "LANCELOT_PROVIDER_MODE": "api",
            "LANCELOT_COMMS_TYPE": "none",
            "LANCELOT_AUTH_PROVIDER": "oidc",
            "OIDC_ISSUER_URL": "https://issuer.example",
            "OIDC_CLIENT_ID": "client-id",
            "OIDC_CLIENT_SECRET": "client-secret",
        }
    )
    monkeypatch.setattr(onboarding_module.secrets, "token_urlsafe", lambda n: f"enterprise-token-{n}")

    stored = []

    class Vault:
        def __init__(self, config_path):
            self.config_path = config_path

        def store(self, key, value, type):
            stored.append((key, value, type))

    fake_secret_cache = types.SimpleNamespace(
        is_bootstrapped=lambda: True,
        bootstrap=lambda vault: stored.append(("bootstrap", vault.config_path, "vault")),
        get=lambda key, default="": "",
    )
    monkeypatch.setitem(sys.modules, "secret_cache", fake_secret_cache)
    monkeypatch.setitem(sys.modules, "connectors.vault", types.SimpleNamespace(CredentialVault=Vault))

    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("daemon down")),
    )

    response = orch._handle_final_checks()

    assert orch.state == "READY"
    assert "Enterprise SSO is enabled" in response
    assert "UAB Daemon: Not running" in response
    assert ("system.api_token", "enterprise-token-32", "system_secret") in stored
    assert ("system.owner_token", "enterprise-token-32", "system_secret") in stored
    assert any(item[0] == "bootstrap" for item in stored)


def test_determine_state_accepts_oauth_cli_adc_and_snapshot_credentials(tmp_path, monkeypatch):
    base = tmp_path / "oauth"
    base.mkdir()
    (base / "USER.md").write_text("# User Profile", encoding="utf-8")

    monkeypatch.setenv("LANCELOT_PROVIDER", "anthropic")
    monkeypatch.setitem(
        sys.modules,
        "oauth_token_manager",
        types.SimpleNamespace(
            get_oauth_manager=lambda: types.SimpleNamespace(
                get_token_status=lambda: {"configured": True}
            )
        ),
    )
    orch = new_orchestrator(base, bonded=True)
    assert orch.determine_state() == "PROVIDER_MODE_SELECTION"

    monkeypatch.setenv("LANCELOT_PROVIDER", "openai-codex")
    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: True)
    codex = new_orchestrator(tmp_path / "codex", bonded=True)
    assert codex.determine_state() == "PROVIDER_MODE_SELECTION"

    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: False)
    monkeypatch.setitem(
        sys.modules,
        "openai_codex_oauth_manager",
        types.SimpleNamespace(
            get_openai_codex_manager=lambda: types.SimpleNamespace(
                get_token_status=lambda: {"configured": True}
            )
        ),
    )
    codex_oauth = new_orchestrator(tmp_path / "codex-oauth", bonded=True)
    assert codex_oauth.determine_state() == "PROVIDER_MODE_SELECTION"

    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    monkeypatch.setenv("LANCELOT_PROVIDER_MODE", "sdk")
    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "none")
    monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
    monkeypatch.setenv("WARROOM_USERNAME", "arthur")
    monkeypatch.setenv("WARROOM_PASSWORD", "long-password")
    monkeypatch.setenv("LANCELOT_OWNER_TOKEN", "owner")
    monkeypatch.setenv("LANCELOT_API_TOKEN", "api")
    monkeypatch.setenv("LANCELOT_VAULT_KEY", "vault")
    snapshot_ready = new_orchestrator(tmp_path / "snapshot-ready", bonded=True)
    snapshot_ready.snapshot.credential_status = "verified"
    snapshot_ready.snapshot.local_model_status = "verified"
    assert snapshot_ready.determine_state() == "READY"


def test_onboarding_env_provider_vault_and_auth_inference_fallbacks(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    orch = new_orchestrator(env_dir, bonded=True)
    (env_dir / ".env").write_text(
        "LANCELOT_PROVIDER=openai\nOIDC_ISSUER_URL=https://issuer.example\nOIDC_CLIENT_ID=client\n",
        encoding="utf-8",
    )
    assert orch._get_env_value("LANCELOT_PROVIDER") == "openai"
    assert orch._infer_auth_provider() == "oidc"

    monkeypatch.setitem(
        sys.modules,
        "secret_cache",
        types.SimpleNamespace(
            is_bootstrapped=lambda: True,
            get=lambda key, default="": (_ for _ in ()).throw(RuntimeError("vault unavailable")),
        ),
    )
    assert orch._get_env_value("MISSING_KEY") is None

    selected = new_orchestrator(tmp_path / "selected", bonded=True)
    selected.snapshot.flagship_provider = "nvidia"
    assert selected._get_selected_provider() == "nvidia"

    monkeypatch.setattr(onboarding_module, "_load_persisted_provider", lambda: "openai-codex")
    assert selected._get_selected_provider() == "openai-codex"

    monkeypatch.setattr(onboarding_module, "_load_persisted_provider", lambda: "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    inferred = new_orchestrator(tmp_path / "inferred", bonded=True)
    assert inferred._infer_provider_from_keys() == "openai"
    assert inferred._get_selected_provider() == "openai"

    class Vault:
        @staticmethod
        def load_config(path):
            return {"encryption": {"key_env_var": "CUSTOM_VAULT_KEY", "docker_secret": "custom_secret"}}

        @staticmethod
        def resolve_key_with_origin(env_var, docker_secret):
            assert (env_var, docker_secret) == ("CUSTOM_VAULT_KEY", "custom_secret")
            return "resolved-key", "env"

    monkeypatch.setitem(sys.modules, "src.connectors.vault", types.SimpleNamespace(CredentialVault=Vault))
    assert orch._has_vault_key_configured() is True

    class BrokenVault:
        @staticmethod
        def load_config(path):
            raise RuntimeError("vault config unreadable")

    monkeypatch.setitem(sys.modules, "src.connectors.vault", types.SimpleNamespace(CredentialVault=BrokenVault))
    monkeypatch.delenv("LANCELOT_VAULT_KEY", raising=False)
    assert orch._has_vault_key_configured() is False


def test_onboarding_save_error_and_recovery_command_paths(tmp_path, monkeypatch):
    orch = new_orchestrator(tmp_path, bonded=True)
    orch.env_file = str(tmp_path / "missing" / ".env")
    orch._write_env_values({"A": "1"}, "Section")
    assert not (tmp_path / "missing" / ".env").exists()

    orch.state = "COOLDOWN"
    orch.snapshot.cooldown_until = 9999999999
    response = orch.process("Arthur", "continue")
    assert "System is in cooldown" in response

    monkeypatch.setattr(
        onboarding_module.recovery_commands,
        "try_handle",
        lambda text, snapshot: "status recovery" if text == "STATUS" else None,
    )
    assert orch.process("Arthur", "STATUS") == "status recovery"

    complete = new_orchestrator(tmp_path / "complete", bonded=True)
    complete.user_file = str(tmp_path / "complete" / "USER-as-directory")
    os.makedirs(complete.user_file, exist_ok=True)
    complete._complete_onboarding()
    assert complete.state != "READY"
