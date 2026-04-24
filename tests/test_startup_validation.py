from src.core.health.monitor import HealthCheck, HealthMonitor
from src.core.startup_validation import (
    set_startup_validation_report,
    startup_validation_health_details,
    startup_validation_ready,
    validate_startup_environment,
)


def _valid_env(**overrides):
    env = {
        "LANCELOT_PROVIDER": "gemini",
        "GEMINI_API_KEY": "gemini-key",
        "LANCELOT_API_TOKEN": "api-token",
        "LANCELOT_VAULT_KEY": "vault-key",
        "LANCELOT_AUTH_PROVIDER": "local",
        "WARROOM_USERNAME": "Myles",
        "WARROOM_PASSWORD": "hashed-password",
        "LOCAL_LLM_URL": "http://local-llm:8080",
        "HOST_AGENT_URL": "http://host.docker.internal:9111",
        "UAB_DAEMON_URL": "http://host.docker.internal:7900",
    }
    env.update(overrides)
    return env


def test_valid_startup_config_is_ready():
    report = validate_startup_environment(_valid_env())

    assert report.ready is True
    assert report.can_start is True
    assert report.blocked == []
    assert report.degraded == []


def test_missing_provider_key_degrades_model_route():
    env = _valid_env(GEMINI_API_KEY="")
    report = validate_startup_environment(env)

    assert report.ready is False
    assert report.can_start is True
    assert any("GEMINI_API_KEY" in reason for reason in report.degraded)
    assert report.blocked == []


def test_missing_api_token_blocks_production_mode():
    env = _valid_env(LANCELOT_API_TOKEN="")
    report = validate_startup_environment(env)

    assert report.ready is False
    assert report.can_start is False
    assert "LANCELOT_API_TOKEN" in report.required_missing
    assert any("LANCELOT_API_TOKEN" in reason for reason in report.blocked)


def test_boot_vault_satisfies_vault_requirement_after_env_scrub():
    env = _valid_env(LANCELOT_VAULT_KEY="")
    report = validate_startup_environment(env, vault_configured=True)

    assert "LANCELOT_VAULT_KEY" not in report.required_missing
    assert not any("LANCELOT_VAULT_KEY" in reason for reason in report.blocked)


def test_missing_api_token_allowed_only_in_explicit_dev_mode():
    env = _valid_env(LANCELOT_API_TOKEN="", LANCELOT_DEV_MODE="true")
    report = validate_startup_environment(env)

    assert report.can_start is True
    assert not any("LANCELOT_API_TOKEN" in reason for reason in report.blocked)
    assert any("LANCELOT_DEV_MODE=true" in warning for warning in report.warnings)


def test_public_local_model_url_fails_closed():
    env = _valid_env(LOCAL_LLM_URL="https://api.example.com/v1")
    report = validate_startup_environment(env)

    assert report.ready is False
    assert report.can_start is False
    assert any("LOCAL_LLM_URL" in reason for reason in report.blocked)
    assert any("local control-plane boundary" in reason for reason in report.blocked)


def test_host_bridge_url_fails_closed_when_public():
    env = _valid_env(HOST_AGENT_URL="https://agent.example.com")
    report = validate_startup_environment(env)

    assert report.can_start is False
    assert any("HOST_AGENT_URL" in reason for reason in report.blocked)


def test_health_monitor_exposes_startup_validation_details():
    report = validate_startup_environment(_valid_env(GEMINI_API_KEY=""))
    set_startup_validation_report(report)

    monitor = HealthMonitor(
        checks=[
            HealthCheck(
                name="startup_validation",
                check_fn=startup_validation_ready,
                degraded_reason="Startup validation failed",
                snapshot_details_fn=startup_validation_health_details,
            )
        ]
    )

    snapshot = monitor.compute_snapshot()

    assert snapshot.ready is False
    assert snapshot.startup_validation_ready is False
    assert snapshot.startup_validation["provider"] == "gemini"
    assert any("GEMINI_API_KEY" in reason for reason in snapshot.degraded_reasons)
