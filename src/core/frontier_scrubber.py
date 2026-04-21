"""
Canonical local PII scrubbing boundary for frontier-bound payloads.

This module centralizes the local redaction lane so the capability exists as a
standalone subsystem instead of orchestrator-only helper logic.

Public API:
    normalize_frontier_pii_text(text) -> str
    detect_frontier_pii_categories(text) -> set[str]
    validate_frontier_redaction(original, redacted) -> tuple[bool, str]
    LocalPIIScrubber.scrub_text(text) -> PIIScrubResult
    LocalPIIScrubber.scrub_payload(payload) -> Any
    LocalPIIScrubber.status() -> dict
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from src.core.model_usage_policy import (
    FRONTIER_SCRUB_DISABLED,
    FRONTIER_SCRUB_REQUIRED,
    clear_frontier_scrub_fallback,
    get_model_usage_status,
    record_frontier_scrub_fallback,
    set_local_model_availability,
)

logger = logging.getLogger(__name__)

_FRONTIER_PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}\b"),
    "date_of_birth": re.compile(
        r"\b(?:dob|date of birth|birth date)\s*[:\-]?\s*"
        r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
        re.IGNORECASE,
    ),
}
_FRONTIER_CREDIT_CARD_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_FRONTIER_SEPARATOR_TRANSLATION = str.maketrans({
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
})


def normalize_frontier_pii_text(text: str) -> str:
    """Normalize obfuscated separators before structured PII detection."""
    if not isinstance(text, str):
        return ""

    normalized = unicodedata.normalize("NFKC", text).translate(_FRONTIER_SEPARATOR_TRANSLATION)
    normalized = "".join(
        ch for ch in normalized
        if unicodedata.category(ch) != "Cf"
    )
    normalized = re.sub(r"(?<=\w)\s*@\s*(?=\w)", "@", normalized)
    normalized = re.sub(r"(?<=\w)\s*\.\s*(?=\w)", ".", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def detect_frontier_pii_categories(text: str) -> set[str]:
    """Detect obvious structured PII that must not leave the local scrub lane."""
    if not isinstance(text, str) or not text:
        return set()

    normalized_text = normalize_frontier_pii_text(text)
    categories: set[str] = set()
    for category, pattern in _FRONTIER_PII_PATTERNS.items():
        if pattern.search(normalized_text):
            categories.add(category)

    for match in _FRONTIER_CREDIT_CARD_PATTERN.finditer(normalized_text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19:
            categories.add("credit_card")
            break

    return categories


def validate_frontier_redaction(original: str, redacted: str) -> tuple[bool, str]:
    """Reject local scrub output that still carries detectable structured PII."""
    original_categories = detect_frontier_pii_categories(original)
    if not original_categories:
        return True, ""

    residual_categories = detect_frontier_pii_categories(redacted)
    if residual_categories:
        joined = ", ".join(sorted(residual_categories))
        return False, f"Local redaction output still contains detectable PII: {joined}"

    return True, ""


class PIIScrubError(RuntimeError):
    """Raised when the configured scrub policy requires fail-closed blocking."""


@dataclass(frozen=True)
class PIIScrubResult:
    """Structured result of a frontier scrub attempt."""

    text: str
    policy: str
    source: str
    scrubbed: bool
    fallback_used: bool
    detected_categories: tuple[str, ...]
    residual_categories: tuple[str, ...]
    reason: Optional[str] = None


@dataclass(frozen=True)
class PIIScrubAuditEvent:
    """Audit-ready scrub event for a specific payload path."""

    path: str
    input_length: int
    source: str
    scrubbed: bool
    fallback_used: bool
    detected_categories: tuple[str, ...]
    residual_categories: tuple[str, ...]
    reason: Optional[str] = None


class PIIScrubPayloadError(PIIScrubError):
    """Raised when payload scrubbing fails and the failing path matters."""

    def __init__(self, *, path: str, original_text: str, reason: str):
        self.path = path
        self.original_text = original_text
        self.detected_categories = tuple(sorted(detect_frontier_pii_categories(original_text)))
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class LocalPIIScrubber:
    """Standalone local-model PII scrubber for frontier-bound content."""

    _PASSTHROUGH_KEYS = frozenset({
        "role", "type", "id", "tool_call_id", "tool_use_id",
        "name", "model", "mime_type", "media_type",
    })

    def __init__(self, *, model_router: Any = None, local_model: Any = None):
        self.model_router = model_router
        self.local_model = local_model

    def bind(self, *, model_router: Any = None, local_model: Any = None) -> "LocalPIIScrubber":
        """Update runtime dependencies after gateway injection."""
        self.model_router = model_router
        self.local_model = local_model
        return self

    def status(self) -> dict:
        """Expose the persisted policy + runtime scrub status."""
        return get_model_usage_status()

    def scrub_text(self, text: str) -> PIIScrubResult:
        """Scrub sensitive text locally before it reaches a frontier provider."""
        if not isinstance(text, str) or not text.strip():
            policy = self.status()["frontier_scrub_mode"]
            return PIIScrubResult(
                text=text,
                policy=policy,
                source="noop",
                scrubbed=False,
                fallback_used=False,
                detected_categories=(),
                residual_categories=(),
            )

        policy = self.status()
        scrub_mode = policy["frontier_scrub_mode"]
        detected_categories = tuple(sorted(detect_frontier_pii_categories(text)))
        if scrub_mode == FRONTIER_SCRUB_DISABLED:
            clear_frontier_scrub_fallback()
            return PIIScrubResult(
                text=text,
                policy=scrub_mode,
                source="policy_disabled",
                scrubbed=False,
                fallback_used=False,
                detected_categories=detected_categories,
                residual_categories=detected_categories,
            )

        failure_reason = "Local scrubbing unavailable"

        routed = self._scrub_via_router(text)
        if routed is not None:
            candidate, failure_reason = routed
            if candidate is not None:
                return self._validated_success(
                    original=text,
                    candidate=candidate,
                    source="model_router",
                    success_reason="Local redaction lane ready",
                    detected_categories=detected_categories,
                )

        direct = self._scrub_via_local_model(text)
        if direct is not None:
            candidate, failure_reason = direct
            if candidate is not None:
                return self._validated_success(
                    original=text,
                    candidate=candidate,
                    source="local_model",
                    success_reason="Local model ready for scrubbing",
                    detected_categories=detected_categories,
                )

        set_local_model_availability(False, failure_reason)
        if scrub_mode == FRONTIER_SCRUB_REQUIRED:
            raise PIIScrubError(
                f"Frontier scrub policy is required but unavailable: {failure_reason}"
            )

        record_frontier_scrub_fallback(failure_reason)
        residual_categories = tuple(sorted(detect_frontier_pii_categories(text)))
        return PIIScrubResult(
            text=text,
            policy=scrub_mode,
            source="frontier_fallback",
            scrubbed=False,
            fallback_used=True,
            detected_categories=detected_categories,
            residual_categories=residual_categories,
            reason=failure_reason,
        )

    def scrub_payload(self, payload: Any) -> Any:
        """Recursively scrub provider-native payloads where text content is present."""
        scrubbed, _events = self.scrub_payload_with_audit(payload)
        return scrubbed

    def scrub_payload_with_audit(
        self,
        payload: Any,
        *,
        path: str = "root",
    ) -> tuple[Any, tuple[PIIScrubAuditEvent, ...]]:
        """Scrub nested provider payloads and return per-path audit events."""
        events: list[PIIScrubAuditEvent] = []
        scrubbed = self._scrub_payload_with_audit(payload, path=path, events=events)
        return scrubbed, tuple(events)

    def _scrub_payload_with_audit(
        self,
        payload: Any,
        *,
        path: str,
        events: list[PIIScrubAuditEvent],
    ) -> Any:
        if isinstance(payload, str):
            try:
                result = self.scrub_text(payload)
            except PIIScrubError as exc:
                raise PIIScrubPayloadError(
                    path=path,
                    original_text=payload,
                    reason=str(exc),
                ) from exc

            if result.detected_categories or result.fallback_used:
                events.append(
                    PIIScrubAuditEvent(
                        path=path,
                        input_length=len(payload),
                        source=result.source,
                        scrubbed=result.scrubbed,
                        fallback_used=result.fallback_used,
                        detected_categories=result.detected_categories,
                        residual_categories=result.residual_categories,
                        reason=result.reason,
                    )
                )
            return result.text

        if isinstance(payload, list):
            return [
                self._scrub_payload_with_audit(item, path=f"{path}[{idx}]", events=events)
                for idx, item in enumerate(payload)
            ]

        if isinstance(payload, dict):
            scrubbed: dict[str, Any] = {}
            for key, value in payload.items():
                child_path = f"{path}.{key}"
                if key in self._PASSTHROUGH_KEYS:
                    scrubbed[key] = value
                else:
                    scrubbed[key] = self._scrub_payload_with_audit(
                        value,
                        path=child_path,
                        events=events,
                    )
            return scrubbed

        return payload

    def _validated_success(
        self,
        *,
        original: str,
        candidate: str,
        source: str,
        success_reason: str,
        detected_categories: tuple[str, ...],
    ) -> PIIScrubResult:
        valid, validation_error = validate_frontier_redaction(original, candidate)
        if not valid:
            set_local_model_availability(False, validation_error)
            return self._failed_candidate(candidate, validation_error)

        set_local_model_availability(True, success_reason)
        clear_frontier_scrub_fallback()
        return PIIScrubResult(
            text=candidate,
            policy=self.status()["frontier_scrub_mode"],
            source=source,
            scrubbed=candidate != original,
            fallback_used=False,
            detected_categories=detected_categories,
            residual_categories=(),
        )

    def _failed_candidate(self, candidate: str, reason: str) -> PIIScrubResult:
        return PIIScrubResult(
            text=candidate,
            policy=self.status()["frontier_scrub_mode"],
            source="invalid_candidate",
            scrubbed=False,
            fallback_used=False,
            detected_categories=(),
            residual_categories=tuple(sorted(detect_frontier_pii_categories(candidate))),
            reason=reason,
        )

    def _scrub_via_router(self, text: str) -> Optional[tuple[Optional[str], str]]:
        router = self.model_router
        if router is None:
            return None

        failure_reason = "Local scrubbing unavailable"
        try:
            routed = router.route("redact", text)
            if getattr(routed, "executed", False) and isinstance(getattr(routed, "output", None), str):
                candidate = routed.output.strip()
                if candidate:
                    valid, validation_error = validate_frontier_redaction(text, candidate)
                    if valid:
                        return candidate, ""
                    failure_reason = validation_error
                    set_local_model_availability(False, failure_reason)
                else:
                    failure_reason = "Local redaction router returned empty output"
            else:
                failure_reason = (
                    getattr(getattr(routed, "decision", None), "error", None)
                    or failure_reason
                )
        except Exception as exc:
            failure_reason = f"Local redaction router failed: {exc}"
            set_local_model_availability(False, failure_reason)
            logger.warning(
                "Local redaction router failed, falling back to direct local model: %s",
                exc,
            )
        return None, failure_reason

    def _scrub_via_local_model(self, text: str) -> Optional[tuple[Optional[str], str]]:
        local_model = self.local_model
        if local_model is None:
            return None

        failure_reason = "Local scrubbing unavailable"
        try:
            if local_model.is_healthy():
                redacted = local_model.redact(text)
                if isinstance(redacted, str) and redacted.strip():
                    candidate = redacted.strip()
                    valid, validation_error = validate_frontier_redaction(text, candidate)
                    if valid:
                        return candidate, ""
                    failure_reason = validation_error
                    set_local_model_availability(False, failure_reason)
                else:
                    failure_reason = "Local model returned empty redaction output"
            else:
                failure_reason = "Local model health check failed for scrubbing"
        except Exception as exc:
            failure_reason = f"Direct local redaction failed: {exc}"
            set_local_model_availability(False, failure_reason)
            logger.warning("Direct local redaction failed, using original text: %s", exc)
        return None, failure_reason
