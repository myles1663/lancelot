"""Local PII scrubbing boundary for frontier-bound payloads."""

from __future__ import annotations

import logging
import os
import re
import time
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
from src.core.local_model_roles import ROLE_SCRUB_REGION_FINDER, ScrubRegion

logger = logging.getLogger(__name__)

_FRONTIER_SCRUB_CHUNK_CHARS = max(
    1000,
    int(os.environ.get("LANCELOT_FRONTIER_SCRUB_CHUNK_CHARS", "6000")),
)
_FRONTIER_SCRUB_BACKOFF_S = max(
    0.0,
    float(os.environ.get("LANCELOT_FRONTIER_SCRUB_BACKOFF_S", "60")),
)
_FRONTIER_SCRUB_CASCADE_ENABLED = os.environ.get(
    "LANCELOT_FRONTIER_SCRUB_CASCADE_ENABLED",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}
_FRONTIER_SCRUB_CASCADE_MIN_CHARS = max(
    0,
    int(os.environ.get("LANCELOT_FRONTIER_SCRUB_CASCADE_MIN_CHARS", "6000")),
)
_FRONTIER_SCRUB_REGION_FINDER_MAX_CHUNKS = max(
    1,
    int(os.environ.get("LANCELOT_SCRUB_REGION_FINDER_MAX_CHUNKS", "8")),
)

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
_FRONTIER_ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+"
    r"[A-Z][A-Za-z0-9'.-]*(?:\s+[A-Z][A-Za-z0-9'.-]*){0,5}\s+"
    r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Lane|Ln\.?|Court|Ct\.?|"
    r"Drive|Dr\.?|Boulevard|Blvd\.?|Way|Place|Pl\.?|Circle|Cir\.?)"
    r"(?:,\s*[A-Z][A-Za-z .'-]+)?"
    r"(?:,\s*[A-Z]{2})?"
    r"(?:\s+\d{5}(?:-\d{4})?)?",
)
_FRONTIER_PRIVATE_URL_PATTERN = re.compile(
    r"https?://[^\s)>\"']*(?:token|secret|code|password|passwd|key|auth)[^\s)>\"']*",
    re.IGNORECASE,
)
_FRONTIER_BARE_SECRET_PATTERNS = (
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", re.IGNORECASE),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{8,}\b", re.IGNORECASE),
    re.compile(r"\b(?:sk|rk|ak)-(?:live|test|prod|dev)-[A-Za-z0-9][A-Za-z0-9._-]{8,}\b", re.IGNORECASE),
    re.compile(r"\b(?:Bearer|Token)\s+[A-Za-z0-9._~+/-]{12,}={0,2}\b", re.IGNORECASE),
)
_FRONTIER_LABELED_SECRET_PATTERN = re.compile(
    r"\b(?P<label>"
    r"api[\s_-]?key|secret[\s_-]?key|access[\s_-]?token|refresh[\s_-]?token|"
    r"auth(?:entication)?[\s_-]?token|bearer[\s_-]?token|github[\s_-]?token|"
    r"gitlab[\s_-]?token|slack[\s_-]?token|stripe[\s_-]?key|"
    r"reset[\s_-]?code|verification[\s_-]?code|auth[\s_-]?code|"
    r"one[\s_-]?time(?:[\s_-]?pass(?:word|code)?|[\s_-]?code)|"
    r"otp|pin|password|passwd|passphrase|secret"
    r")\b"
    r"(?P<sep>\s*(?:=|:|is|was)?\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._~:/+=,@!#$%^&*?-]{3,})"
    r"(?P=quote)",
    re.IGNORECASE,
)
_FRONTIER_SEPARATOR_TRANSLATION = str.maketrans({
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
})

_FRONTIER_REDACTION_MARKERS = {
    "ssn": "[SSN]",
    "email": "[EMAIL]",
    "phone": "[PHONE]",
    "date_of_birth": "[DATE_OF_BIRTH]",
    "credit_card": "[CREDIT_CARD]",
    "account_id": "[ACCOUNT_ID]",
    "name": "[NAME]",
    "address": "[ADDRESS]",
    "private_url": "[URL]",
    "secret": "[SECRET]",
}

