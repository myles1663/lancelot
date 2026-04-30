from __future__ import annotations

import logging
from typing import Any

from response.presenter import (
    AGENTIC_RESPONSE_SCHEMA,
    ResponsePresenter,
    parse_structured_response,
)

_logger = logging.getLogger("src.core.orchestrator")


def receipt_summary(tool_receipts: list[dict[str, Any]]) -> str:
    """Return the receipt-only ground truth used for structured summaries."""
    return "\n".join(
        f"- {receipt['skill']}: {receipt.get('result', 'unknown')}"
        for receipt in tool_receipts
    )


def _generate_structured_summary(
    runtime: Any,
    *,
    prompt: str,
    user_prompt: str,
    system_instruction: str,
    tool_receipts: list[dict[str, Any]],
    claim_verification: bool,
) -> str | None:
    msg = runtime.build_frontier_user_message(user_prompt)
    result = runtime.provider_generate(
        model=runtime.route_model(prompt),
        messages=[msg],
        system_instruction=system_instruction,
        config={
            "response_mime_type": "application/json",
            "response_schema": AGENTIC_RESPONSE_SCHEMA,
        },
    )
    structured = parse_structured_response(result.text)
    if not structured:
        return None

    presenter = ResponsePresenter(claim_verification=claim_verification)
    return presenter.present(structured, tool_receipts)


def summarize_interrupted_tool_run(
    runtime: Any,
    *,
    prompt: str,
    tool_receipts: list[dict[str, Any]],
    error: Exception,
    claim_verification: bool,
) -> str | None:
    """Summarize completed tool work after the planner errors mid-loop."""
    summary = receipt_summary(tool_receipts)
    summary_prompt = (
        f"Summarize what was accomplished based on these tool receipts. "
        f"The process was interrupted by an error: {error}\n"
        f"Be concise. Only claim actions that appear in the receipts.\n\n"
        f"TOOL RECEIPTS:\n{summary}"
    )
    try:
        return _generate_structured_summary(
            runtime,
            prompt=prompt,
            user_prompt=summary_prompt,
            system_instruction=(
                "You summarize tool results into JSON. Only include actions "
                "verified by receipts."
            ),
            tool_receipts=tool_receipts,
            claim_verification=claim_verification,
        )
    except Exception as exc:
        _logger.warning(
            "error_path_reformat_failed",
            extra={"error": str(exc)},
        )
        return None


def reformat_final_tool_response(
    runtime: Any,
    *,
    prompt: str,
    text: str,
    tool_receipts: list[dict[str, Any]],
    claim_verification: bool,
) -> str | None:
    """Reformat a final free-text tool response through the receipt verifier."""
    summary = receipt_summary(tool_receipts)
    reformat_prompt = (
        f"Reformat this response into the required JSON schema. "
        f"Only include actions that appear in the ACTUAL TOOL RECEIPTS below.\n\n"
        f"TOOL RECEIPTS (ground truth - only these actions happened):\n{summary}\n\n"
        f"ORIGINAL RESPONSE:\n{text}"
    )
    try:
        presented = _generate_structured_summary(
            runtime,
            prompt=prompt,
            user_prompt=reformat_prompt,
            system_instruction=(
                "You reformat text into JSON. Only include actions verified "
                "by tool receipts."
            ),
            tool_receipts=tool_receipts,
            claim_verification=claim_verification,
        )
        if presented:
            _logger.debug(
                "structured_reformat_succeeded",
                extra={"presented_chars": len(presented)},
            )
        else:
            _logger.debug("structured_reformat_parse_failed")
        return presented
    except Exception as exc:
        _logger.warning(
            "structured_reformat_failed",
            extra={"error": str(exc)},
        )
        return None


def verify_raw_response_claims(
    text: str,
    tool_receipts: list[dict[str, Any]],
    *,
    claim_verification: bool,
) -> str:
    """Apply receipt-backed claim verification when structured output is off."""
    if not claim_verification or not text:
        return text

    try:
        presenter = ResponsePresenter(claim_verification=True)
        return presenter.present_fallback(text, tool_receipts)
    except Exception as exc:
        _logger.warning(
            "claim_verification_failed",
            extra={"error": str(exc)},
        )
        return text


def summarize_max_iterations(
    runtime: Any,
    *,
    prompt: str,
    tool_receipts: list[dict[str, Any]],
    claim_verification: bool,
) -> str | None:
    """Summarize verified receipt state when the tool loop hits its limit."""
    summary = receipt_summary(tool_receipts)
    summary_prompt = (
        f"Summarize what was accomplished based on these tool receipts. "
        f"Be concise. Only claim actions that appear in the receipts.\n\n"
        f"TOOL RECEIPTS:\n{summary}"
    )
    try:
        presented = _generate_structured_summary(
            runtime,
            prompt=prompt,
            user_prompt=summary_prompt,
            system_instruction=(
                "Summarize tool execution results concisely. Only mention "
                "actions in the receipts."
            ),
            tool_receipts=tool_receipts,
            claim_verification=claim_verification,
        )
        if presented:
            _logger.debug(
                "max_iterations_summary_rendered",
                extra={"presented_chars": len(presented)},
            )
        return presented
    except Exception as exc:
        _logger.warning(
            "max_iterations_presenter_failed",
            extra={"error": str(exc)},
        )
        return None
