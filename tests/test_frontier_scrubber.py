from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.frontier_scrubber import (
    LocalPIIScrubber,
    PIIScrubError,
    PIIScrubPayloadError,
    detect_frontier_pii_categories,
    normalize_frontier_pii_text,
    redact_frontier_pii_deterministically,
    split_text_for_frontier_redaction,
)
from src.core.local_model_roles import ScrubRegion
from src.core.model_usage_policy import (
    FRONTIER_SCRUB_DISABLED,
    FRONTIER_SCRUB_PREFERRED,
    FRONTIER_SCRUB_REQUIRED,
    get_model_usage_status,
    init_model_usage_policy,
    record_frontier_scrub_fallback,
    update_model_usage_policy,
)


def _router_result(*, executed: bool, output: str = "", error: str | None = None):
    return SimpleNamespace(
        executed=executed,
        output=output,
        decision=SimpleNamespace(error=error),
    )


@pytest.fixture(autouse=True)
def _init_model_policy(tmp_data_dir):
    init_model_usage_policy(str(tmp_data_dir))
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_REQUIRED)
    yield


def test_scrub_text_routes_through_model_router_with_structured_result():
    router = MagicMock()
    router.route.return_value = _router_result(executed=True, output="Contact [EMAIL]")
    scrubber = LocalPIIScrubber(model_router=router)

    result = scrubber.scrub_text("Contact alice@example.com")

    assert result.text == "Contact [EMAIL]"
    assert result.source == "model_router"
    assert result.fallback_used is False
    assert result.pre_scrubbed is False
    assert result.pre_scrub_source is None
    assert result.local_verification_used is True
    assert result.detected_categories == ("email",)
    assert result.residual_categories == ()
    router.route.assert_called_once_with("redact", "Contact alice@example.com")


def test_scrub_text_falls_back_to_direct_local_model_after_invalid_router_candidate():
    router = MagicMock()
    router.route.return_value = _router_result(
        executed=True,
        output="Contact alice@example.com",
    )
    local_model = MagicMock()
    local_model.is_healthy.return_value = True
    local_model.redact.return_value = "Contact [EMAIL]"
    scrubber = LocalPIIScrubber(model_router=router, local_model=local_model)

    result = scrubber.scrub_text("Contact alice@example.com")

    assert result.text == "Contact [EMAIL]"
    assert result.source == "local_model"
    assert result.fallback_used is False
    local_model.redact.assert_called_once_with("Contact alice@example.com")


def test_required_mode_blocks_when_local_scrub_is_unavailable():
    router = MagicMock()
    router.route.return_value = _router_result(executed=False, error="router offline")
    scrubber = LocalPIIScrubber(model_router=router)

    with pytest.raises(PIIScrubError, match="required"):
        scrubber.scrub_text("secret data")


def test_required_mode_uses_deterministic_fallback_when_local_model_inference_fails():
    local_model = MagicMock()
    local_model.is_healthy.return_value = True
    local_model.redact.side_effect = RuntimeError("HTTP 500 from local-llm")
    scrubber = LocalPIIScrubber(local_model=local_model)

    result = scrubber.scrub_text("Contact alice@example.com")
    status = get_model_usage_status()

    assert result.text == "Contact [EMAIL]"
    assert result.source == "deterministic_local"
    assert result.scrubbed is True
    assert result.fallback_used is True
    assert result.residual_categories == ()
    assert status["frontier_scrub_fallback_active"] is True
    assert "deterministic local scrub fallback used" in (
        status["last_frontier_scrub_fallback_reason"] or ""
    )


def test_router_timeout_uses_deterministic_fallback_without_second_model_attempt():
    router = MagicMock()
    router.route.side_effect = TimeoutError("timed out")
    local_model = MagicMock()
    scrubber = LocalPIIScrubber(model_router=router, local_model=local_model)

    result = scrubber.scrub_text("Continue with the existing plan.")

    assert result.text == "Continue with the existing plan."
    assert result.source == "deterministic_local"
    assert result.fallback_used is True
    assert result.residual_categories == ()
    local_model.is_healthy.assert_not_called()
    local_model.redact.assert_not_called()


