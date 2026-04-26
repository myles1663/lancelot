"""LLM generation, retry, thinking, and deep-reasoning helpers."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from providers.base import wait_before_provider_retry


_logger = logging.getLogger("orchestrator.generation")

RETRYABLE_ERROR_MARKERS = (
    "429",
    "resource_exhausted",
    "500",
    "internal",
    "503",
    "service_unavailable",
    "overloaded",
    "rate_limit",
    "timeout",
)


def is_retryable_error(exc: Exception) -> bool:
    """Return whether a provider failure is transient enough to retry."""
    err_str = str(exc).lower()
    return any(marker in err_str for marker in RETRYABLE_ERROR_MARKERS)


def llm_call_with_retry(
    runtime: Any,
    call_fn: Callable[[], Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Any:
    """Execute an LLM call with exponential backoff on transient errors."""
    from providers.base import ProviderAuthError

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return call_fn()
        except ProviderAuthError as exc:
            try:
                from providers.api import report_auth_error

                report_auth_error(exc.provider, str(exc))
            except ImportError as report_exc:
                logging.debug(
                    "Provider auth reporter unavailable during retry handling: %s",
                    report_exc,
                )
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries and runtime._is_retryable_error(exc):
                delay = base_delay * (2**attempt)
                _logger.warning(
                    "llm_api_transient_error",
                    extra={
                        "attempt": attempt + 1,
                        "max_attempts": max_retries + 1,
                        "delay_s": delay,
                        "error": str(exc),
                    },
                )
                wait_before_provider_retry(
                    delay,
                    getattr(runtime, "_stop_event", None),
                    provider="orchestrator",
                )
            else:
                raise
    raise last_exc


def text_only_generate(
    runtime: Any,
    prompt: str,
    system_instruction: str | None = None,
    context_str: str | None = None,
    image_parts: list | None = None,
) -> str:
    """Run a standard no-tools provider call, including frontier-safe message construction."""
    if not runtime.provider:
        return "Error: LLM provider not initialized."

    if not system_instruction:
        system_instruction = runtime._build_system_instruction()

    ctx = context_str or runtime.context_env.get_context_string()
    full_text = f"{ctx}\n\n{prompt}"

    try:
        msg = runtime._build_frontier_user_message(full_text, images=image_parts)
        messages = [msg]

        result = runtime._llm_call_with_retry(
            lambda: runtime._provider_generate(
                model=runtime._route_model(prompt),
                messages=messages,
                system_instruction=system_instruction,
                config={"thinking": runtime._get_thinking_config()},
            )
        )
        return result.text if result.text else ""
    except Exception as exc:
        _logger.warning(
            "text_only_generate_failed",
            extra={"error": str(exc)},
        )
        return f"Error generating response: {exc}"


def get_thinking_config() -> dict | None:
    """Return the configured provider-agnostic thinking level."""
    level = os.getenv("GEMINI_THINKING_LEVEL", "low")
    if level == "off":
        return None
    return {"thinking_level": level}


def should_use_deep_reasoning(runtime: Any, user_message: str) -> bool:
    """Determine whether a request warrants a reasoning-only pass."""
    if len(user_message) < 30:
        return False

    lower = user_message.lower()
    words = set(lower.split())

    conversational = {
        "hello",
        "hi",
        "hey",
        "thanks",
        "thank",
        "bye",
        "ok",
        "okay",
        "yes",
        "no",
        "sure",
        "status",
        "who",
    }
    if words.issubset(conversational) or len(words) <= 2:
        return False

    if runtime._is_continuation(user_message):
        return False

    reasoning_indicators = {
        "analyze",
        "analyse",
        "compare",
        "research",
        "investigate",
        "evaluate",
        "assess",
        "review",
        "explain",
        "diagnose",
        "strategy",
        "recommend",
        "design",
        "architect",
        "plan",
        "competitive",
        "intelligence",
        "news about",
        "updates on",
    }
    if words & reasoning_indicators:
        return True

    reasoning_phrases = [
        "what should",
        "how should",
        "help me think",
        "what's the best",
        "pros and cons",
        "trade-off",
        "deep dive",
        "thorough",
        "comprehensive",
    ]
    if any(phrase in lower for phrase in reasoning_phrases):
        return True

    if runtime._needs_research(user_message):
        return True

    if len(user_message) > 100 and "?" in user_message:
        return True

    return len(user_message) > 200


def build_reasoning_instruction(runtime: Any) -> str:
    """Build the system instruction for the deep reasoning pass."""
    if runtime.soul:
        identity = (
            "You are Lancelot, a governed autonomous agent.\n"
            f"Mission: {runtime.soul.mission}\n"
            f"Allegiance: {runtime.soul.allegiance}\n"
        )
    else:
        identity = (
            "You are Lancelot, a governed autonomous agent "
            "serving your bonded user.\n"
        )

    self_knowledge = (
        "YOUR ARCHITECTURE:\n"
        "- Soul: Constitutional governance — mission, allegiance, tone invariants, risk rules\n"
        "- Memory: Tiered persistence — core blocks, working (24h), episodic (30-day), archival\n"
        "- Skills: Modular capabilities — manifest+execute pattern, security pipeline\n"
        "- Tool Fabric: Provider-agnostic execution — shell, file, repo, web, deploy, vision\n"
        "- Receipt System: Immutable audit trail for all tool calls\n"
        "- Scheduler: Gated automation — cron/interval jobs with approval rules\n"
        "- War Room: Operator dashboard — health, memory, skills, kill switches\n"
        "- Structured Output: JSON schema responses with claim checking\n"
    )

    capabilities = (
        "AVAILABLE TOOLS (you will use these in the execution phase):\n"
        "- network_client: HTTP requests (GET/POST/PUT/DELETE) for APIs, web research\n"
        "- github_search: Search GitHub repos, commits, issues, releases — structured data with URLs\n"
        "- command_runner: Shell commands on the system\n"
        "- repo_writer: Create/edit/delete files in the workspace\n"
        "- telegram_send: Send messages/files to Telegram\n"
        "- warroom_send: Push notifications to the War Room\n"
        "- schedule_job: Create/list/delete scheduled tasks\n"
        "- service_runner: Docker service management\n"
        "- document_creator: Generate formatted documents\n"
    )

    ctx = runtime.context_env.get_context_string() if runtime.context_env else ""
    memory_block = f"CURRENT CONTEXT:\n{ctx}\n" if ctx else ""

    directives = (
        "REASONING DIRECTIVES:\n"
        "1. Think deeply about this task before any action is taken.\n"
        "2. What information do you need to find? What do you already know?\n"
        "3. What approaches should you consider? What are the trade-offs?\n"
        "4. What would a thorough, well-grounded answer look like?\n"
        "5. Acknowledge uncertainty — never fabricate facts or sources.\n"
        "6. If completing this task well requires a tool or skill that doesn't "
        "exist in the inventory above, note it as: CAPABILITY GAP: <description>\n"
        "7. Do NOT call tools or take actions. Just reason about the task.\n"
        "8. Produce analysis you would stake your reputation on.\n"
    )

    return f"{identity}\n{self_knowledge}\n{capabilities}\n{memory_block}\n{directives}"
