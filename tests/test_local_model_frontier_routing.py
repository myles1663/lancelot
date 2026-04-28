from types import SimpleNamespace
from unittest.mock import MagicMock

from providers.base import GenerateResult
import orchestrator as orch_mod
from src.core.model_usage_policy import (
    FRONTIER_SCRUB_DISABLED,
    FRONTIER_SCRUB_PREFERRED,
    FRONTIER_SCRUB_REQUIRED,
    get_model_usage_status,
    init_model_usage_policy,
    update_model_usage_policy,
)


def _make_orchestrator():
    orch = orch_mod.LancelotOrchestrator.__new__(orch_mod.LancelotOrchestrator)
    orch.provider = MagicMock()
    orch.model_router = MagicMock()
    orch.local_model = None
    orch.receipt_service = MagicMock()
    orch.soul = None
    orch.context_env = SimpleNamespace(get_context_string=lambda: "CTX")
    orch.get_thinking_config = lambda: None
    orch.route_model = lambda prompt: "gpt-4o"
    orch.llm_call_with_retry = lambda fn, max_retries=3, base_delay=1.0: fn()
    return orch


def setup_function():
    init_model_usage_policy("test_data")
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_REQUIRED)


def test_text_only_generate_redacts_frontier_prompt_via_model_router():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(executed=True, output="Contact [EMAIL]")
    orch.provider.build_user_message.side_effect = lambda text, images=None: {"role": "user", "content": text}
    orch.provider.generate.return_value = GenerateResult(text="ok")

    result = orch._text_only_generate(
        "Contact me at alice@example.com",
        system_instruction="test",
        context_str="CTX",
    )

    assert result == "ok"
    orch.model_router.route.assert_called()
    call = orch.provider.generate.call_args
    assert call.kwargs["messages"][0]["content"] == "Contact [EMAIL]"


def test_frontier_tool_results_are_redacted_before_provider_message_build():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(executed=True, output='{"email":"[EMAIL]"}')
    orch.provider.build_tool_response_message.side_effect = lambda tool_results: tool_results

    tool_msg = orch._build_frontier_tool_response_message(
        [("call-1", "lookup_customer", '{"email":"alice@example.com"}')]
    )

    assert tool_msg == [("call-1", "lookup_customer", '{"email":"[EMAIL]"}')]
    orch.model_router.route.assert_called_once()


def test_redaction_falls_back_to_direct_local_model_when_router_missing():
    orch = _make_orchestrator()
    orch.model_router = None
    orch.local_model = MagicMock()
    orch.local_model.is_healthy.return_value = True
    orch.local_model.redact.return_value = "SSN [SSN]"

    redacted = orch._redact_for_frontier("SSN 123-45-6789")

    assert redacted == "SSN [SSN]"
    orch.local_model.redact.assert_called_once_with("SSN 123-45-6789")
    receipt = orch.receipt_service.create.call_args.args[0]
    assert receipt.action_name == "pii_scrub_applied"
    assert receipt.inputs["payload_path"] == "root"
    assert receipt.metadata["pii_categories"] == ["ssn"]


def test_router_output_with_residual_pii_falls_back_to_direct_local_model():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(
        executed=True,
        output="Contact me at alice@example.com",
        decision=SimpleNamespace(error=None),
    )
    orch.local_model = MagicMock()
    orch.local_model.is_healthy.return_value = True
    orch.local_model.redact.return_value = "Contact me at [EMAIL]"

    redacted = orch._redact_for_frontier("Contact me at alice@example.com")

    assert redacted == "Contact me at [EMAIL]"
    orch.local_model.redact.assert_called_once_with("Contact me at alice@example.com")


def test_required_scrub_blocks_when_router_output_still_contains_detectable_pii():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(
        executed=True,
        output="Contact me at alice@example.com",
        decision=SimpleNamespace(error=None),
    )
    orch.local_model = None
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_REQUIRED)

    try:
        orch._redact_for_frontier("Contact me at alice@example.com")
        assert False, "Expected residual PII to block frontier egress"
    except RuntimeError as exc:
        assert "detectable pii" in str(exc).lower()


def test_required_scrub_blocks_when_direct_local_model_output_still_contains_detectable_pii():
    orch = _make_orchestrator()
    orch.model_router = None
    orch.local_model = MagicMock()
    orch.local_model.is_healthy.return_value = True
    orch.local_model.redact.return_value = "Reach me at alice@example.com"
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_REQUIRED)

    try:
        orch._redact_for_frontier("Reach me at alice@example.com")
        assert False, "Expected residual PII to block frontier egress"
    except RuntimeError as exc:
        assert "detectable pii" in str(exc).lower()


def test_required_scrub_blocks_frontier_egress_when_local_scrub_unavailable():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(executed=False, decision=SimpleNamespace(error="router offline"))
    orch.local_model = None
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_REQUIRED)

    try:
        orch._redact_for_frontier("secret data")
        assert False, "Expected required scrub to block frontier egress"
    except RuntimeError as exc:
        assert "required" in str(exc).lower()


def test_preferred_scrub_allows_fallback_when_local_scrub_unavailable():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(executed=False, decision=SimpleNamespace(error="router offline"))
    orch.local_model = None
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_PREFERRED)

    assert orch._redact_for_frontier("secret data") == "secret data"
    receipt = orch.receipt_service.create.call_args.args[0]
    assert receipt.action_name == "pii_scrub_fallback"
    assert receipt.metadata["degraded_privacy"] is True
    assert receipt.outputs["fallback_used"] is True


