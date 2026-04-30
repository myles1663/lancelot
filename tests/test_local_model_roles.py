from unittest.mock import MagicMock

from src.core.local_model_roles import (
    LocalModelRoleConfig,
    LocalModelRoleRouter,
    ROLE_SCRUB_REGION_FINDER,
    ROLE_SCRUB_SEGMENT_VERIFIER,
    ROLE_UTILITY,
    _clean_redacted_segment,
    load_local_model_role_configs,
    parse_scrub_regions,
)


def _configs():
    return {
        ROLE_SCRUB_REGION_FINDER: LocalModelRoleConfig(
            role=ROLE_SCRUB_REGION_FINDER,
            base_url="http://local-finder:8080",
            model="bonsai-1.7b",
            priority=10,
            timeout_s=3.0,
            max_input_chars=20000,
        ),
        ROLE_SCRUB_SEGMENT_VERIFIER: LocalModelRoleConfig(
            role=ROLE_SCRUB_SEGMENT_VERIFIER,
            base_url="http://local-verifier:8080",
            model="bonsai-8b",
            priority=9,
            timeout_s=4.0,
            max_input_chars=4000,
        ),
        ROLE_UTILITY: LocalModelRoleConfig(
            role=ROLE_UTILITY,
            base_url="http://local-verifier:8080",
            model="bonsai-8b",
            priority=1,
            timeout_s=30.0,
            max_input_chars=12000,
        ),
    }


def test_parse_scrub_regions_accepts_json_array_with_line_numbers():
    regions = parse_scrub_regions(
        '[{"line": 2, "label": "name", "confidence": 0.8, "reason": "person"}]',
        line_count=5,
    )

    assert len(regions) == 1
    assert regions[0].start_line == 2
    assert regions[0].end_line == 2
    assert regions[0].label == "name"
    assert regions[0].confidence == 0.8


def test_parse_scrub_regions_accepts_first_array_from_repeated_model_output():
    raw = """
    [{"line": "000002: Contact alice@example.com", "label": "email", "confidence": 0.9}]
    ```json
    [{"line": 1, "label": "noise", "confidence": 0.1}]
    ```
    """

    regions = parse_scrub_regions(raw, line_count=5)

    assert len(regions) == 1
    assert regions[0].start_line == 2
    assert regions[0].label == "email"


def test_clean_redacted_segment_discards_single_line_explanation_tail():
    cleaned = _clean_redacted_segment(
        "Contact [NAME] at [EMAIL]\nOkay, here is why...",
        original="Contact Alice at alice@example.com",
    )

    assert cleaned == "Contact [NAME] at [EMAIL]"


def test_role_router_uses_distinct_clients_for_scrub_roles():
    created = {}

    def client_factory(*, base_url, role):
        client = MagicMock()
        client.base_url = base_url
        client.role = role
        client.health.return_value = {"ready": True, "loaded": True, "status": "ok"}
        created[role] = client
        return client

    router = LocalModelRoleRouter(_configs(), client_factory=client_factory)

    assert router.client_for(ROLE_SCRUB_REGION_FINDER).base_url == "http://local-finder:8080"
    assert router.client_for(ROLE_SCRUB_SEGMENT_VERIFIER).base_url == "http://local-verifier:8080"
    assert router.client_for(ROLE_UTILITY).base_url == "http://local-verifier:8080"
    assert set(created) == {
        ROLE_SCRUB_REGION_FINDER,
        ROLE_SCRUB_SEGMENT_VERIFIER,
        ROLE_UTILITY,
    }


def test_status_prefers_runtime_model_reported_by_endpoint():
    def client_factory(*, base_url, role):
        client = MagicMock()
        client.health.return_value = {
            "ready": True,
            "loaded": True,
            "status": "ok",
            "model": f"runtime-{role}",
        }
        return client

    router = LocalModelRoleRouter(_configs(), client_factory=client_factory)

    status = router.status()

    assert status["roles"][ROLE_SCRUB_REGION_FINDER]["model"] == (
        f"runtime-{ROLE_SCRUB_REGION_FINDER}"
    )
    assert status["roles"][ROLE_UTILITY]["model"] == f"runtime-{ROLE_UTILITY}"


def test_status_uses_health_timeout_not_inference_timeout():
    clients = {}

    def client_factory(*, base_url, role):
        client = MagicMock()
        client.health.return_value = {"ready": True, "loaded": True, "status": "ok"}
        clients[role] = client
        return client

    configs = _configs()
    configs[ROLE_UTILITY] = LocalModelRoleConfig(
        role=ROLE_UTILITY,
        base_url="http://local-verifier:8080",
        model="bonsai-8b",
        priority=1,
        timeout_s=30.0,
        max_input_chars=12000,
        health_timeout_s=0.25,
    )
    router = LocalModelRoleRouter(configs, client_factory=client_factory)

    router.status()

    clients[ROLE_UTILITY].health.assert_called_once_with(timeout=0.25)


def test_region_finder_default_budget_matches_bounded_bonsai_window(monkeypatch):
    monkeypatch.delenv("LANCELOT_SCRUB_REGION_FINDER_MAX_CHARS", raising=False)

    configs = load_local_model_role_configs()

    assert configs[ROLE_SCRUB_REGION_FINDER].max_input_chars == 6000


def test_region_finder_returns_normalized_regions():
    client = MagicMock()
    client.complete.return_value = """
    ```json
    [{"line": 1, "label": "email", "confidence": 0.95}]
    ```
    """

    router = LocalModelRoleRouter(
        _configs(),
        client_factory=lambda **_kwargs: client,
    )

    regions = router.find_pii_regions("Contact alice@example.com")

    assert len(regions) == 1
    assert regions[0].start_line == 1
    assert regions[0].label == "email"
    assert "Return exactly one JSON array" in client.complete.call_args.args[0]


def test_segment_verifier_preserves_role_boundary():
    client = MagicMock()
    client.complete.return_value = "Escalation owner [NAME]"
    router = LocalModelRoleRouter(
        _configs(),
        client_factory=lambda **_kwargs: client,
    )

    output = router.redact_segment(
        "Escalation owner Myles Hamilton",
        context="Project context",
        label="name",
    )

    assert output == "Escalation owner [NAME]"
    prompt = client.complete.call_args.args[0]
    assert "Suspicion label: name" in prompt
    assert "Project context" in prompt
    assert "Input: Contact Myles at myles@example.com for ticket 123." in prompt
    assert "Input: Escalation owner Bob at [EMAIL] for case 998877." in prompt
