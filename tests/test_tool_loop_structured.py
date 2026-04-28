import json
from types import SimpleNamespace

from response.presenter import AGENTIC_RESPONSE_SCHEMA
from tool_loop_structured import (
    receipt_summary,
    reformat_final_tool_response,
    summarize_interrupted_tool_run,
    summarize_max_iterations,
    verify_raw_response_claims,
)


def _runtime_returning_structured(text="Verified response"):
    captured = {}

    def provider_generate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            text=json.dumps(
                {
                    "response_to_user": text,
                    "actions_taken": [
                        {
                            "tool": "repo_writer",
                            "summary": "Updated README",
                            "status": "success",
                        },
                        {
                            "tool": "imaginary_tool",
                            "summary": "This should be ignored",
                            "status": "success",
                        },
                    ],
                    "next_action": "done",
                }
            )
        )

    runtime = SimpleNamespace(
        build_frontier_user_message=lambda prompt: {"role": "user", "content": prompt},
        provider_generate=provider_generate,
        route_model=lambda prompt: "model-for-" + prompt,
    )
    return runtime, captured


def test_receipt_summary_uses_receipts_as_ground_truth():
    assert receipt_summary(
        [
            {"skill": "repo_writer", "result": "SUCCESS"},
            {"skill": "network_client", "result": "FAILED: 500"},
        ]
    ) == "- repo_writer: SUCCESS\n- network_client: FAILED: 500"


def test_reformat_final_tool_response_uses_schema_and_receipt_prompt():
    runtime, captured = _runtime_returning_structured()
    receipts = [{"skill": "repo_writer", "result": "SUCCESS"}]

    presented = reformat_final_tool_response(
        runtime,
        prompt="update docs",
        text="I updated docs and deployed production",
        tool_receipts=receipts,
        claim_verification=False,
    )

    assert presented == "Verified response"
    assert captured["model"] == "model-for-update docs"
    assert captured["config"] == {
        "response_mime_type": "application/json",
        "response_schema": AGENTIC_RESPONSE_SCHEMA,
    }
    user_prompt = captured["messages"][0]["content"]
    assert "ACTUAL TOOL RECEIPTS" in user_prompt
    assert "- repo_writer: SUCCESS" in user_prompt
    assert "ORIGINAL RESPONSE:" in user_prompt


def test_summarize_interrupted_tool_run_includes_error_context():
    runtime, captured = _runtime_returning_structured("Partial verified work")

    presented = summarize_interrupted_tool_run(
        runtime,
        prompt="fetch data",
        tool_receipts=[{"skill": "network_client", "result": "SUCCESS"}],
        error=TimeoutError("provider timeout"),
        claim_verification=False,
    )

    assert presented == "Partial verified work"
    user_prompt = captured["messages"][0]["content"]
    assert "interrupted by an error: provider timeout" in user_prompt
    assert "- network_client: SUCCESS" in user_prompt


def test_summarize_max_iterations_returns_none_when_structured_parse_fails():
    runtime = SimpleNamespace(
        build_frontier_user_message=lambda prompt: {"role": "user", "content": prompt},
        provider_generate=lambda **kwargs: SimpleNamespace(text="not json"),
        route_model=lambda prompt: "model",
    )

    assert summarize_max_iterations(
        runtime,
        prompt="loop",
        tool_receipts=[{"skill": "network_client", "result": "SUCCESS"}],
        claim_verification=False,
    ) is None


def test_verify_raw_response_claims_is_noop_when_disabled():
    assert verify_raw_response_claims(
        "raw text",
        [{"skill": "repo_writer", "result": "SUCCESS"}],
        claim_verification=False,
    ) == "raw text"