def test_preferred_scrub_records_degraded_fallback_when_residual_pii_detected():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(
        executed=True,
        output="DOB: 01/02/1990",
        decision=SimpleNamespace(error=None),
    )
    orch.local_model = None
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_PREFERRED)

    assert orch._redact_for_frontier("DOB: 01/02/1990") == "DOB: 01/02/1990"

    status = get_model_usage_status()
    assert status["frontier_scrub_fallback_active"] is True
    assert "detectable pii" in (status["last_frontier_scrub_fallback_reason"] or "").lower()
    receipt = orch.receipt_service.create.call_args.args[0]
    assert receipt.action_name == "pii_scrub_fallback"
    assert receipt.metadata["residual_categories"] == ["date_of_birth"]


def test_disabled_scrub_skips_local_redaction_attempts():
    orch = _make_orchestrator()
    orch.model_router = MagicMock()
    orch.local_model = MagicMock()
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_DISABLED)

    assert orch._redact_for_frontier("secret data") == "secret data"
    orch.model_router.route.assert_not_called()
    orch.local_model.redact.assert_not_called()
    orch.receipt_service.create.assert_not_called()


def test_detect_frontier_pii_categories_catches_structured_variants():
    categories = orch_mod._detect_frontier_pii_categories(
        "Email alice+alerts@example.co.uk, SSN 123-45-6789, phone 415.555.1212, "
        "DOB: 01/02/1990, card 4111 1111 1111 1111"
    )

    assert categories == {
        "credit_card",
        "date_of_birth",
        "email",
        "phone",
        "ssn",
    }


def test_detect_frontier_pii_categories_normalizes_obfuscated_zero_width_and_unicode_separators():
    categories = orch_mod._detect_frontier_pii_categories(
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


def test_scrub_frontier_payload_redacts_nested_strings_under_arbitrary_keys():
    orch = _make_orchestrator()

    def redact_side_effect(task_type, text):
        assert task_type == "redact"
        return SimpleNamespace(
            executed=True,
            output=(
                text.replace("alice@example.com", "[EMAIL]")
                .replace("415-555-1212", "[PHONE]")
                .replace("4111 1111 1111 1111", "[CARD]")
            ),
            decision=SimpleNamespace(error=None),
        )

    orch.model_router.route.side_effect = redact_side_effect

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

    scrubbed = orch._scrub_frontier_payload(payload)

    assert scrubbed["id"] == "tool-call-1"
    result = scrubbed["content"][0]["result"]
    assert result["customer_email"] == "[EMAIL]"
    assert result["nested"][0]["phone"] == "[PHONE]"
    assert scrubbed["output"]["payment_card"] == "[CARD]"


def test_required_scrub_blocks_zero_width_obfuscated_residual_pii():
    orch = _make_orchestrator()
    orch.model_router.route.return_value = SimpleNamespace(
        executed=True,
        output="ali\u200bce @ example . com",
        decision=SimpleNamespace(error=None),
    )
    orch.local_model = None
    update_model_usage_policy(frontier_scrub_mode=FRONTIER_SCRUB_REQUIRED)

    try:
        orch._redact_for_frontier("alice@example.com")
        assert False, "Expected obfuscated residual PII to block frontier egress"
    except RuntimeError as exc:
        assert "detectable pii" in str(exc).lower()
    receipt = orch.receipt_service.create.call_args.args[0]
    assert receipt.action_name == "pii_scrub_blocked"
    assert receipt.inputs["payload_path"] == "root"
    assert receipt.metadata["residual_categories"] == ["email"]


def test_scrub_frontier_payload_emits_receipts_for_nested_detected_fields():
    orch = _make_orchestrator()

    def redact_side_effect(task_type, text):
        assert task_type == "redact"
        return SimpleNamespace(
            executed=True,
            output=text.replace("alice@example.com", "[EMAIL]").replace("415-555-1212", "[PHONE]"),
            decision=SimpleNamespace(error=None),
        )

    orch.model_router.route.side_effect = redact_side_effect

    scrubbed = orch._scrub_frontier_payload(
        {
            "content": [{"result": {"email": "alice@example.com"}}],
            "output": {"phone": "415-555-1212"},
        }
    )

    assert scrubbed["content"][0]["result"]["email"] == "[EMAIL]"
    assert scrubbed["output"]["phone"] == "[PHONE]"
    receipts = [call.args[0] for call in orch.receipt_service.create.call_args_list]
    assert [receipt.action_name for receipt in receipts] == [
        "pii_scrub_applied",
        "pii_scrub_applied",
    ]
    assert {receipt.inputs["payload_path"] for receipt in receipts} == {
        "root.content[0].result.email",
        "root.output.phone",
    }


def test_large_frontier_text_receipt_records_prescrub_and_local_verification():
    orch = _make_orchestrator()
    orch.model_router = None
    orch.local_model = MagicMock()
    orch.local_model.is_healthy.return_value = True
    orch.local_model.redact.side_effect = lambda chunk: chunk
    text = ("Customer alice@example.com " + ("x" * 1000) + "\n") * 8

    redacted = orch._redact_for_frontier(text)

    assert redacted.count("[EMAIL]") == 8
    receipt = orch.receipt_service.create.call_args.args[0]
    assert receipt.action_name == "pii_scrub_applied"
    assert receipt.inputs["source"] == "chunked_local_model_after_deterministic_prescrub"
    assert receipt.outputs["pre_scrubbed"] is True
    assert receipt.outputs["pre_scrub_source"] == "deterministic_local"
    assert receipt.outputs["local_verification_used"] is True
    assert receipt.outputs["scrub_pipeline"] == [
        "deterministic_prescrub",
        "local_model_verification",
        "deterministic_validation",
    ]
    assert receipt.metadata["pre_scrubbed"] is True
    assert receipt.metadata["local_verification_used"] is True
