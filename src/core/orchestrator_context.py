"""Context memory, cache, and rule persistence helpers for the orchestrator."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any


_logger = logging.getLogger("orchestrator.context")

TIER_A_MEMORY_FILES = (
    "USER.md",
    "RULES.md",
    "MEMORY_SUMMARY.md",
    "CAPABILITIES.md",
)


def load_memory(runtime: Any) -> None:
    """Load Tier A memory files and verify the signed rules file."""
    _logger.info("Loading memory into Context Environment.")

    for filename in TIER_A_MEMORY_FILES:
        runtime.context_env.read_file(filename)

    try:
        sig_path = os.path.join(runtime.data_dir, "RULES.md.sig")
        rules_path = os.path.join(runtime.data_dir, "RULES.md")
        if os.path.exists(sig_path) and os.path.exists(rules_path):
            hmac_key = os.getenv("LANCELOT_HMAC_KEY", "default-dev-key")
            with open(rules_path, "rb") as rules_file:
                rules_bytes = rules_file.read()
            expected_sig = hmac.new(hmac_key.encode(), rules_bytes, hashlib.sha256).hexdigest()
            with open(sig_path, "r") as sig_file:
                stored_sig = sig_file.read().strip()
            if expected_sig != stored_sig:
                logging.warning("HMAC signature mismatch for RULES.md - file may have been tampered with")
    except Exception as exc:
        logging.warning("HMAC check failed: %s", exc)

    _logger.info("Memory loaded into ContextEnv.")


def init_context_cache(runtime: Any) -> None:
    """Create a Gemini context cache for static memory content when available."""
    if not runtime.provider:
        return

    if runtime.provider.provider_name != "gemini":
        _logger.debug(
            "context_caching_unsupported",
            extra={"provider": runtime.provider.provider_name},
        )
        runtime._cache = None
        return

    try:
        from google.genai import types as gemini_types

        system_instruction = runtime._build_system_instruction()
        cache_contents = (
            f"Rules:\n{runtime.rules_context}\n\n"
            f"User Context:\n{runtime.user_context}\n\n"
            f"Memory Summary:\n{runtime.memory_summary}"
        )

        gemini_client = runtime.provider._client
        runtime._cache = gemini_client.caches.create(
            model=runtime._cache_model,
            config=gemini_types.CreateCachedContentConfig(
                contents=[cache_contents],
                system_instruction=system_instruction,
                ttl=f"{runtime._cache_ttl}s",
                display_name="lancelot-cold-memory",
            ),
        )
        _logger.info(
            "context_cache_created",
            extra={
                "cache_name": runtime._cache.name,
                "ttl_s": runtime._cache_ttl,
            },
        )
    except Exception as exc:
        _logger.warning(
            "context_cache_unavailable",
            extra={"error": str(exc)},
        )
        runtime._cache = None


def query_memory(runtime: Any, query_text: str, n_results: int = 3) -> str:
    """Retrieve relevant context from the configured memory collection."""
    if not runtime.memory_collection:
        return ""

    try:
        results = runtime.memory_collection.query(
            query_texts=[query_text],
            n_results=n_results,
        )
        documents = results["documents"][0] if results["documents"] else []
        if not documents:
            return "No relevant past memories found."

        return "\n- ".join(documents)
    except Exception as exc:
        return f"Error retrieving memory: {exc}"


def log_rule_candidate(runtime: Any, content: str) -> None:
    """Append a candidate learned rule for human review."""
    candidate_path = os.path.join(runtime.data_dir, "RULE_CANDIDATES.md")
    try:
        with open(candidate_path, "a") as candidate_file:
            candidate_file.write(f"\n{content}")
        _logger.info("Rule candidate logged for review: %s", content.strip())
    except Exception as exc:
        _logger.warning("Error logging rule candidate: %s", exc)


def update_rules(runtime: Any, new_knowledge: str) -> None:
    """Append validated knowledge to RULES.md and refresh its signature."""
    valid, reason = runtime._validate_rule_content(new_knowledge)
    if not valid:
        _logger.warning("Rule rejected: %s", reason)
        return

    rule_path = os.path.join(runtime.data_dir, "RULES.md")
    try:
        with open(rule_path, "a") as rules_file:
            rules_file.write(f"\n{new_knowledge}")
        runtime.rules_context += f"\n{new_knowledge}"
        _logger.info(
            "Confidence High (>90%%): Updated RULES.md with: %s",
            new_knowledge.strip(),
        )

        hmac_key = os.getenv("LANCELOT_HMAC_KEY", "default-dev-key")
        with open(rule_path, "rb") as rules_file:
            rules_bytes = rules_file.read()
        sig = hmac.new(hmac_key.encode(), rules_bytes, hashlib.sha256).hexdigest()
        sig_path = os.path.join(runtime.data_dir, "RULES.md.sig")
        with open(sig_path, "w") as sig_file:
            sig_file.write(sig)

        runtime._init_context_cache()
    except Exception as exc:
        _logger.warning("Error updating rules: %s", exc)
