from src.core.orch_helpers.intent_helpers import needs_research


def test_needs_research_for_live_health_status_prompt():
    assert needs_research("Run a health check and summarize the current system status.") is True


def test_needs_research_for_runtime_status_prompt():
    assert needs_research("What is the current runtime status of the service?") is True