def test_recent_scrub_timeout_backoff_skips_repeated_model_attempts():
    record_frontier_scrub_fallback("Local redaction router failed: timed out")
    router = MagicMock()
    local_model = MagicMock()
    scrubber = LocalPIIScrubber(model_router=router, local_model=local_model)

    result = scrubber.scrub_text("Continue with the existing plan.")

    assert result.text == "Continue with the existing plan."
    assert result.source == "deterministic_local"
    assert result.fallback_used is True
    assert "retry backoff active" in (result.reason or "")
    router.route.assert_not_called()
    local_model.is_healthy.assert_not_called()
    local_model.redact.assert_not_called()


def test_deterministic_frontier_redaction_clears_structured_pii():
    redacted = redact_frontier_pii_deterministically(
        "Email alice@example.com, phone 415-555-1212, SSN 123-45-6789, "
        "DOB: 01/02/1990, card 4111 1111 1111 1111"
    )

    assert detect_frontier_pii_categories(redacted) == set()
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    assert "[SSN]" in redacted
    assert "[DATE_OF_BIRTH]" in redacted
    assert "[CREDIT_CARD]" in redacted


def test_deterministic_frontier_redaction_preserves_newlines_for_direct_matches():
    redacted = redact_frontier_pii_deterministically(
        "Line one\nEmail alice@example.com\nLine three"
    )

    assert redacted == "Line one\nEmail [EMAIL]\nLine three"


def test_deterministic_frontier_redaction_handles_contextual_names_and_case_ids():
    redacted = redact_frontier_pii_deterministically(
        "Escalation owner Bob at bob@example.com for case 998877."
    )

    assert redacted == "Escalation owner [NAME] at [EMAIL] for case [ACCOUNT_ID]."


def test_deterministic_frontier_redaction_handles_full_customer_name_before_email():
    redacted = redact_frontier_pii_deterministically(
        "Customer Alice Smith, email alice@example.com, phone 415-555-1212."
    )

    assert redacted == "Customer [NAME], email [EMAIL], phone [PHONE]."


def test_deterministic_frontier_redaction_handles_secrets_and_private_urls():
    redacted = redact_frontier_pii_deterministically(
        "Reset code 492817, password hunter2, API key sk-live-1234567890abcdef, "
        "GitHub token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456, "
        "reset URL https://portal.example.org/reset?token=abc123, "
        "rotate secret sk-test-XYZ987."
    )

    assert detect_frontier_pii_categories(redacted) == set()
    assert redacted.count("[SECRET]") == 5
    assert "[URL]" in redacted
    assert "492817" not in redacted
    assert "hunter2" not in redacted
    assert "sk-live-1234567890abcdef" not in redacted
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in redacted
    assert "https://portal.example.org/reset?token=abc123" not in redacted
    assert "sk-test-XYZ987" not in redacted


def test_deterministic_frontier_redaction_does_not_redact_benign_secret_word():
    text = "This note says secret data but contains no credential value."

    assert redact_frontier_pii_deterministically(text) == text
    assert detect_frontier_pii_categories(text) == set()


def test_deterministic_frontier_redaction_handles_address_and_lives_at_name():
    redacted = redact_frontier_pii_deterministically(
        "Myles Hamilton lives at 404 Nowhere Lane, Springfield, IL 62704."
    )

    assert redacted == "[NAME] lives at [ADDRESS]."
    assert detect_frontier_pii_categories(redacted) == set()