_FRONTIER_ACCOUNT_ID_PATTERN = re.compile(
    r"\b(?P<label>ticket|case|account|customer|client|request|incident)"
    r"(?:\s+(?:id|number|no\.?))?\s*(?:#|:)?\s*"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._-]*\d[A-Za-z0-9._-]*)\b",
    re.IGNORECASE,
)
_FRONTIER_CONTEXTUAL_NAME_PATTERN = re.compile(
    r"\b(?P<label>(?i:account lead|project lead|technical lead|business lead|team lead|"
    r"lead|reviewer|contact|owner|escalation owner|approver|requester|assignee|"
    r"customer|client|employee))"
    r"[ \t]+(?P<value>[A-Z][A-Za-z'.-]*(?:[ \t]+[A-Z][A-Za-z'.-]*){0,3})"
    r"(?=[ \t]*(?:,|\bat\b|<|\(|email|phone|\[EMAIL\]|"
    r"\b(?:reviewing|reviewed|approving|approved|owns|owning|handles|handling|working|for|on)\b|"
    r"(?:\r?\n)|$))"
)
_FRONTIER_LIVES_AT_NAME_PATTERN = re.compile(
    r"\b(?P<value>[A-Z][A-Za-z'.-]*(?:[ \t]+[A-Z][A-Za-z'.-]*){1,3})"
    r"(?=[ \t]+lives[ \t]+at\b)"
)
_FRONTIER_SEMANTIC_CUE_PATTERN = re.compile(
    r"\b(?:"
    r"api\s*key|secret\s*key|access\s*token|refresh\s*token|auth\s*token|"
    r"bearer\s*token|github\s*token|gitlab\s*token|slack\s*token|"
    r"reset\s*code|verification\s*code|auth\s*code|one\s*time\s*code|"
    r"otp|pin|password|passwd|passphrase|secret|token|credential|"
    r"address|lives\s+at|owner|lead|reviewer|approver|assignee|requester|customer|client|employee|"
    r"ticket|case|account|incident|ssn|dob|date\s+of\s+birth|card|phone|email"
    r")\b",
    re.IGNORECASE,
)
_FRONTIER_UNSTRUCTURED_NAME_CANDIDATE_PATTERN = re.compile(
    r"\b(?i:"
    r"customer\s+success|success\s+contact|account\s+owner|account\s+lead|"
    r"project\s+lead|technical\s+lead|business\s+lead|team\s+lead|"
    r"lead|reviewer|approver|assignee|requester|owner|contact|"
    r"customer|client|employee"
    r")\b"
    r".{0,96}?"
    r"\b[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,3}\b",
)
_FRONTIER_REDACTION_MARKER_PATTERN = re.compile(
    r"\[(?:SSN|EMAIL|PHONE|DATE_OF_BIRTH|CREDIT_CARD|ACCOUNT_ID|NAME|ADDRESS|URL|SECRET)\]"
)


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

    if _FRONTIER_ACCOUNT_ID_PATTERN.search(normalized_text):
        categories.add("account_id")
    if _FRONTIER_CONTEXTUAL_NAME_PATTERN.search(normalized_text):
        categories.add("name")
    if _FRONTIER_LIVES_AT_NAME_PATTERN.search(normalized_text):
        categories.add("name")
    if _FRONTIER_ADDRESS_PATTERN.search(normalized_text):
        categories.add("address")
    if _FRONTIER_PRIVATE_URL_PATTERN.search(normalized_text):
        categories.add("private_url")
    if _contains_frontier_secret(normalized_text):
        categories.add("secret")

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


def redact_frontier_pii_deterministically(text: str) -> str:
    """Redact structured PII using local deterministic patterns.

    This is the fail-safe local scrub path used when the redaction model is
    present but cannot complete inference.
    """
    if not isinstance(text, str) or not text:
        return text

    if not detect_frontier_pii_categories(text):
        return text

    redacted = _redact_frontier_pii_patterns(text)
    if not detect_frontier_pii_categories(redacted):
        return redacted

    # Obfuscated PII such as "ali ce @ example . com" may require normalized
    # matching. Use it only as a fallback so ordinary payloads keep formatting.
    redacted = _redact_frontier_pii_patterns(normalize_frontier_pii_text(text))
    return redacted


def _redact_frontier_pii_patterns(text: str) -> str:
    redacted = str(text or "")
    for category, pattern in _FRONTIER_PII_PATTERNS.items():
        redacted = pattern.sub(_FRONTIER_REDACTION_MARKERS[category], redacted)

    def _replace_card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19:
            return _FRONTIER_REDACTION_MARKERS["credit_card"]
        return match.group(0)

    redacted = _FRONTIER_CREDIT_CARD_PATTERN.sub(_replace_card, redacted)
    redacted = _FRONTIER_PRIVATE_URL_PATTERN.sub(
        _FRONTIER_REDACTION_MARKERS["private_url"],
        redacted,
    )
    for pattern in _FRONTIER_BARE_SECRET_PATTERNS:
        redacted = pattern.sub(_FRONTIER_REDACTION_MARKERS["secret"], redacted)
    redacted = _FRONTIER_LABELED_SECRET_PATTERN.sub(
        _replace_labeled_secret,
        redacted,
    )
    redacted = _FRONTIER_ADDRESS_PATTERN.sub(
        _FRONTIER_REDACTION_MARKERS["address"],
        redacted,
    )
    redacted = _FRONTIER_ACCOUNT_ID_PATTERN.sub(
        lambda match: f"{match.group('label')} {_FRONTIER_REDACTION_MARKERS['account_id']}",
        redacted,
    )
    redacted = _FRONTIER_CONTEXTUAL_NAME_PATTERN.sub(
        lambda match: f"{match.group('label')} {_FRONTIER_REDACTION_MARKERS['name']}",
        redacted,
    )
    redacted = _FRONTIER_LIVES_AT_NAME_PATTERN.sub(
        _FRONTIER_REDACTION_MARKERS["name"],
        redacted,
    )
    return redacted


