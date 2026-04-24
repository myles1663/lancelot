import os
import types

from src.ui.onboarding import OnboardingOrchestrator
from src.ui import onboarding as onboarding_module


def test_get_env_value_ignores_none_key(tmp_data_dir):
    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))

    assert orch._get_env_value(None) is None


def test_get_env_value_reads_bootstrapped_secret_cache(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("WARROOM_USERNAME", raising=False)
    cache_module = types.SimpleNamespace(
        is_bootstrapped=lambda: True,
        get=lambda key, default="": {"WARROOM_USERNAME": "Myles"}.get(key, default),
    )
    monkeypatch.setitem(__import__("sys").modules, "secret_cache", cache_module)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
    orch.env_file = str(tmp_data_dir / "isolated.env")

    assert orch._get_env_value("WARROOM_USERNAME") == "Myles"


def test_infer_provider_from_keys_skips_oauth_only_entries(tmp_data_dir, monkeypatch):
    user_file = tmp_data_dir / "USER.md"
    user_file.write_text("Arthur", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LANCELOT_PROVIDER", raising=False)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))

    assert orch._infer_provider_from_keys() == "anthropic"


def test_determine_state_handles_oauth_only_provider_without_env_var(tmp_data_dir, monkeypatch):
    user_file = tmp_data_dir / "USER.md"
    user_file.write_text("Arthur", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_PROVIDER", "openai-codex")
    monkeypatch.delenv("LANCELOT_PROVIDER_MODE", raising=False)
    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: False)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
    orch.env_file = str(tmp_data_dir / "isolated.env")
    orch.state = orch._determine_state()

    assert orch.state == "HANDSHAKE"


def test_determine_state_accepts_codex_cli_auth(tmp_data_dir, monkeypatch):
    user_file = tmp_data_dir / "USER.md"
    user_file.write_text("Arthur", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_PROVIDER", "openai-codex")
    monkeypatch.delenv("LANCELOT_PROVIDER_MODE", raising=False)
    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: True)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
    orch.env_file = str(tmp_data_dir / "isolated.env")
    orch.state = orch._determine_state()

    assert orch.state == "PROVIDER_MODE_SELECTION"


def test_flagship_selection_prefers_codex_cli_auth(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: True)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
    orch.env_file = str(tmp_data_dir / "isolated.env")
    orch.process("Arthur", "Hello")

    response = orch.process("Arthur", "6")

    assert orch.state == "PROVIDER_MODE_SELECTION"
    assert "Codex CLI Auth Detected" in response
    assert orch._get_env_value("LANCELOT_PROVIDER") == "openai-codex"
    assert orch._get_env_value("LANCELOT_AUTH_MODE") == "OAUTH"


def test_has_security_tokens_accepts_docker_secret_vault_key(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("LANCELOT_OWNER_TOKEN", "owner-token")
    monkeypatch.setenv("LANCELOT_API_TOKEN", "api-token")
    monkeypatch.delenv("LANCELOT_VAULT_KEY", raising=False)
    monkeypatch.setattr(onboarding_module.OnboardingOrchestrator, "_has_vault_key_configured", lambda self: True)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))

    assert orch._has_security_tokens() is True


def test_determine_state_prefers_durable_provider_over_stale_env(tmp_data_dir, monkeypatch):
    user_file = tmp_data_dir / "USER.md"
    user_file.write_text("Arthur", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    monkeypatch.setenv("LANCELOT_PROVIDER_MODE", "sdk")
    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "none")
    monkeypatch.setenv("LANCELOT_AUTH_PROVIDER", "local")
    monkeypatch.setenv("WARROOM_USERNAME", "Arthur")
    monkeypatch.setenv("WARROOM_PASSWORD", "password")
    monkeypatch.setenv("LANCELOT_OWNER_TOKEN", "owner-token")
    monkeypatch.setenv("LANCELOT_API_TOKEN", "api-token")
    monkeypatch.setattr(onboarding_module, "_load_persisted_provider", lambda: "openai-codex")
    monkeypatch.setattr(onboarding_module, "_has_codex_cli_auth", lambda: True)
    monkeypatch.setattr(onboarding_module.OnboardingOrchestrator, "_has_vault_key_configured", lambda self: True)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
    orch.snapshot.local_model_status = "verified"
    orch.env_file = str(tmp_data_dir / "isolated.env")

    assert orch._get_selected_provider() == "openai-codex"
    assert orch._determine_state() == "READY"


def test_determine_state_requires_auth_model_after_comms(tmp_data_dir, monkeypatch):
    user_file = tmp_data_dir / "USER.md"
    user_file.write_text("Arthur", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("LANCELOT_PROVIDER_MODE", "sdk")
    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "none")
    monkeypatch.delenv("LANCELOT_AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("WARROOM_USERNAME", raising=False)
    monkeypatch.delenv("WARROOM_PASSWORD", raising=False)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
    orch.snapshot.local_model_status = "verified"
    orch.env_file = str(tmp_data_dir / "isolated.env")

    assert orch._determine_state() == "AUTH_MODEL_SELECTION"


def test_determine_state_infers_local_auth_for_upgraded_install(tmp_data_dir, monkeypatch):
    user_file = tmp_data_dir / "USER.md"
    user_file.write_text("Arthur", encoding="utf-8")
    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("LANCELOT_PROVIDER_MODE", "sdk")
    monkeypatch.setenv("LANCELOT_COMMS_TYPE", "none")
    monkeypatch.setenv("WARROOM_USERNAME", "Arthur")
    monkeypatch.setenv("WARROOM_PASSWORD", "hashed-or-legacy-password")
    monkeypatch.setenv("LANCELOT_OWNER_TOKEN", "owner-token")
    monkeypatch.setenv("LANCELOT_API_TOKEN", "api-token")
    monkeypatch.setenv("LANCELOT_VAULT_KEY", "vault-key")
    monkeypatch.delenv("LANCELOT_AUTH_PROVIDER", raising=False)

    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))
    orch.snapshot.local_model_status = "verified"
    orch.env_file = str(tmp_data_dir / "isolated.env")

    assert orch._infer_auth_provider() == "local"
    assert orch._determine_state() == "READY"


def test_comms_selection_prompt_only_shows_supported_runtime_backends(tmp_data_dir):
    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))

    prompt = orch._comms_selection_prompt()

    assert "Telegram" in prompt
    assert "Google Chat" in prompt
    assert "Slack" not in prompt
    assert "Discord" not in prompt
    assert "Microsoft Teams" not in prompt
    assert "WhatsApp" not in prompt
    assert "Email" not in prompt
    assert "SMS" not in prompt


def test_comms_selection_rejects_unsupported_bidirectional_channel(tmp_data_dir):
    orch = OnboardingOrchestrator(data_dir=str(tmp_data_dir))

    response = orch._handle_comms_selection("slack")

    assert "not yet available as a bidirectional runtime backend" in response
    assert "Telegram" in response
    assert "Google Chat" in response