def test_large_text_is_redacted_in_local_model_chunks():
    local_model = MagicMock()
    local_model.is_healthy.return_value = True
    local_model.redact.side_effect = lambda chunk: chunk
    scrubber = LocalPIIScrubber(local_model=local_model)
    text = ("Customer alice@example.com " + ("x" * 1000) + "\n") * 8

    result = scrubber.scrub_text(text)

    assert result.source == "chunked_local_model_after_deterministic_prescrub"
    assert result.text.count("[EMAIL]") == 8
    assert result.residual_categories == ()
    assert result.pre_scrubbed is True
    assert result.pre_scrub_source == "deterministic_local"
    assert result.local_verification_used is True
    assert local_model.redact.call_count > 1
    assert all(len(call.args[0]) <= 6000 for call in local_model.redact.call_args_list)


def test_large_text_uses_role_cascade_before_chunking():
    class FakeRoles:
        def __init__(self):
            self.finder_inputs = []
            self.verified_segments = []

        def find_pii_regions(self, text, **_kwargs):
            self.finder_inputs.append(text)
            return [ScrubRegion(start_line=3, end_line=3, label="name", confidence=0.91)]

        def redact_segment(self, segment, *, context="", label="pii"):
            self.verified_segments.append(segment)
            return segment.replace("Myles Hamilton", "[NAME]")

    roles = FakeRoles()
    local_model = MagicMock()
    scrubber = LocalPIIScrubber(local_model=local_model, local_model_roles=roles)
    lines = [
        "Summary line",
        "Customer email alice@example.com",
        "Customer success Myles Hamilton",
    ] + [f"filler {idx} " + ("x" * 120) for idx in range(80)]
    text = "\n".join(lines)

    result = scrubber.scrub_text(text)

    assert result.source == "scrub_cascade"
    assert "[EMAIL]" in result.text
    assert "[NAME]" in result.text
    assert "alice@example.com" not in result.text
    assert "Myles Hamilton" not in result.text
    assert result.pre_scrubbed is True
    assert result.pre_scrub_source == "deterministic_local"
    assert result.local_verification_used is True
    assert result.scrub_stages == (
        "deterministic_prescrub",
        "scrub_region_finder",
        "scrub_segment_verifier",
    )
    assert "name" in result.detected_categories
    assert len(roles.finder_inputs) == 1
    assert "[EMAIL]" in roles.finder_inputs[0]
    assert len(roles.finder_inputs[0].splitlines()) < len(text.splitlines())
    assert "2|Customer email [EMAIL]" in roles.finder_inputs[0]
    assert "3|Customer success Myles Hamilton" in roles.finder_inputs[0]
    assert "filler 79" not in roles.finder_inputs[0]
    assert len(roles.verified_segments) == 1
    assert "Customer success" in roles.verified_segments[0]
    local_model.redact.assert_not_called()


def test_oversized_region_finder_input_uses_bounded_windows(monkeypatch):
    import src.core.frontier_scrubber as frontier_scrubber

    class FakeRoles:
        def __init__(self):
            self.finder_inputs = []
            self.verified_segments = []

        def config_for(self, _role):
            return SimpleNamespace(max_input_chars=260)

        def find_pii_regions(self, text, **kwargs):
            assert kwargs["text_is_numbered"] is True
            self.finder_inputs.append(text)
            regions = []
            for line in text.splitlines():
                prefix, body = line.split("|", 1)
                if "Customer success" in body:
                    line_number = int(prefix)
                    regions.append(
                        ScrubRegion(
                            start_line=line_number,
                            end_line=line_number,
                            label="name",
                            confidence=0.92,
                        )
                    )
            return regions

        def redact_segment(self, segment, *, context="", label="pii"):
            self.verified_segments.append(segment)
            return segment.replace("Myles Hamilton", "[NAME]")

    monkeypatch.setattr(frontier_scrubber, "_FRONTIER_SCRUB_CASCADE_MIN_CHARS", 0)
    roles = FakeRoles()
    scrubber = LocalPIIScrubber(local_model_roles=roles)
    lines = ["Customer email alice@example.com"] + [
        "Customer success Myles Hamilton" for _idx in range(30)
    ]
    text = "\n".join(lines)

    result = scrubber.scrub_text(text)

    assert result.source == "scrub_cascade"
    assert "alice@example.com" not in result.text
    assert "Myles Hamilton" not in result.text
    assert result.local_verification_used is True
    assert result.scrub_stages == (
        "deterministic_prescrub",
        "scrub_region_finder_chunked",
        "scrub_segment_verifier",
    )
    assert len(roles.finder_inputs) > 1
    assert all(len(item) <= 260 for item in roles.finder_inputs)
    assert len(roles.verified_segments) == 30