def _contains_frontier_secret(text: str) -> bool:
    for pattern in _FRONTIER_BARE_SECRET_PATTERNS:
        if pattern.search(text):
            return True

    for match in _FRONTIER_LABELED_SECRET_PATTERN.finditer(text):
        if _looks_like_frontier_secret(
            label=match.group("label"),
            value=match.group("value"),
        ):
            return True
    return False


def _replace_labeled_secret(match: re.Match[str]) -> str:
    value = match.group("value")
    body, trailing = _split_secret_trailing_punctuation(value)
    if not _looks_like_frontier_secret(label=match.group("label"), value=body):
        return match.group(0)
    return (
        f"{match.group('label')}"
        f"{match.group('sep')}"
        f"{match.group('quote')}"
        f"{_FRONTIER_REDACTION_MARKERS['secret']}"
        f"{match.group('quote')}"
        f"{trailing}"
    )


def _split_secret_trailing_punctuation(value: str) -> tuple[str, str]:
    body = str(value or "").rstrip(".,;:)]}")
    return body, str(value or "")[len(body):]


def _looks_like_frontier_secret(*, label: str, value: str) -> bool:
    normalized_label = re.sub(r"[\s_-]+", "_", str(label or "").strip().lower())
    candidate = str(value or "").strip().strip("\"'")
    if not candidate or candidate.upper() in {"[SECRET]", "<SECRET>", "REDACTED"}:
        return False

    code_labels = {
        "reset_code",
        "verification_code",
        "auth_code",
        "one_time_code",
        "one_time_passcode",
        "otp",
        "pin",
    }
    password_labels = {"password", "passwd", "passphrase"}
    strict_labels = {
        "api_key",
        "secret_key",
        "access_token",
        "refresh_token",
        "auth_token",
        "authentication_token",
        "bearer_token",
        "github_token",
        "gitlab_token",
        "slack_token",
        "stripe_key",
    }

    if normalized_label in code_labels:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{3,11}", candidate))
    if normalized_label in password_labels:
        return len(candidate) >= 4
    if normalized_label in strict_labels:
        return len(candidate) >= 8 or _has_secret_shape(candidate)
    if normalized_label == "secret":
        return len(candidate) >= 8 or (len(candidate) >= 6 and _has_secret_shape(candidate))
    return False


def _has_secret_shape(value: str) -> bool:
    candidate = str(value or "")
    return bool(re.search(r"\d", candidate) and re.search(r"[-_.~:/+=@!#$%^&*?]", candidate))


def _line_has_frontier_semantic_cue(line: str) -> bool:
    return bool(_FRONTIER_SEMANTIC_CUE_PATTERN.search(str(line or "")))


def _text_has_frontier_semantic_cue(text: str) -> bool:
    return bool(_FRONTIER_SEMANTIC_CUE_PATTERN.search(str(text or "")))


def _line_needs_frontier_model_verification(line: str) -> bool:
    text = str(line or "")
    return _line_has_frontier_semantic_cue(text) and _line_has_frontier_model_candidate(text)


def _text_needs_frontier_model_verification(text: str) -> bool:
    return any(
        _line_needs_frontier_model_verification(line)
        for line in str(text or "").splitlines() or [str(text or "")]
    )


def _line_has_frontier_model_candidate(line: str) -> bool:
    text = normalize_frontier_pii_text(str(line or ""))
    if detect_frontier_pii_categories(text):
        return True
    return bool(_FRONTIER_UNSTRUCTURED_NAME_CANDIDATE_PATTERN.search(text))


