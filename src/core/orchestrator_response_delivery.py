"""Stateful response delivery helpers for the Lancelot orchestrator."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from orch_helpers.response_helpers import append_download_links as _append_download_links

_logger = logging.getLogger(__name__)
_SYNTHESIS_MAX_TOKENS = 16384


def force_synthesis(
    runtime: Any,
    messages: list,
    last_raw: Any,
    system_instruction: str,
    prompt: str,
) -> str:
    """Force a final text response after a model narrates work instead."""
    try:
        raw_msg = last_raw
        if isinstance(raw_msg, dict):
            raw_msg = {key: value for key, value in raw_msg.items() if key in ("role", "content")}
        messages.append(raw_msg)

        synthesis_msg = runtime.build_frontier_user_message(
            "IMPORTANT: You just described what you would do instead of actually doing it. "
            "Now produce the COMPLETE, DETAILED report. This is your FINAL response - "
            "the user will see exactly this text.\n\n"
            "Requirements:\n"
            "1. Write the full analysis with ALL sections (not just an executive summary)\n"
            "2. Include specific data points, numbers, and comparisons from the research\n"
            "3. Use markdown headers (##) for each major section\n"
            "4. Cover: findings, competitive comparison, strengths/weaknesses, "
            "roadmap implications, and recommendations\n"
            "5. Be comprehensive - aim for 2000+ words\n\n"
            "Do NOT say 'let me compile' or 'I will now' - write the actual content."
        )
        messages.append(synthesis_msg)

        thinking_config = runtime.get_thinking_config()
        synthesis_config = {"max_tokens": _SYNTHESIS_MAX_TOKENS}
        if thinking_config:
            synthesis_config["thinking"] = thinking_config

        deep_model = runtime.get_deep_model()
        _logger.debug(
            "forced_synthesis_started",
            extra={
                "max_tokens": _SYNTHESIS_MAX_TOKENS,
                "model": deep_model,
                "prompt_length": len(prompt or ""),
            },
        )
        result = runtime.llm_call_with_retry(
            lambda: runtime.provider_generate(
                model=deep_model,
                messages=messages,
                system_instruction=system_instruction,
                config=synthesis_config,
            )
        )
        return result.text if result.text else ""
    except Exception as exc:
        _logger.warning(
            "forced_synthesis_failed",
            extra={"error": str(exc)},
        )
        return ""


def deliver_war_room_artifacts(runtime: Any, artifacts: list) -> list:
    """Broadcast assembled artifacts and return auto-created document paths."""
    created_docs = []

    try:
        from event_bus import Event, event_bus
    except ImportError:
        try:
            from src.core.event_bus import Event, event_bus
        except ImportError:
            _logger.debug("event_bus_unavailable_skipping_artifact_delivery")
            return created_docs

    for artifact in artifacts:
        try:
            artifact_type = artifact.type if isinstance(artifact.type, str) else artifact.type.value
            content = artifact.content or {}

            if artifact_type == "RESEARCH_REPORT":
                full_text = content.get("full_text", "")
                if full_text and content.get("auto_document"):
                    doc_path = runtime.auto_create_document(full_text)
                    if doc_path:
                        content["document_path"] = doc_path
                        created_docs.append(doc_path)
                        _logger.info(
                            "war_room_auto_document_created",
                            extra={"path": doc_path},
                        )

            event = Event(
                type="warroom_artifact",
                payload={
                    "artifact_id": artifact.id,
                    "artifact_type": artifact_type,
                    "content": artifact.content,
                    "session_id": artifact.session_id,
                    "created_at": artifact.created_at,
                },
            )
            event_bus.publish_sync(event)
        except Exception as exc:
            _logger.warning(
                "war_room_artifact_delivery_failed",
                extra={
                    "artifact_id": getattr(artifact, "id", "unknown"),
                    "error": str(exc),
                },
            )

    return created_docs


def auto_create_document(runtime: Any, content: str, title: str = "Research Report") -> str:
    """Create a PDF report through the governed document_creator skill."""
    if not runtime.skill_executor:
        return ""

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp}.pdf"

        sections = []
        current_section = {"heading": "", "paragraphs": []}
        for raw_line in content.split("\n"):
            line = raw_line.strip()
            if line.startswith("## "):
                if current_section["paragraphs"] or current_section["heading"]:
                    sections.append(current_section)
                current_section = {"heading": line[3:], "paragraphs": []}
            elif line.startswith("# "):
                if current_section["paragraphs"] or current_section["heading"]:
                    sections.append(current_section)
                current_section = {"heading": line[2:], "paragraphs": []}
            elif line.startswith("- "):
                current_section.setdefault("bullets", []).append(line[2:])
            elif line:
                current_section["paragraphs"].append(line)
        if current_section["paragraphs"] or current_section["heading"]:
            sections.append(current_section)

        doc_content = {
            "title": title,
            "subtitle": f"Generated {datetime.now().strftime('%B %d, %Y')}",
            "sections": sections,
        }

        try:
            from skills.executor import SkillContext
        except ImportError:
            from src.core.skills.executor import SkillContext

        ctx = SkillContext(skill_name="document_creator", caller="assembler")
        result = runtime.skill_executor.run(
            "document_creator",
            {"format": "pdf", "path": filename, "content": doc_content},
            context=ctx,
        )
        if result.success:
            return result.outputs.get("path", "")

        _logger.warning(
            "auto_document_creation_failed",
            extra={"error": result.error},
        )
        return ""
    except Exception as exc:
        _logger.warning(
            "auto_document_creation_error",
            extra={"error": str(exc)},
        )
        return ""


def append_download_links(response: str, doc_paths: list) -> str:
    return _append_download_links(response, doc_paths)


def validate_llm_response(runtime: Any, response_text: str) -> str:
    """Remove learned-rule leakage and run final output through the sanitizer."""
    cleaned = response_text.replace("[Learned Rule]", "")
    return runtime.sanitizer.sanitize(cleaned)