def test_large_payload_deterministic_secret_prescrub_expands_nearby_semantic_lines():
    class FakeRoles:
        def __init__(self):
            self.finder_inputs = []
            self.verified_segments = []

        def find_pii_regions(self, text, **_kwargs):
            self.finder_inputs.append(text)
            return [ScrubRegion(start_line=3, end_line=3, label="name", confidence=0.91)]

        def redact_segment(self, segment, *, context="", label="pii"):
            self.verified_segments.append(segment)
            return segment.replace("Myles Hamilton", "[NAME]")

    roles = FakeRoles()
    scrubber = LocalPIIScrubber(local_model_roles=roles)
    lines = [
        "Summary line",
        "Customer email alice@example.com",
        "Customer success Myles Hamilton",
        "GitHub token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    ] + [f"filler {idx} " + ("x" * 120) for idx in range(80)]
    text = "\n".join(lines)

    result = scrubber.scrub_text(text)

    assert "alice@example.com" not in result.text
    assert "Myles Hamilton" not in result.text
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in result.text
    assert result.residual_categories == ()
    assert "2|Customer email [EMAIL]" in roles.finder_inputs[0]
    assert "3|Customer success Myles Hamilton" in roles.finder_inputs[0]
    assert "4|GitHub token [SECRET]" in roles.finder_inputs[0]
    assert "filler 79" not in roles.finder_inputs[0]


def test_large_deterministic_clean_payload_skips_role_cascade():
    roles = MagicMock()
    roles.find_pii_regions.return_value = []
    scrubber = LocalPIIScrubber(local_model_roles=roles)

    result = scrubber.scrub_text("Contact alice@example.com\n" + ("x" * 7000))

    assert result.source == "deterministic_local"
    assert result.text.startswith("Contact [EMAIL]")
    assert result.scrubbed is True
    assert result.fallback_used is False
    assert result.scrub_stages == ("deterministic_prescrub", "deterministic_validation")
    roles.find_pii_regions.assert_not_called()


def test_short_deterministic_prescrub_skips_role_cascade_when_clean():
    roles = MagicMock()
    scrubber = LocalPIIScrubber(local_model_roles=roles)

    result = scrubber.scrub_text("Contact Myles at myles@example.com for ticket 123.")

    assert result.source == "deterministic_local"
    assert result.text == "Contact [NAME] at [EMAIL] for ticket [ACCOUNT_ID]."
    assert result.fallback_used is False
    assert result.local_verification_used is False
    assert result.scrub_stages == ("deterministic_prescrub", "deterministic_validation")
    roles.find_pii_regions.assert_not_called()


def test_deterministic_prescrub_redacts_approver_name():
    text = "Review approver Myles Hamilton"

    assert redact_frontier_pii_deterministically(text) == "Review approver [NAME]"
    assert detect_frontier_pii_categories(text) == {"name"}


def test_deterministic_prescrub_redacts_account_lead_name():
    text = "Account lead Myles Hamilton"

    assert redact_frontier_pii_deterministically(text) == "Account lead [NAME]"
    assert detect_frontier_pii_categories(text) == {"name"}


def test_deterministic_prescrub_redacts_account_lead_name_before_action_verb():
    text = "Account lead Myles Hamilton reviewing access posture."

    assert (
        redact_frontier_pii_deterministically(text)
        == "Account lead [NAME] reviewing access posture."
    )
    assert detect_frontier_pii_categories(text) == {"name"}


def test_large_clean_payload_without_privacy_cues_skips_role_cascade():
    roles = MagicMock()
    scrubber = LocalPIIScrubber(local_model_roles=roles)
    text = "\n".join(
        f"Line {idx}: harmless engineering status update for dashboard latency validation."
        for idx in range(160)
    )

    result = scrubber.scrub_text(text)

    assert result.source == "deterministic_clean"
    assert result.scrubbed is False
    assert result.fallback_used is False
    assert result.local_verification_used is False
    assert result.scrub_stages == ("deterministic_detection",)
    roles.find_pii_regions.assert_not_called()


def test_large_payload_with_broad_privacy_cue_without_private_value_skips_role_cascade():
    roles = MagicMock()
    roles.find_pii_regions.return_value = []
    scrubber = LocalPIIScrubber(local_model_roles=roles)
    text = "Customer requested a review of account access policy.\n" + "\n".join(
        f"Line {idx}: harmless engineering status update for dashboard latency validation."
        for idx in range(160)
    )

    result = scrubber.scrub_text(text)

    assert result.source == "deterministic_clean"
    assert result.scrubbed is False
    assert result.local_verification_used is False
    roles.find_pii_regions.assert_not_called()


def test_chunked_router_timeout_uses_deterministic_fallback_without_direct_model_attempts():
    router = MagicMock()
    router.route.side_effect = TimeoutError("timed out")
    local_model = MagicMock()
    scrubber = LocalPIIScrubber(model_router=router, local_model=local_model)
    text = ("Customer alice@example.com " + ("x" * 1000) + "\n") * 8

    result = scrubber.scrub_text(text)

    assert result is not None
    assert result.source == "chunked_deterministic_local_after_deterministic_prescrub"
    assert result.text.count("[EMAIL]") == 8
    assert result.fallback_used is True
    assert result.pre_scrubbed is True
    assert result.pre_scrub_source == "deterministic_local"
    assert result.local_verification_used is False
    assert result.residual_categories == ()
    assert router.route.call_count > 1
    local_model.is_healthy.assert_not_called()
    local_model.redact.assert_not_called()


def test_split_text_for_frontier_redaction_preserves_payload():
    text = ("alpha beta gamma\n" * 1000).strip()

    chunks = split_text_for_frontier_redaction(text, max_chars=500)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_preferred_mode_returns_original_and_records_degraded_fallback():
    router = MagicMock()
    router.route.return_value = _router_result(executed=False, error="router offline")
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_PREFERRED)
    scrubber = LocalPIIScrubber(model_router=router)

    result = scrubber.scrub_text("secret data")
    status = get_model_usage_status()

    assert result.text == "secret data"
    assert result.source == "frontier_fallback"
    assert result.fallback_used is True
    assert "router offline" in (result.reason or "")
    assert status["frontier_scrub_fallback_active"] is True