def split_text_for_frontier_redaction(
    text: str,
    *,
    max_chars: int = _FRONTIER_SCRUB_CHUNK_CHARS,
) -> tuple[str, ...]:
    """Split text into bounded chunks without dropping separators."""
    if not isinstance(text, str) or not text:
        return ()
    if len(text) <= max_chars:
        return (text,)

    chunks: list[str] = []
    start = 0
    text_len = len(text)
    min_split = max(1, max_chars // 2)
    while start < text_len:
        hard_end = min(start + max_chars, text_len)
        if hard_end == text_len:
            chunks.append(text[start:hard_end])
            break

        split_at = -1
        for marker in ("\n\n", "\n", ". ", " "):
            candidate = text.rfind(marker, start, hard_end)
            if candidate >= start + min_split:
                split_at = candidate + len(marker)
                break

        if split_at <= start:
            split_at = hard_end

        chunks.append(text[start:split_at])
        start = split_at

    return tuple(chunk for chunk in chunks if chunk)


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
    pre_scrubbed: bool = False
    pre_scrub_source: Optional[str] = None
    local_verification_used: bool = False
    scrub_stages: tuple[str, ...] = ()


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
    pre_scrubbed: bool = False
    pre_scrub_source: Optional[str] = None
    local_verification_used: bool = False
    scrub_stages: tuple[str, ...] = ()


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

    def __init__(
        self,
        *,
        model_router: Any = None,
        local_model: Any = None,
        local_model_roles: Any = None,
    ):
        self.model_router = model_router
        self.local_model = local_model
        self.local_model_roles = local_model_roles

    def bind(
        self,
        *,
        model_router: Any = None,
        local_model: Any = None,
        local_model_roles: Any = None,
    ) -> "LocalPIIScrubber":
        """Update runtime dependencies after gateway injection."""
        self.model_router = model_router
        self.local_model = local_model
        self.local_model_roles = local_model_roles
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

        fallback_backoff_reason = self._active_fallback_backoff_reason(policy)
        if fallback_backoff_reason:
            deterministic = self._scrub_deterministically(
                text,
                failure_reason=fallback_backoff_reason,
                detected_categories=detected_categories,
                record_runtime_fallback=False,
            )
            if deterministic is not None:
                return deterministic

        failure_reason = "Local scrubbing unavailable"
        deterministic_input = redact_frontier_pii_deterministically(text)
        deterministic_prescrubbed = deterministic_input != text
        deterministic_prescrub_source: Optional[str] = (
            "deterministic_local" if deterministic_prescrubbed else None
        )
        deterministic_residual_categories = tuple(
            sorted(detect_frontier_pii_categories(deterministic_input))
        )

        if (
            self.local_model_roles is not None
            and not deterministic_prescrubbed
            and not deterministic_residual_categories
            and not self._should_use_role_cascade(
                text,
                pre_scrubbed=False,
                deterministic_residual_categories=(),
            )
        ):
            return self._deterministic_clean_success(text)

        if (
            self.local_model_roles is not None
            and deterministic_prescrubbed
            and not deterministic_residual_categories
            and (
                len(text or "") < _FRONTIER_SCRUB_CASCADE_MIN_CHARS
                or not self._deterministic_prescrub_needs_model_verification(
                    deterministic_input
                )
            )
        ):
            deterministic_result = self._deterministic_prescrub_success(
                original=text,
                candidate=deterministic_input,
                detected_categories=detected_categories,
            )
            if deterministic_result is not None:
                return deterministic_result

        if self._should_use_role_cascade(
            text,
            pre_scrubbed=deterministic_prescrubbed,
            deterministic_residual_categories=deterministic_residual_categories,
        ):
            cascade_result, failure_reason = self._scrub_via_role_cascade(
                deterministic_input,
                original=text,
                detected_categories=detected_categories,
                pre_scrubbed=deterministic_prescrubbed,
                pre_scrub_source=deterministic_prescrub_source,
            )
            if cascade_result is not None:
                return cascade_result
            if self._can_use_deterministic_fallback(failure_reason):
                expected_size_skip = self._is_expected_cascade_size_skip(failure_reason)
                deterministic = self._scrub_deterministically(
                    text,
                    failure_reason=failure_reason,
                    detected_categories=detected_categories,
                    pre_scrubbed=deterministic_prescrubbed,
                    pre_scrub_source=deterministic_prescrub_source,
                    record_runtime_fallback=not expected_size_skip,
                )
                if deterministic is not None:
                    if expected_size_skip:
                        set_local_model_availability(
                            True,
                            "Deterministic local scrub completed after bounded cascade skip",
                        )
                        clear_frontier_scrub_fallback()
                    return deterministic

        if self._requires_chunked_redaction(text):
            chunked_result, failure_reason = self._scrub_text_in_chunks(
                deterministic_input,
                original=text,
                detected_categories=detected_categories,
                pre_scrubbed=deterministic_prescrubbed,
                pre_scrub_source=deterministic_prescrub_source,
            )
            if chunked_result is not None:
                return chunked_result

        model_input = text
        pre_scrubbed = False
        pre_scrub_source: Optional[str] = None

        routed = self._scrub_via_router(model_input)
        if routed is not None:
            candidate, failure_reason = routed
            if candidate is not None:
                return self._validated_success(
                    original=text,
                    candidate=candidate,
                    source="model_router",
                    success_reason="Local redaction lane ready",
                    detected_categories=detected_categories,
                    pre_scrubbed=pre_scrubbed,
                    pre_scrub_source=pre_scrub_source,
                )
            if self._can_use_deterministic_fallback(failure_reason):
                deterministic = self._scrub_deterministically(
                    text,
                    failure_reason=failure_reason,
                    detected_categories=detected_categories,
                    pre_scrubbed=pre_scrubbed,
                    pre_scrub_source=pre_scrub_source,
                )
                if deterministic is not None:
                    return deterministic

        direct = self._scrub_via_local_model(model_input)
        if direct is not None:
            candidate, failure_reason = direct
            if candidate is not None:
                return self._validated_success(
                    original=text,
                    candidate=candidate,
                    source="local_model",
                    success_reason="Local model ready for scrubbing",
                    detected_categories=detected_categories,
                    pre_scrubbed=pre_scrubbed,
                    pre_scrub_source=pre_scrub_source,
                )
            if self._can_use_deterministic_fallback(failure_reason):
                deterministic = self._scrub_deterministically(
                    text,
                    failure_reason=failure_reason,
                    detected_categories=detected_categories,
                    pre_scrubbed=pre_scrubbed,
                    pre_scrub_source=pre_scrub_source,
                )
                if deterministic is not None:
                    return deterministic

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
                        pre_scrubbed=result.pre_scrubbed,
                        pre_scrub_source=result.pre_scrub_source,
                        local_verification_used=result.local_verification_used,
                        scrub_stages=result.scrub_stages,
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
        pre_scrubbed: bool = False,
        pre_scrub_source: Optional[str] = None,
        scrub_stages: tuple[str, ...] = (),
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
            pre_scrubbed=pre_scrubbed,
            pre_scrub_source=pre_scrub_source,
            local_verification_used=True,
            scrub_stages=scrub_stages,
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

    @staticmethod
    def _requires_chunked_redaction(text: str) -> bool:
        return isinstance(text, str) and len(text) > _FRONTIER_SCRUB_CHUNK_CHARS

    def _should_use_role_cascade(
        self,
        text: str,
        *,
        pre_scrubbed: bool,
        deterministic_residual_categories: tuple[str, ...] = (),
    ) -> bool:
        if not _FRONTIER_SCRUB_CASCADE_ENABLED:
            return False
        if self.local_model_roles is None:
            return False
        if deterministic_residual_categories:
            return True
        if pre_scrubbed:
            return self._deterministic_prescrub_needs_model_verification(text)
        return (
            len(text or "") >= _FRONTIER_SCRUB_CASCADE_MIN_CHARS
            and _text_needs_frontier_model_verification(text)
        )

    def _deterministic_prescrub_success(
        self,
        *,
        original: str,
        candidate: str,
        detected_categories: tuple[str, ...],
    ) -> Optional[PIIScrubResult]:
        valid, validation_error = validate_frontier_redaction(original, candidate)
        if not valid:
            set_local_model_availability(False, validation_error)
            return None

        set_local_model_availability(True, "Deterministic local scrub completed without residual PII")
        clear_frontier_scrub_fallback()
        return PIIScrubResult(
            text=candidate,
            policy=self.status()["frontier_scrub_mode"],
            source="deterministic_local",
            scrubbed=candidate != original,
            fallback_used=False,
            detected_categories=detected_categories,
            residual_categories=(),
            reason=None,
            pre_scrubbed=True,
            pre_scrub_source="deterministic_local",
            local_verification_used=False,
            scrub_stages=("deterministic_prescrub", "deterministic_validation"),
        )

    def _deterministic_clean_success(self, text: str) -> PIIScrubResult:
        set_local_model_availability(True, "Deterministic local scrub found no frontier PII")
        clear_frontier_scrub_fallback()
        return PIIScrubResult(
            text=text,
            policy=self.status()["frontier_scrub_mode"],
            source="deterministic_clean",
            scrubbed=False,
            fallback_used=False,
            detected_categories=(),
            residual_categories=(),
            reason=None,
            local_verification_used=False,
            scrub_stages=("deterministic_detection",),
        )

    @staticmethod
    def _deterministic_prescrub_needs_model_verification(text: str) -> bool:
        return any(
            _line_needs_frontier_model_verification(line)
            for line in str(text or "").splitlines()
        )

    def _scrub_via_role_cascade(
        self,
        text: str,
        *,
        original: str,
        detected_categories: tuple[str, ...],
        pre_scrubbed: bool,
        pre_scrub_source: Optional[str],
    ) -> tuple[Optional[PIIScrubResult], str]:
        roles = self.local_model_roles
        if roles is None:
            return None, "Local model role router unavailable"

        stages = ["deterministic_prescrub"]
        try:
            finder_input, finder_line_count, text_is_numbered = self._region_finder_input(
                text,
                original=original,
                pre_scrubbed=pre_scrubbed,
            )
            regions, finder_stage, reason = self._find_pii_regions_with_budget(
                roles,
                finder_input,
                line_count=finder_line_count,
                text_is_numbered=text_is_numbered,
            )
            if reason:
                logger.info("%s", reason)
                return None, reason
            stages.append(finder_stage)
        except Exception as exc:
            reason = f"Local scrub cascade region finder failed: {exc}"
            set_local_model_availability(False, reason)
            logger.warning("Local scrub cascade region finder failed: %s", exc)
            return None, reason

        if not regions:
            candidate = text
            valid, validation_error = validate_frontier_redaction(original, candidate)
            if not valid:
                set_local_model_availability(False, validation_error)
                return None, validation_error
            local_verification_used = finder_stage != "scrub_region_finder_skipped_no_candidates"
            set_local_model_availability(
                True,
                "Local scrub cascade found no remaining PII regions"
                if local_verification_used
                else "Deterministic scrub candidate filter found no local-model regions",
            )
            clear_frontier_scrub_fallback()
            return (
                PIIScrubResult(
                    text=candidate,
                    policy=self.status()["frontier_scrub_mode"],
                    source=(
                        "scrub_cascade_no_regions"
                        if local_verification_used
                        else "scrub_cascade_no_candidates"
                    ),
                    scrubbed=candidate != original,
                    fallback_used=False,
                    detected_categories=detected_categories,
                    residual_categories=(),
                    pre_scrubbed=pre_scrubbed,
                    pre_scrub_source=pre_scrub_source,
                    local_verification_used=local_verification_used,
                    scrub_stages=tuple(stages),
                ),
                "",
            )

        try:
            candidate, semantic_categories = self._apply_region_redactions(
                text,
                regions,
                roles=roles,
            )
            stages.append("scrub_segment_verifier")
        except Exception as exc:
            reason = f"Local scrub cascade segment verifier failed: {exc}"
            set_local_model_availability(False, reason)
            logger.warning("Local scrub cascade segment verifier failed: %s", exc)
            return None, reason

        combined_categories = tuple(sorted(set(detected_categories) | set(semantic_categories)))
        valid, validation_error = validate_frontier_redaction(original, candidate)
        if not valid:
            set_local_model_availability(False, validation_error)
            return None, validation_error

        set_local_model_availability(True, "Local scrub cascade ready")
        clear_frontier_scrub_fallback()
        return (
            PIIScrubResult(
                text=candidate,
                policy=self.status()["frontier_scrub_mode"],
                source="scrub_cascade",
                scrubbed=candidate != original,
                fallback_used=False,
                detected_categories=combined_categories,
                residual_categories=(),
                pre_scrubbed=pre_scrubbed,
                pre_scrub_source=pre_scrub_source,
                local_verification_used=True,
                scrub_stages=tuple(stages),
            ),
            "",
        )

    @staticmethod
    def _region_finder_input(
        text: str,
        *,
        original: str,
        pre_scrubbed: bool,
        radius: int = 1,
    ) -> tuple[str, int, bool]:
        """Condense large payloads to likely-private lines while preserving line numbers."""
        full_line_count = max(1, len(str(text or "").splitlines()) or 1)
        should_condense = (
            len(text or "") >= _FRONTIER_SCRUB_CASCADE_MIN_CHARS
            and (pre_scrubbed or _text_has_frontier_semantic_cue(text))
        )
        if not should_condense:
            return text, full_line_count, False

        scrubbed_lines = str(text or "").splitlines()
        original_lines = str(original or "").splitlines()
        if not scrubbed_lines:
            return text, full_line_count, False

        keep: set[int] = set()
        for idx, line in enumerate(scrubbed_lines):
            original_line = original_lines[idx] if idx < len(original_lines) else ""
            if line != original_line or _line_has_frontier_semantic_cue(original_line or line):
                for candidate in range(idx - radius, idx + radius + 1):
                    if 0 <= candidate < len(scrubbed_lines):
                        keep.add(candidate)

        if not keep:
            return "", full_line_count, True

        return (
            "\n".join(
                f"{idx + 1}|{line}"
                for idx, line in enumerate(scrubbed_lines)
                if idx in keep
            ),
            full_line_count,
            True,
        )

    def _apply_region_redactions(
        self,
        text: str,
        regions: list[ScrubRegion],
        *,
        roles: Any,
    ) -> tuple[str, tuple[str, ...]]:
        split_lines = str(text or "").splitlines(keepends=True)
        if not split_lines:
            split_lines = [str(text or "")]

        normalized_regions: list[ScrubRegion] = []
        for region in regions:
            normalized = region.normalized(len(split_lines))
            if normalized is not None:
                normalized_regions.append(normalized)

        if not normalized_regions:
            return text, ()

        labels: set[str] = set()
        for region in sorted(normalized_regions, key=lambda item: (item.start_line, item.end_line)):
            labels.add(region.label)
            start_idx = region.start_line - 1
            end_idx = region.end_line
            segment = "".join(split_lines[start_idx:end_idx])
            context = self._region_context(split_lines, start_idx, end_idx)
            redacted = roles.redact_segment(
                segment,
                context=context,
                label=region.label,
            )
            split_lines[start_idx:end_idx] = [self._preserve_trailing_newline(segment, redacted)]

        return "".join(split_lines), tuple(sorted(labels))

    @staticmethod
    def _region_context(lines: list[str], start_idx: int, end_idx: int, radius: int = 1) -> str:
        context_start = max(0, start_idx - radius)
        context_end = min(len(lines), end_idx + radius)
        return "".join(lines[context_start:context_end])

    @staticmethod
    def _preserve_trailing_newline(original: str, replacement: str) -> str:
        if original.endswith("\r\n") and not replacement.endswith(("\n", "\r")):
            return replacement + "\r\n"
        if original.endswith("\n") and not replacement.endswith(("\n", "\r")):
            return replacement + "\n"
        return replacement

    @staticmethod
    def _region_finder_input_limit(roles: Any) -> Optional[int]:
        config_for = getattr(roles, "config_for", None)
        if not callable(config_for):
            return None
        try:
            config = config_for(ROLE_SCRUB_REGION_FINDER)
            raw_value = getattr(config, "max_input_chars", None)
            if not isinstance(raw_value, (int, float, str)) or isinstance(raw_value, bool):
                return None
            value = int(raw_value or 0)
        except Exception:
            return None
        return value if value > 0 else None

    def _find_pii_regions_with_budget(
        self,
        roles: Any,
        finder_input: str,
        *,
        line_count: int,
        text_is_numbered: bool,
    ) -> tuple[list[ScrubRegion], str, str]:
        if not str(finder_input or "").strip():
            return [], "scrub_region_finder_skipped_no_candidates", ""

        finder_limit = self._region_finder_input_limit(roles)
        if not finder_limit or len(finder_input or "") <= finder_limit:
            return (
                self._call_region_finder(
                    roles,
                    finder_input,
                    line_count=line_count,
                    text_is_numbered=text_is_numbered,
                ),
                "scrub_region_finder",
                "",
            )

        chunks, reason = self._split_region_finder_windows(
            finder_input,
            max_chars=finder_limit,
            text_is_numbered=text_is_numbered,
        )
        if reason:
            return [], "", reason
        if len(chunks) > _FRONTIER_SCRUB_REGION_FINDER_MAX_CHUNKS:
            return (
                [],
                "",
                "Local scrub cascade region finder skipped: narrowed payload "
                "requires too many local model windows "
                f"({len(chunks)} > {_FRONTIER_SCRUB_REGION_FINDER_MAX_CHUNKS})",
            )

        regions: list[ScrubRegion] = []
        for chunk in chunks:
            regions.extend(
                self._call_region_finder(
                    roles,
                    chunk,
                    line_count=line_count,
                    text_is_numbered=True,
                )
            )
        return self._dedupe_regions(regions), "scrub_region_finder_chunked", ""

    @staticmethod
    def _call_region_finder(
        roles: Any,
        text: str,
        *,
        line_count: int,
        text_is_numbered: bool,
    ) -> list[ScrubRegion]:
        try:
            return roles.find_pii_regions(
                text,
                line_count=line_count,
                text_is_numbered=text_is_numbered,
            )
        except TypeError:
            return roles.find_pii_regions(text)

    @staticmethod
    def _split_region_finder_windows(
        text: str,
        *,
        max_chars: int,
        text_is_numbered: bool,
    ) -> tuple[list[str], str]:
        raw_lines = str(text or "").splitlines() or [str(text or "")]
        if text_is_numbered:
            lines = raw_lines
        else:
            lines = [f"{idx}|{line}" for idx, line in enumerate(raw_lines, start=1)]

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in lines:
            line_len = len(line) + 1
            if line_len > max_chars:
                return (
                    [],
                    "Local scrub cascade region finder skipped: one narrowed line "
                    f"exceeds configured local model guard ({line_len} > {max_chars} chars)",
                )
            if current and current_len + line_len > max_chars:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += line_len

        if current:
            chunks.append("\n".join(current))
        return chunks, ""

    @staticmethod
    def _dedupe_regions(regions: list[ScrubRegion]) -> list[ScrubRegion]:
        deduped: dict[tuple[int, int, str], ScrubRegion] = {}
        for region in regions:
            key = (region.start_line, region.end_line, region.label)
            existing = deduped.get(key)
            if existing is None or region.confidence > existing.confidence:
                deduped[key] = region
        return list(deduped.values())

    @staticmethod
    def _is_expected_cascade_size_skip(reason: str) -> bool:
        return "region finder skipped" in str(reason or "").lower()

    def _active_fallback_backoff_reason(self, status: dict) -> Optional[str]:
        """Avoid repeated local-model timeouts during the same chat turn."""
        if _FRONTIER_SCRUB_BACKOFF_S <= 0:
            return None
        if not status.get("frontier_scrub_fallback_active"):
            return None

        reason = str(status.get("last_frontier_scrub_fallback_reason") or "")
        if not self._can_use_deterministic_fallback(reason):
            return None

        fallback_at = status.get("last_frontier_scrub_fallback_at")
        if not isinstance(fallback_at, (int, float)):
            return None
        elapsed = time.time() - float(fallback_at)
        if elapsed < 0 or elapsed > _FRONTIER_SCRUB_BACKOFF_S:
            return None

        return (
            f"{reason}; local scrubber retry backoff active "
            f"({elapsed:.1f}s elapsed, {_FRONTIER_SCRUB_BACKOFF_S:.1f}s window)"
        )

    def _scrub_text_in_chunks(
        self,
        text: str,
        *,
        original: str,
        detected_categories: tuple[str, ...],
        pre_scrubbed: bool = False,
        pre_scrub_source: Optional[str] = None,
    ) -> tuple[Optional[PIIScrubResult], str]:
        chunks = split_text_for_frontier_redaction(text)
        if len(chunks) <= 1:
            return None, "Local scrubbing unavailable"

        outputs: list[str] = []
        sources: list[str] = []
        fallback_reasons: list[str] = []
        last_failure = "Local scrubbing unavailable"

        for chunk in chunks:
            chunk_candidate = None
            chunk_source = ""

            routed = self._scrub_via_router(chunk)
            if routed is not None:
                candidate, last_failure = routed
                if candidate is not None:
                    chunk_candidate = candidate
                    chunk_source = "model_router"

            if chunk_candidate is None and self._can_use_deterministic_fallback(last_failure):
                chunk_candidate = redact_frontier_pii_deterministically(chunk)
                chunk_source = "deterministic_local"
                fallback_reasons.append(last_failure)

            if chunk_candidate is None:
                direct = self._scrub_via_local_model(chunk)
                if direct is not None:
                    candidate, last_failure = direct
                    if candidate is not None:
                        chunk_candidate = candidate
                        chunk_source = "local_model"

            if chunk_candidate is None and self._can_use_deterministic_fallback(last_failure):
                chunk_candidate = redact_frontier_pii_deterministically(chunk)
                chunk_source = "deterministic_local"
                fallback_reasons.append(last_failure)

            if chunk_candidate is None:
                return None, last_failure

            outputs.append(chunk_candidate)
            sources.append(chunk_source)

        candidate = "".join(outputs)
        valid, validation_error = validate_frontier_redaction(original, candidate)
        if not valid:
            set_local_model_availability(False, validation_error)
            return None, validation_error

        used_fallback = bool(fallback_reasons)
        base_source = (
            f"chunked_{sources[0]}"
            if len(set(sources)) == 1
            else "chunked_mixed"
        )
        source = (
            f"{base_source}_after_deterministic_prescrub"
            if pre_scrubbed
            else base_source
        )
        reason = None
        if used_fallback:
            reason = (
                "; ".join(dict.fromkeys(fallback_reasons))
                + "; deterministic local scrub fallback used for one or more chunks"
            )
            set_local_model_availability(False, reason)
            record_frontier_scrub_fallback(reason)
        else:
            set_local_model_availability(True, "Local chunked redaction lane ready")
            clear_frontier_scrub_fallback()

        return (
            PIIScrubResult(
                text=candidate,
                policy=self.status()["frontier_scrub_mode"],
                source=source,
                scrubbed=candidate != original,
                fallback_used=used_fallback,
                detected_categories=detected_categories,
                residual_categories=(),
                reason=reason,
                pre_scrubbed=pre_scrubbed,
                pre_scrub_source=pre_scrub_source,
                local_verification_used=any(
                    source_name in {"model_router", "local_model"}
                    for source_name in sources
                ),
                scrub_stages=(
                    ("deterministic_prescrub",) if pre_scrubbed else ()
                )
                + (
                    ("local_model_verification",)
                    if any(
                        source_name in {"model_router", "local_model"}
                        for source_name in sources
                    )
                    else ()
                )
                + (
                    ("deterministic_fallback",)
                    if any(source_name == "deterministic_local" for source_name in sources)
                    else ()
                )
                + ("deterministic_validation",),
            ),
            "",
        )

    @staticmethod
    def _can_use_deterministic_fallback(reason: str) -> bool:
        lowered = (reason or "").lower()
        return (
            "direct local redaction failed" in lowered
            or "local redaction router failed" in lowered
            or "timed out" in lowered
            or "timeout" in lowered
            or "context window exceeded" in lowered
            or "health check failed" in lowered
            or "empty redaction output" in lowered
            or "local scrub cascade" in lowered
        )

    def _scrub_deterministically(
        self,
        text: str,
        *,
        failure_reason: str,
        detected_categories: tuple[str, ...],
        pre_scrubbed: bool = False,
        pre_scrub_source: Optional[str] = None,
        record_runtime_fallback: bool = True,
    ) -> Optional[PIIScrubResult]:
        candidate = redact_frontier_pii_deterministically(text)
        valid, validation_error = validate_frontier_redaction(text, candidate)
        if not valid:
            set_local_model_availability(False, validation_error)
            return None

        reason = f"{failure_reason}; deterministic local scrub fallback used"
        if record_runtime_fallback:
            set_local_model_availability(False, failure_reason)
            record_frontier_scrub_fallback(reason)
        return PIIScrubResult(
            text=candidate,
            policy=self.status()["frontier_scrub_mode"],
            source="deterministic_local",
            scrubbed=candidate != text,
            fallback_used=True,
            detected_categories=detected_categories,
            residual_categories=(),
            reason=reason,
            pre_scrubbed=pre_scrubbed,
            pre_scrub_source=pre_scrub_source,
            local_verification_used=False,
            scrub_stages=(
                ("deterministic_prescrub",) if pre_scrubbed else ()
            ) + ("deterministic_fallback", "deterministic_validation"),
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
                "Local redaction router failed; using configured scrub fallback path: %s",
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
            logger.warning("Direct local redaction failed; using configured scrub fallback path: %s", exc)
        return None, failure_reason
