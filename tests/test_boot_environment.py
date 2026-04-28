import logging

import boot


def test_boot_validation_does_not_warn_for_openai_codex(monkeypatch, caplog):
    monkeypatch.setenv("LANCELOT_PROVIDER", "openai-codex")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setattr(boot, "_codex_auth_available", lambda: True)

    with caplog.at_level(logging.INFO, logger="lancelot.gateway.boot"):
        env = boot._validate_boot_environment(api_token="token")

    assert env.provider == "openai-codex"
    assert env.credential_var == "CODEX_OAUTH"
    assert "No GEMINI_API_KEY set" not in caplog.text
    assert "LLM features may be unavailable" not in caplog.text


def test_boot_validation_warns_for_missing_api_key_provider(monkeypatch, caplog):
    monkeypatch.setenv("LANCELOT_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    with caplog.at_level(logging.WARNING, logger="lancelot.gateway.boot"):
        env = boot._validate_boot_environment(api_token="token")

    assert env.provider == "gemini"
    assert env.credential_var == "GEMINI_API_KEY"
    assert "No GEMINI_API_KEY set" in caplog.text