def test_disabled_mode_skips_local_redaction_attempts():
    router = MagicMock()
    local_model = MagicMock()
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_DISABLED)
    scrubber = LocalPIIScrubber(model_router=router, local_model=local_model)

    result = scrubber.scrub_text("DOB: 01/02/1990")

    assert result.text == "DOB: 01/02/1990"
    assert result.source == "policy_disabled"
    assert result.fallback_used is False
    router.route.assert_not_called()
    local_model.redact.assert_not_called()


def test_scrub_payload_redacts_nested_strings_and_preserves_passthrough_keys():
    router = MagicMock()

    def route_side_effect(task_type, text):
        assert task_type == "redact"
        return _router_result(
            executed=True,
            output=(
                text.replace("alice@example.com", "[EMAIL]")
                .replace("415-555-1212", "[PHONE]")
                .replace("4111 1111 1111 1111", "[CARD]")
            ),
        )

    router.route.side_effect = route_side_effect
    scrubber = LocalPIIScrubber(model_router=router)
    payload = {
        "id": "tool-call-1",
        "content": [
            {
                "type": "tool_result",
                "result": {
                    "customer_email": "alice@example.com",
                    "nested": [{"phone": "415-555-1212"}],
                },
            }
        ],
        "output": {"payment_card": "4111 1111 1111 1111"},
    }

    scrubbed = scrubber.scrub_payload(payload)

    assert scrubbed["id"] == "tool-call-1"
    assert scrubbed["content"][0]["type"] == "tool_result"
    assert scrubbed["content"][0]["result"]["customer_email"] == "[EMAIL]"
    assert scrubbed["content"][0]["result"]["nested"][0]["phone"] == "[PHONE]"
    assert scrubbed["output"]["payment_card"] == "[CARD]"


