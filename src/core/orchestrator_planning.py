"""Planning enrichment and execution-summary helpers for the orchestrator."""

from __future__ import annotations

import logging
import re


logger = logging.getLogger(__name__)


def enrich_plan_with_llm(runtime, artifact, user_text: str):
    """Use the configured provider to replace generic plan steps with concrete ones."""
    if not runtime.provider:
        return artifact

    self_awareness = runtime._build_self_awareness()

    prompt = (
        f'The user asked: "{user_text}"\n\n'
        f"Your goal: {artifact.goal}\n\n"
        f"{self_awareness}\n\n"
        "INSTRUCTIONS:\n"
        "1. FIRST: Use your network_client tool to research relevant APIs, docs, and endpoints. "
        "For example, call network_client with method=GET to fetch API documentation pages. "
        "Do this BEFORE generating any plan steps.\n"
        "2. AFTER you have research results, generate 4-6 specific, actionable plan steps.\n"
        "3. Ground the plan in YOUR real capabilities and the research results.\n"
        "4. You already communicate via Telegram with text and voice notes.\n"
        "5. If the user says 'us' or 'we', that includes you.\n"
        "6. Don't suggest downloading third-party apps when your existing capabilities cover the need.\n\n"
        "Your final text response must be ONLY a numbered list of steps (1. ... 2. ... etc)."
    )

    sys_instruction = (
        f"You are Lancelot's planning module. {self_awareness} "
        "You MUST use your tools to research before generating plan steps. "
        "Call network_client to fetch real API docs and data. "
        "Your final response should be only numbered steps."
    )

    try:
        from feature_flags import FEATURE_AGENTIC_LOOP

        if FEATURE_AGENTIC_LOOP:
            logger.info("Enriching plan with forced tool research")
            raw = runtime._agentic_generate(
                prompt=prompt,
                system_instruction=sys_instruction,
                allow_writes=False,
                force_tool_use=True,
                skip_structured_reformat=True,
            )
        else:
            msg = runtime._build_frontier_user_message(
                f"{runtime.context_env.get_context_string()}\n\n{prompt}"
            )
            result = runtime._llm_call_with_retry(
                lambda: runtime._provider_generate(
                    model=runtime.model_name,
                    messages=[msg],
                    system_instruction=sys_instruction,
                )
            )
            raw = result.text.strip() if result.text else ""

        steps = re.findall(r"^\d+\.\s*(.+)$", raw, re.MULTILINE)
        if steps and len(steps) >= 3:
            artifact.plan_steps = steps
            artifact.next_action = steps[0]
            logger.info("Plan enriched with %d LLM-generated steps", len(steps))
    except Exception as exc:
        logger.warning("Plan enrichment failed, using template: %s", exc)

    return artifact


def summarize_execution_results(runtime, graph, run_result) -> str:
    """Summarize real skill execution results using the configured provider."""
    if not runtime.provider:
        return ""

    results_text = []
    for step_result in run_result.step_results:
        step_label = step_result.step_id
        for step in graph.steps:
            if step.step_id == step_result.step_id:
                step_label = step.inputs.get("description", step.type)
                break
        if step_result.success:
            results_text.append(f"- {step_label}: SUCCESS - {step_result.outputs}")
        else:
            results_text.append(f"- {step_label}: FAILED - {step_result.error}")

    results_block = "\n".join(results_text)

    prompt = (
        f"Goal: {graph.goal}\n\n"
        f"Execution results:\n{results_block}\n\n"
        "Summarize what was accomplished for the user. "
        "Be direct and concise. Report real outcomes only. "
        "If steps failed, explain what went wrong and suggest fixes."
    )

    try:
        system_instruction = runtime._build_execution_instruction()
        msg = runtime._build_frontier_user_message(
            f"{runtime.context_env.get_context_string()}\n\n{prompt}"
        )
        gen_result = runtime._llm_call_with_retry(
            lambda: runtime._provider_generate(
                model=runtime._route_model(graph.goal or ""),
                messages=[msg],
                system_instruction=system_instruction,
                config={"thinking": runtime._get_thinking_config()},
            )
        )
        from response.policies import OutputPolicy

        return OutputPolicy.strip_tool_scaffolding(gen_result.text)
    except Exception as exc:
        logger.warning("Result summarization failed: %s", exc)
        return f"**Execution Complete**\n\n{results_block}"
