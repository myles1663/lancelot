from src.core.model_usage_policy import (
    FRONTIER_SCRUB_PREFERRED,
    LOCAL_EXECUTION_DISABLED,
    LOCAL_EXECUTION_LOW_RISK_ONLY,
    clear_frontier_scrub_fallback,
    get_model_usage_status,
    init_model_usage_policy,
    record_frontier_scrub_fallback,
    set_local_model_availability,
    update_model_usage_policy,
)


def test_policy_defaults(tmp_data_dir):
    init_model_usage_policy(str(tmp_data_dir))
    status = get_model_usage_status()
    assert status["local_execution_mode"] == LOCAL_EXECUTION_LOW_RISK_ONLY
    assert status["frontier_scrub_mode"] == "required"


def test_policy_persists_updates(tmp_data_dir):
    init_model_usage_policy(str(tmp_data_dir))
    update_model_usage_policy(
        local_execution_mode=LOCAL_EXECUTION_DISABLED,
        frontier_scrub_mode=FRONTIER_SCRUB_PREFERRED,
    )

    init_model_usage_policy(str(tmp_data_dir))
    status = get_model_usage_status()
    assert status["local_execution_mode"] == LOCAL_EXECUTION_DISABLED
    assert status["frontier_scrub_mode"] == FRONTIER_SCRUB_PREFERRED


def test_runtime_status_tracks_availability_and_fallback(tmp_data_dir):
    init_model_usage_policy(str(tmp_data_dir))
    set_local_model_availability(
        True,
        "Local model connected",
        loaded=True,
        ready=True,
        last_verified_at="2026-04-17T00:00:00Z",
        last_checked_at="2026-04-17T00:00:00Z",
        consecutive_failures=0,
        last_smoke_elapsed_ms=12.5,
    )
    record_frontier_scrub_fallback("router offline")
    status = get_model_usage_status()
    assert status["local_execution_available"] is True
    assert status["local_model_loaded"] is True
    assert status["local_model_ready"] is True
    assert status["local_model_status"] == "ready"
    assert status["frontier_scrub_fallback_active"] is True
    assert status["frontier_scrub_fallback_count"] == 1
    clear_frontier_scrub_fallback()
    assert get_model_usage_status()["frontier_scrub_fallback_active"] is False
