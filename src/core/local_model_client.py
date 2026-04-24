"""
Local model client for the local utility service.

Single-owner module providing high-level methods for the five utility tasks:
    classify_intent, extract_json, summarize, redact, rag_rewrite

Talks to the local-llm Docker service over HTTP (default http://localhost:8080).

Public API:
    LocalModelClient(base_url=None, role="utility")
    client.health()             → dict
    client.is_healthy()         → bool
    client.complete(prompt, **) → str
    client.classify_intent(text)        → str
    client.extract_json(text, schema)   → dict
    client.summarize(text)              → str
    client.redact(text)                 → str
    client.rag_rewrite(query)           → str
"""

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Optional

from src.core.outbound_http import LocalControlPlaneError, assert_local_control_url
from local_models.lockfile import load_all_prompts

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8080"
_LOCAL_REDACT_TIMEOUT_S = max(
    1.0,
    float(os.environ.get("LANCELOT_LOCAL_REDACT_TIMEOUT_S", "10")),
)
_LOCAL_MODEL_ALLOWED_HOSTNAMES = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
    "local-llm",
})


class LocalModelError(Exception):
    """Raised when a local model request fails."""


class LocalModelClient:
    """HTTP client for the local-llm utility service."""

    def __init__(self, base_url: Optional[str] = None, role: str = "utility"):
        self._base_url = (
            base_url
            or os.environ.get("LOCAL_LLM_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.role = role
        self._prompts: Optional[dict] = None

    # ------------------------------------------------------------------
    # Prompt template loading (lazy, cached)
    # ------------------------------------------------------------------

    def _get_prompts(self) -> dict:
        if self._prompts is None:
            self._prompts = load_all_prompts()
        return self._prompts

    def _render(self, name: str, **kwargs) -> str:
        """Render a prompt template with variables."""
        template = self._get_prompts()[name]
        return template.format(**kwargs)

    def _local_url(self, path: str) -> str:
        url = f"{self._base_url}{path}"
        return assert_local_control_url(
            url,
            component="Local utility model request",
            allowed_hostnames=_LOCAL_MODEL_ALLOWED_HOSTNAMES,
            allow_single_label_hostnames=True,
        )

    # ------------------------------------------------------------------
    # Low-level HTTP
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_http_error_body(exc: urllib.error.HTTPError) -> str:
        """Best-effort HTTP error body decode with explicit observability."""
        try:
            return exc.read().decode("utf-8", errors="replace")
        except Exception as decode_exc:
            logger.warning(
                "Failed to decode local model HTTP error body for %s: %s",
                getattr(exc, "filename", "unknown"),
                decode_exc,
            )
            return ""

    def _post(self, path: str, payload: dict, timeout: float = 30.0) -> dict:
        """POST JSON to the local-llm service and return parsed response."""
        try:
            url = self._local_url(path)
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except LocalControlPlaneError as exc:
            raise LocalModelError(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            body = self._decode_http_error_body(exc)
            raise LocalModelError(
                f"HTTP {exc.code} from {url}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LocalModelError(
                f"Connection failed to {url}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise LocalModelError(f"Request failed: {exc}") from exc

    def _get(self, path: str, timeout: float = 10.0) -> dict:
        """GET from the local-llm service and return parsed response."""
        try:
            url = self._local_url(path)
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except LocalControlPlaneError as exc:
            raise LocalModelError(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            body = self._decode_http_error_body(exc)
            raise LocalModelError(
                f"HTTP {exc.code} from {url}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LocalModelError(
                f"Connection failed to {url}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise LocalModelError(f"Request failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict:
        """Return readiness status from the local-llm service.

        Returns readiness metadata including loaded/ready state.
        Raises LocalModelError only when the service is unreachable or
        returns a non-JSON error body.
        """
        try:
            url = self._local_url("/health")
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                return _normalize_health_payload(json.loads(resp.read().decode("utf-8")))
        except LocalControlPlaneError as exc:
            raise LocalModelError(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            body = self._decode_http_error_body(exc)
            if body:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError as parse_exc:
                    logger.warning(
                        "Local model health returned non-JSON HTTP error body from %s: %s",
                        url,
                        parse_exc,
                    )
                else:
                    if isinstance(parsed, dict):
                        return _normalize_health_payload(parsed)
            raise LocalModelError(
                f"HTTP {exc.code} from {url}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LocalModelError(
                f"Connection failed to {url}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise LocalModelError(f"Request failed: {exc}") from exc

    def is_healthy(self) -> bool:
        """Quick readiness check — True only when inference smoke has passed."""
        try:
            data = self.health()
            return data.get("ready") is True
        except LocalModelError:
            return False

    # ------------------------------------------------------------------
    # Raw completion
    # ------------------------------------------------------------------

    def complete(
        self,
        prompt: str,
        max_tokens: int = 128,
        temperature: float = 0.1,
        stop: Optional[list] = None,
        timeout: float = 30.0,
    ) -> str:
        """Run a raw text completion against the local model.

        Returns the generated text string.
        """
        payload = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop is not None:
            payload["stop"] = stop

        result = self._post("/v1/completions", payload, timeout=timeout)
        return _completion_text(result)

    # ------------------------------------------------------------------
    # Utility task methods
    # ------------------------------------------------------------------

    def classify_intent(self, text: str) -> str:
        """Classify user intent into a category.

        Returns one of: question, command, information, greeting,
        feedback, unclear.
        """
        prompt = self._render("classify_intent", input=text)
        raw = self.complete(
            prompt,
            max_tokens=16,
            temperature=0.0,
            stop=["\n"],
        )
        allowed = {"question", "command", "information", "greeting", "feedback", "unclear"}
        labels = _extract_allowed_labels(raw, allowed)
        if len(labels) == 1:
            return labels[0]
        return _classify_intent_heuristically(text)

    def extract_json(self, text: str, schema: str) -> dict:
        """Extract structured data from text as JSON.

        Args:
            text: The source text to extract from.
            schema: JSON schema description for the output.

        Returns parsed dict. Raises LocalModelError if output is not
        valid JSON.
        """
        prompt = self._render("extract_json", input=text, schema=schema)
        raw = self.complete(prompt, max_tokens=512, temperature=0.0)
        cleaned = raw.strip()

        # Strip markdown code fences if present
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LocalModelError(
                f"Model returned invalid JSON: {exc}\nRaw output: {raw!r}"
            ) from exc

    def summarize(self, text: str) -> str:
        """Summarize text in 2-3 concise sentences."""
        prompt = self._render("summarize_internal", input=text)
        raw = self.complete(
            prompt,
            max_tokens=256,
            temperature=0.1,
            stop=["\n\n"],
        )
        return raw.strip()

    def redact(self, text: str) -> str:
        """Redact PII from text, replacing with bracketed type markers."""
        prompt = self._render("redact", input=text)
        raw = self.complete(
            prompt,
            max_tokens=512,
            temperature=0.0,
            timeout=_LOCAL_REDACT_TIMEOUT_S,
        )
        return raw.strip()

    def rag_rewrite(self, query: str) -> str:
        """Rewrite a query for improved vector database retrieval."""
        prompt = self._render("rag_rewrite", input=query)
        raw = self.complete(
            prompt,
            max_tokens=96,
            temperature=0.0,
            stop=["\n"],
        )
        return _first_nonempty_line(raw)

    def verify_routing_intent(self, text: str) -> str:
        """Verify routing intent using the local model as a second opinion.

        V21: Used when the keyword classifier produces PLAN_REQUEST or
        EXEC_REQUEST but the message is long enough to be ambiguous.
        The local model reads the full message and decides if the user
        actually wants a plan, an action, or is asking a question.

        Returns one of: plan, action, question
        """
        prompt = self._render("verify_intent", input=text)
        raw = self.complete(prompt, max_tokens=16, temperature=0.0, timeout=10.0)
        return _extract_first_allowed_label(
            raw,
            {"plan", "action", "question"},
            default="action",
        )

    # ------------------------------------------------------------------
    # Fix Pack V8: Chat completions with tool/function calling
    # ------------------------------------------------------------------

    def chat_with_tools(
        self,
        messages: list,
        tools: Optional[list] = None,
        max_tokens: int = 512,
        temperature: float = 0.1,
        tool_choice: Optional[str] = None,
        timeout: float = 60.0,
    ) -> dict:
        """Chat completion with optional tool/function calling support.

        Uses the /v1/chat/completions endpoint (OpenAI-compatible).

        Args:
            messages: List of {role, content} dicts.
            tools: Optional list of tool declarations (OpenAI format).
            max_tokens: Max tokens for generation.
            temperature: Sampling temperature.
            tool_choice: Optional tool choice constraint.
            timeout: Request timeout in seconds.

        Returns:
            Full OpenAI-format chat completion response dict.
        """
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        return self._post("/v1/chat/completions", payload, timeout=timeout)


def _normalize_health_payload(payload: dict) -> dict:
    """Normalize Lancelot wrapper and native llama-server health payloads."""
    if not isinstance(payload, dict):
        return {"loaded": False, "ready": False, "status": "unavailable"}
    normalized = dict(payload)
    status = str(normalized.get("status") or "").lower()
    if "ready" not in normalized:
        normalized["ready"] = status == "ok"
    if "loaded" not in normalized:
        normalized["loaded"] = bool(normalized.get("ready")) or status == "ok"
    if "last_error" not in normalized:
        normalized["last_error"] = None if normalized.get("ready") else normalized.get("error")
    return normalized


def _completion_text(payload: dict) -> str:
    """Extract text from Lancelot wrapper or OpenAI-style completions."""
    if not isinstance(payload, dict):
        raise LocalModelError("Local model returned a non-object completion payload")
    if "text" in payload:
        return str(payload.get("text") or "")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        if "text" in first:
            return str(first.get("text") or "")
        message = first.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
    raise LocalModelError("Local model completion payload did not include text")


def _extract_first_allowed_label(raw: str, allowed: set[str], *, default: str) -> str:
    """Extract a bounded classifier label from noisy local model output."""
    labels = _extract_allowed_labels(raw, allowed)
    if labels:
        return labels[0]
    return default


def _extract_allowed_labels(raw: str, allowed: set[str]) -> list[str]:
    labels: list[str] = []
    lowered = str(raw or "").lower()
    for word in lowered.split():
        cleaned = word.strip(".,!?:;'\"`[](){}\n\r")
        if cleaned in allowed and cleaned not in labels:
            labels.append(cleaned)
    return labels


def _first_nonempty_line(raw: str) -> str:
    for line in str(raw or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return ""


def _classify_intent_heuristically(text: str) -> str:
    """Deterministic fallback when a small local model echoes the label list."""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return "unclear"
    if not any(ch.isalnum() for ch in lowered):
        return "unclear"
    if lowered in {"hi", "hello", "hey"} or lowered.startswith(("hi ", "hello ", "hey ")):
        return "greeting"
    if any(token in lowered for token in ("thanks", "thank you", "great job", "that worked")):
        return "feedback"
    if lowered.endswith("?") or lowered.split(" ", 1)[0] in {
        "what", "why", "how", "when", "where", "who", "can", "could", "should", "would",
    }:
        return "question"
    if lowered.split(" ", 1)[0] in {
        "do", "run", "create", "write", "update", "delete", "remove", "commit", "push",
        "summarize", "explain", "find", "check", "fix", "build", "test",
    }:
        return "command"
    return "information"
