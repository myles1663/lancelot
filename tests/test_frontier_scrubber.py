from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.frontier_scrubber import (
    LocalPIIScrubber,
    PIIScrubError,
    PIIScrubPayloadError,
    detect_frontier_pii_categories,
    normalize_frontier_pii_text,
)
from src.core.model_usage_policy import (
    FRONTIER_SCRUB_DISABLED,
    FRONTIER_SCRUB_PREFERRED,
    FRONTIER_SCRUB_REQUIRED,
    get_model_usage_status,
    init_model_usage_policy,
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
