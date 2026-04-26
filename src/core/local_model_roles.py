"""
Role-based routing for local model usage.

The local model runtime has two product responsibilities:

- privacy/scrub work that protects frontier egress
- low-risk utility work that saves frontier tokens

Those responsibilities can share one physical local model today, or point to
separate local model services later. The role router keeps that deployment
choice out of callers.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

from src.core.local_model_client import LocalModelClient, LocalModelError

logger = logging.getLogger(__name__)

ROLE_SCRUB_REGION_FINDER = "scrub_region_finder"
ROLE_SCRUB_SEGMENT_VERIFIER = "scrub_segment_verifier"
ROLE_UTILITY = "utility"

SCRUB_ROLES = frozenset({
    ROLE_SCRUB_REGION_FINDER,
    ROLE_SCRUB_SEGMENT_VERIFIER,
})

VALID_LOCAL_MODEL_ROLES = frozenset({
    ROLE_SCRUB_REGION_FINDER,
    ROLE_SCRUB_SEGMENT_VERIFIER,
    ROLE_UTILITY,
})


class LocalModelRoleError(RuntimeError):
    """Raised when a role-specific local model operation fails."""


@dataclass(frozen=True)
class LocalModelRoleConfig:
    role: str
    base_url: str
    model: str
    priority: int
    timeout_s: float
    max_input_chars: int
    enabled: bool = True
    health_timeout_s: float = 1.0

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "base_url": self.base_url,
            "model": self.model,
            "priority": self.priority,
            "timeout_s": self.timeout_s,
            "health_timeout_s": self.health_timeout_s,
            "max_input_chars": self.max_input_chars,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class ScrubRegion:
    start_line: int
    end_line: int
    label: str
    confidence: float = 0.0
    reason: str = ""

    def normalized(self, line_count: int) -> Optional["ScrubRegion"]:
        if line_count <= 0:
            return None
        start = max(1, min(self.start_line, line_count))
        end = max(start, min(self.end_line, line_count))
        return ScrubRegion(
            start_line=start,
            end_line=end,
            label=self.label or "pii",
            confidence=max(0.0, min(float(self.confidence or 0.0), 1.0)),
            reason=self.reason or "",
        )


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _role_base_url(role: str, *, fallback: str) -> str:
    env_names = {
        ROLE_SCRUB_REGION_FINDER: (
            "LOCAL_LLM_SCRUB_REGION_FINDER_URL",
            "LOCAL_LLM_BONSAI_1_7B_URL",
            "LOCAL_LLM_URL",
        ),
        ROLE_SCRUB_SEGMENT_VERIFIER: (
            "LOCAL_LLM_SCRUB_SEGMENT_VERIFIER_URL",
            "LOCAL_LLM_BONSAI_8B_URL",
            "LOCAL_LLM_URL",
        ),
        ROLE_UTILITY: (
            "LOCAL_LLM_UTILITY_URL",
            "LOCAL_LLM_BONSAI_8B_URL",
            "LOCAL_LLM_URL",
        ),
    }[role]
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value
    return fallback


def load_local_model_role_configs() -> dict[str, LocalModelRoleConfig]:
    """Load role config from environment with safe single-model defaults."""
    default_url = os.environ.get("LOCAL_LLM_URL", "http://localhost:8080")
    default_model = os.environ.get("LOCAL_LLM_MODEL", "local-llm")
    default_health_timeout = _env_float("LANCELOT_LOCAL_HEALTH_TIMEOUT_S", 1.0)
    configs = {
        ROLE_SCRUB_REGION_FINDER: LocalModelRoleConfig(
            role=ROLE_SCRUB_REGION_FINDER,
            base_url=_role_base_url(ROLE_SCRUB_REGION_FINDER, fallback=default_url),
            model=os.environ.get("LOCAL_LLM_SCRUB_REGION_FINDER_MODEL", default_model),
            priority=10,
            timeout_s=_env_float("LANCELOT_SCRUB_REGION_FINDER_TIMEOUT_S", 8.0),
            max_input_chars=_env_int("LANCELOT_SCRUB_REGION_FINDER_MAX_CHARS", 6_000),
            enabled=_env_bool("LANCELOT_SCRUB_REGION_FINDER_ENABLED", True),
            health_timeout_s=_env_float(
                "LANCELOT_SCRUB_REGION_FINDER_HEALTH_TIMEOUT_S",
                default_health_timeout,
            ),
        ),
        ROLE_SCRUB_SEGMENT_VERIFIER: LocalModelRoleConfig(
            role=ROLE_SCRUB_SEGMENT_VERIFIER,
            base_url=_role_base_url(ROLE_SCRUB_SEGMENT_VERIFIER, fallback=default_url),
            model=os.environ.get("LOCAL_LLM_SCRUB_SEGMENT_VERIFIER_MODEL", default_model),
            priority=9,
            timeout_s=_env_float("LANCELOT_SCRUB_SEGMENT_VERIFIER_TIMEOUT_S", 10.0),
            max_input_chars=_env_int("LANCELOT_SCRUB_SEGMENT_VERIFIER_MAX_CHARS", 8_000),
            enabled=_env_bool("LANCELOT_SCRUB_SEGMENT_VERIFIER_ENABLED", True),
            health_timeout_s=_env_float(
                "LANCELOT_SCRUB_SEGMENT_VERIFIER_HEALTH_TIMEOUT_S",
                default_health_timeout,
            ),
        ),
        ROLE_UTILITY: LocalModelRoleConfig(
            role=ROLE_UTILITY,
            base_url=_role_base_url(ROLE_UTILITY, fallback=default_url),
            model=os.environ.get("LOCAL_LLM_UTILITY_MODEL", default_model),
            priority=1,
            timeout_s=_env_float("LANCELOT_LOCAL_UTILITY_TIMEOUT_S", 30.0),
            max_input_chars=_env_int("LANCELOT_LOCAL_UTILITY_MAX_CHARS", 12_000),
            enabled=_env_bool("LANCELOT_LOCAL_UTILITY_ENABLED", True),
            health_timeout_s=_env_float(
                "LANCELOT_LOCAL_UTILITY_HEALTH_TIMEOUT_S",
                default_health_timeout,
            ),
        ),
    }
    return configs


class LocalModelRoleRouter:
    """Resolve local model clients by role and run scrub-specific helpers."""

    def __init__(
        self,
        configs: Optional[dict[str, LocalModelRoleConfig]] = None,
        *,
        client_factory: Callable[..., LocalModelClient] = LocalModelClient,
    ):
        self._configs = configs or load_local_model_role_configs()
        self._client_factory = client_factory
        self._clients: dict[str, LocalModelClient] = {}

    @classmethod
    def from_env(cls) -> "LocalModelRoleRouter":
        return cls(load_local_model_role_configs())

    def config_for(self, role: str) -> LocalModelRoleConfig:
        if role not in VALID_LOCAL_MODEL_ROLES:
            raise LocalModelRoleError(f"Unknown local model role: {role}")
        config = self._configs.get(role)
        if config is None:
            raise LocalModelRoleError(f"Local model role not configured: {role}")
        if not config.enabled:
            raise LocalModelRoleError(f"Local model role disabled: {role}")
        return config

    def client_for(self, role: str) -> LocalModelClient:
        config = self.config_for(role)
        if role not in self._clients:
            self._clients[role] = self._client_factory(
                base_url=config.base_url,
                role=role,
            )
        return self._clients[role]

    def status(self) -> dict:
        roles = {}
        for role, config in self._configs.items():
            payload = {
                "configured": True,
                "enabled": config.enabled,
                "model": config.model,
                "base_url": config.base_url,
                "priority": config.priority,
                "ready": False,
                "loaded": False,
                "status": "disabled" if not config.enabled else "unknown",
                "last_error": None,
            }
            if config.enabled:
                try:
                    health = self.client_for(role).health(timeout=config.health_timeout_s)
                    payload.update({
                        "ready": bool(health.get("ready")),
                        "loaded": bool(health.get("loaded", health.get("ready"))),
                        "model": health.get("model") or config.model,
                        "status": health.get("status") or (
                            "ok" if health.get("ready") else "degraded"
                        ),
                        "last_error": health.get("last_error"),
                        "last_verified_at": health.get("last_verified_at"),
                        "last_checked_at": health.get("last_checked_at"),
                        "consecutive_failures": health.get("consecutive_failures", 0),
                        "last_smoke_elapsed_ms": health.get("last_smoke_elapsed_ms"),
                    })
                except Exception as exc:
                    payload.update({
                        "status": "unavailable",
                        "last_error": str(exc),
                    })
            roles[role] = payload
        return {
            "roles": roles,
            "scrub_priority": "scrub roles must run before utility work",
        }

    def find_pii_regions(
        self,
        text: str,
        *,
        line_count: Optional[int] = None,
        text_is_numbered: bool = False,
    ) -> list[ScrubRegion]:
        """Use the region-finder role to identify lines needing model cleanup."""
        config = self.config_for(ROLE_SCRUB_REGION_FINDER)
        if len(text or "") > config.max_input_chars:
            raise LocalModelRoleError(
                f"Region finder input exceeds configured limit "
                f"({len(text)} > {config.max_input_chars})"
            )

        numbered = str(text or "") if text_is_numbered else _numbered_lines(text)
        prompt = (
            "Task: find ONLY lines containing private data. "
            "Return exactly one JSON array and then stop.\n"
            'Schema: [{"line": 2, "label": "email", '
            '"confidence": 0.99, "reason": "contains email address"}]\n'
            "Private data includes PII, secrets, credentials, reset or OTP codes, "
            "passwords, API keys, bearer tokens, provider tokens, private URLs, "
            "names, addresses, account identifiers, ticket/case identifiers, "
            "medical identifiers, and financial identifiers.\n"
            "Examples of private lines: GitHub token ghp_..., password hunter2, "
            "reset code 123456, address 404 Nowhere Lane, case 998877.\n"
            "Rules:\n"
            "- line must be an integer from the prefix before |.\n"
            "- Do not include harmless lines or examples that only contain placeholders.\n"
            "- Do not rewrite the text.\n"
            "- Do not use markdown.\n"
            "- If no private data exists, return [].\n"
            f"Text:\n{numbered}\nJSON array:"
        )
        raw = self.client_for(ROLE_SCRUB_REGION_FINDER).complete(
            prompt,
            max_tokens=384,
            temperature=0.0,
            stop=["```", "\n\n\n"],
            timeout=config.timeout_s,
        )
        normalized_line_count = line_count or max(1, len(text.splitlines()) or 1)
        return parse_scrub_regions(raw, line_count=normalized_line_count)

    def redact_segment(self, segment: str, *, context: str = "", label: str = "pii") -> str:
        """Use the verifier role to redact one bounded suspicious segment."""
        config = self.config_for(ROLE_SCRUB_SEGMENT_VERIFIER)
        if len(segment or "") > config.max_input_chars:
            raise LocalModelRoleError(
                f"Segment verifier input exceeds configured limit "
                f"({len(segment)} > {config.max_input_chars})"
            )
        segment_text = str(segment or "")
        prompt = (
            "Replace person names with [NAME]. Replace email addresses with [EMAIL]. "
            "Replace phone numbers with [PHONE]. Replace street addresses with [ADDRESS]. "
            "Replace passwords, passphrases, reset codes, OTP/PIN values, bearer tokens, "
            "GitHub/GitLab/Slack/Stripe/OpenAI-style tokens, API keys, and secrets "
            "with [SECRET]. Replace private reset or credential URLs with [URL]. "
            "Replace account, ticket, customer, and case identifiers with [ACCOUNT_ID]. "
            "If a value is already bracketed, keep it bracketed and continue redacting "
            "nearby names and identifiers. Preserve harmless text and punctuation. "
            "Return only the rewritten segment.\n"
            "Input: Contact Myles at myles@example.com for ticket 123.\n"
            "Output: Contact [NAME] at [EMAIL] for ticket [ACCOUNT_ID].\n"
            "Input: Escalation owner Bob at [EMAIL] for case 998877.\n"
            "Output: Escalation owner [NAME] at [EMAIL] for case [ACCOUNT_ID].\n"
            "Input: Reset code 492817, password hunter2, GitHub token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456.\n"
            "Output: Reset code [SECRET], password [SECRET], GitHub token [SECRET].\n"
            f"Suspicion label: {label}\n"
            f"Context:\n{context}\n"
            f"Input: {segment_text}\n"
            "Output:"
        )
        stop = ["```", "\n\n"]
        if "\n" not in segment_text.strip("\r\n"):
            stop.append("\n")
        raw = self.client_for(ROLE_SCRUB_SEGMENT_VERIFIER).complete(
            prompt,
            max_tokens=max(32, min(512, len(segment_text.split()) * 4 + 32)),
            temperature=0.0,
            stop=stop,
            timeout=config.timeout_s,
        )
        return _clean_redacted_segment(raw, original=segment_text)


def _numbered_lines(text: str) -> str:
    lines = str(text or "").splitlines() or [str(text or "")]
    return "\n".join(f"{idx}|{line}" for idx, line in enumerate(lines, start=1))


def _strip_code_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = [
            line for line in cleaned.splitlines()
            if not line.strip().startswith("```")
        ]
        return "\n".join(lines).strip()
    return cleaned


def _first_balanced_json_array(text: str) -> Optional[str]:
    start = -1
    depth = 0
    in_string = False
    escape = False
    for idx, char in enumerate(str(text or "")):
        if start < 0:
            if char == "[":
                start = idx
                depth = 1
            continue

        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _extract_json_array(text: str) -> list:
    cleaned = _strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        candidate = _first_balanced_json_array(cleaned)
        if candidate is None:
            raise LocalModelRoleError("Region finder did not return a JSON array")
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LocalModelRoleError(
                f"Region finder returned invalid JSON: {exc}"
            ) from exc
        return parsed if isinstance(parsed, list) else []


def _line_number_value(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = re.match(r"\s*(\d+)", str(value or ""))
    if match:
        return int(match.group(1))
    return None


def _clean_redacted_segment(raw: str, *, original: str) -> str:
    cleaned = _strip_code_fences(raw).strip()
    if not cleaned:
        return cleaned
    if "\n" not in str(original or "").strip("\r\n"):
        return cleaned.splitlines()[0].strip()
    return cleaned


def parse_scrub_regions(raw: str, *, line_count: int) -> list[ScrubRegion]:
    """Parse region-finder JSON into normalized line regions."""
    items = _extract_json_array(raw)
    regions: list[ScrubRegion] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        line = _line_number_value(item.get("line"))
        start_line = _line_number_value(item.get("start_line", line))
        end_line = _line_number_value(item.get("end_line", line if line is not None else start_line))
        try:
            region = ScrubRegion(
                start_line=int(start_line),
                end_line=int(end_line),
                label=str(item.get("label") or "pii").strip().lower() or "pii",
                confidence=float(item.get("confidence") or 0.0),
                reason=str(item.get("reason") or ""),
            ).normalized(line_count)
        except (TypeError, ValueError):
            continue
        if region is not None:
            regions.append(region)

    deduped: dict[tuple[int, int, str], ScrubRegion] = {}
    for region in regions:
        deduped[(region.start_line, region.end_line, region.label)] = region
    return list(deduped.values())
