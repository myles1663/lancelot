from types import SimpleNamespace
from unittest.mock import MagicMock

from orchestrator_frontier import (
    emit_frontier_scrub_receipt,
    provider_generate,
    record_frontier_scrub_result,
)


def test_emit_frontier_scrub_receipt_persists_auditable_pipeline_metadata():
    runtime = SimpleNamespace(
        receipt_service=MagicMock(),
        current_model_usage_status=lambda: {"frontier_scrub_mode": "required"},
        _current_quest_id="quest-1",
        _current_channel="api",
        _current_operator_id="operator-1",
        _current_operator_name="Myles",
        _current_session_id="session-1",
    )

    emit_frontier_scrub_receipt(
        runtime,
        action_name="pii_scrub_applied",
        source="scrub_cascade",
        path="root.customer.email",
        input_length=32,
        detected_categories=("email",),
        scrubbed=True,
        pre_scrubbed=True,
        local_verification_used=True,
    )

    receipt = runtime.receipt_service.create.call_args.args[0]
    assert receipt.action_name == "pii_scrub_applied"
    assert receipt.inputs["payload_path"] == "root.customer.email"
    assert receipt.metadata["frontier_scrub_event"] is True
    assert receipt.metadata["pii_categories"] == ["email"]
    assert receipt.outputs["scrub_pipeline"] == [
        "deterministic_prescrub",
        "local_model_verification",
        "deterministic_validation",
    ]


def test_record_frontier_scrub_result_emits_degraded_progress_for_fallback():
    progress_events = []
    receipt_events = []
    runtime = SimpleNamespace(
        emit_chat_progress=lambda phase, message, **metadata: progress_events.append(
            {"phase": phase, "message": message, **metadata}
        ),
        emit_frontier_scrub_receipt=lambda **kwargs: receipt_events.append(kwargs),
    )
    result = SimpleNamespace(
        source="deterministic_local",
        reason="deterministic local scrub fallback used",
        detected_categories=("email",),
        residual_categories=(),
        scrubbed=True,
        fallback_used=True,
        pre_scrubbed=True,
        pre_scrub_source="deterministic_local",
        local_verification_used=False,
        scrub_stages=("deterministic_prescrub", "deterministic_fallback"),
    )

    record_frontier_scrub_result(runtime, result, path="root", input_length=64)

    assert progress_events[0]["phase"] == "frontier_scrub"
    assert progress_events[0]["degraded"] is True
    assert receipt_events[0]["action_name"] == "pii_scrub_fallback"
    assert receipt_events[0]["fallback_used"] is True


def test_provider_generate_scrubs_messages_before_frontier_call():
    progress_events = []
    provider = MagicMock()
    provider.generate.return_value = "ok"
    runtime = SimpleNamespace(
        provider=provider,
        emit_chat_progress=lambda phase, message, **metadata: progress_events.append(
            {"phase": phase, "message": message, **metadata}
        ),
        scrub_frontier_payload=lambda messages: [{"role": "user", "content": "Contact [EMAIL]"}],
    )

    result = provider_generate(
        runtime,
        model="gpt-test",
        messages=[{"role": "user", "content": "Contact alice@example.com"}],
        system_instruction="system",
        config={"temperature": 0},
    )

    assert result == "ok"
    assert provider.generate.call_args.kwargs["messages"] == [
        {"role": "user", "content": "Contact [EMAIL]"}
    ]
    assert [event["phase"] for event in progress_events] == [
        "frontier_scrub",
        "provider_call",
    ]