def test_scrub_payload_with_audit_returns_nested_event_paths():
    router = MagicMock()

    def route_side_effect(task_type, text):
        assert task_type == "redact"
        return _router_result(
            executed=True,
            output=(
                text.replace("alice@example.com", "[EMAIL]")
                .replace("415-555-1212", "[PHONE]")
            ),
        )

    router.route.side_effect = route_side_effect
    scrubber = LocalPIIScrubber(model_router=router)

    scrubbed, events = scrubber.scrub_payload_with_audit(
        {
            "content": [{"result": {"email": "alice@example.com"}}],
            "output": {"phone": "415-555-1212"},
        }
    )

    assert scrubbed["content"][0]["result"]["email"] == "[EMAIL]"
    assert scrubbed["output"]["phone"] == "[PHONE]"
    assert {event.path for event in events} == {
        "root.content[0].result.email",
        "root.output.phone",
    }
    assert all(event.detected_categories for event in events)


def test_scrub_payload_with_audit_records_large_payload_scrub_pipeline():
    local_model = MagicMock()
    local_model.is_healthy.return_value = True
    local_model.redact.side_effect = lambda chunk: chunk
    scrubber = LocalPIIScrubber(local_model=local_model)
    text = ("Customer alice@example.com " + ("x" * 1000) + "\n") * 8

    scrubbed, events = scrubber.scrub_payload_with_audit({"content": text})

    assert scrubbed["content"].count("[EMAIL]") == 8
    assert len(events) == 1
    event = events[0]
    assert event.path == "root.content"
    assert event.source == "chunked_local_model_after_deterministic_prescrub"
    assert event.pre_scrubbed is True
    assert event.pre_scrub_source == "deterministic_local"
    assert event.local_verification_used is True


def test_scrub_payload_with_audit_surfaces_blocking_path():
    router = MagicMock()
    router.route.return_value = _router_result(
        executed=False,
        error="router offline",
    )
    scrubber = LocalPIIScrubber(model_router=router)

    with pytest.raises(PIIScrubPayloadError) as excinfo:
        scrubber.scrub_payload_with_audit({"content": {"email": "alice@example.com"}})

    assert excinfo.value.path == "root.content.email"
    assert excinfo.value.detected_categories == ("email",)
    assert "required" in str(excinfo.value).lower()


def test_detect_frontier_pii_categories_catches_obfuscated_variants():
    categories = detect_frontier_pii_categories(
        "Email ali\u200bce @ example . com, "
        "SSN 123\u200b-\u200b45\u200b-\u200b6789, "
        "phone 415\u2011555\u20111212, "
        "DOB:\u200b01\u201102\u20111990, "
        "card 4111\u200b1111\u200b1111\u200b1111"
    )

    assert categories == {
        "credit_card",
        "date_of_birth",
        "email",
        "phone",
        "ssn",
    }


def test_normalize_frontier_pii_text_strips_zero_width_and_separator_noise():
    normalized = normalize_frontier_pii_text("ali\u200bce @ example . com and 415\u2011555\u20111212")
    assert normalized == "alice@example.com and 415-555-1212"


def test_scrubber_status_exposes_runtime_policy():
    scrubber = LocalPIIScrubber()
    status = scrubber.status()
    assert status["frontier_scrub_mode"] == FRONTIER_SCRUB_REQUIRED
