"""
LocalModelClient — HTTP client for the local-llm utility service (Prompt 13).

Single-owner module providing high-level methods for the five utility tasks:
    classify_intent, extract_json, summarize, redact, rag_rewrite

Talks to the local-llm Docker service over HTTP (default http://localhost:8080).

Public API:
    LocalModelClient(base_url=None)
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

from local_models.lockfile import load_all_prompts

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8080"


class LocalModelError(Exception):
    """Raised when a local model request fails."""


class LocalModelClient:
    """HTTP client for the local-llm utility service."""

    def __init__(self, base_url: Optional[str] = None):
        self._base_url = (
            base_url
            or os.environ.get("LOCAL_LLM_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
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

    # ------------------------------------------------------------------
    # Low-level HTTP
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict, timeout: float = 30.0) -> dict:
        """POST JSON to the local-llm service and return parsed response."""
        url = f"{self._base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
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
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
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
        """Return health status from the local-llm service.

        Returns dict with keys: status, model, uptime_seconds.
        Raises LocalModelError on failure.
        """
        return self._get("/health")

    def is_healthy(self) -> bool:
        """Quick health check — True if the service is up and model loaded."""
        try:
            data = self.health()
            return data.get("status") == "ok"
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
        return result["text"]

    # ------------------------------------------------------------------
    # Utility task methods
    # ------------------------------------------------------------------

    def classify_intent(self, text: str) -> str:
        """Classify user intent into a category.

        Returns one of: question, command, information, greeting,
        feedback, unclear.
        """
        prompt = self._render("classify_intent", input=text)
        raw = self.complete(prompt, max_tokens=16, temperature=0.0)
        return raw.strip().lower()

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
        raw = self.complete(prompt, max_tokens=256, temperature=0.1)
        return raw.strip()

    def redact(self, text: str) -> str:
        """Redact PII from text, replacing with bracketed type markers."""
        prompt = self._render("redact", input=text)
        raw = self.complete(prompt, max_tokens=512, temperature=0.0)
        return raw.strip()

    def rag_rewrite(self, query: str) -> str:
        """Rewrite a query for improved vector database retrieval."""
        prompt = self._render("rag_rewrite", input=query)
        raw = self.complete(prompt, max_tokens=128, temperature=0.1)
        return raw.strip()
