"""
Antigravity UIBuilder Provider
==============================

Generative UI scaffolding provider backed by the active flagship model.
Falls back to TemplateScaffolder when generative backends are unavailable
or generation fails.

This provider supports GENERATIVE mode for real AI-powered UI generation:
- Uses the shared flagship provider stack (Gemini/OpenAI/Anthropic/xAI/NVIDIA)
- Produces a JSON file manifest from model output and writes validated files
- Captures generation receipts for audit trail visibility

"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from src.core.feature_flags import FEATURE_TOOLS_ANTIGRAVITY
from src.core.flagship_client import FlagshipClient, FlagshipError
from src.core.provider_profile import ConfigError, ProfileRegistry
from src.tools.contracts import (
    BaseProvider,
    Capability,
    ProviderHealth,
    ProviderState,
    ScaffoldResult,
    UIBuilderMode,
)
from src.tools.providers.ui_templates import (
    TEMPLATES,
    TemplateConfig,
    TemplateScaffolder,
)

logger = logging.getLogger(__name__)

_SUPPORTED_GENERATION_PROVIDERS = ("gemini", "openai", "anthropic", "xai", "nvidia")
_PROVIDER_ALIASES = {"openai-codex": "openai"}
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<payload>\{.*?\})\s*```", re.DOTALL)


@dataclass
class GenerationBackend:
    """Resolved flagship model backend used for UI generation."""

    provider_name: str
    model_name: str
    lane: str
    client: FlagshipClient


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class AntigravityUIConfig:
    """Configuration for AntigravityUIProvider."""

    # Feature control
    enabled: bool = True

    # Generation settings
    generation_timeout_s: int = 120
    generation_lane: str = "fast"
    preferred_provider: Optional[str] = None
    max_generated_files: int = 24
    max_file_bytes: int = 200_000

    # Fallback settings
    fallback_to_templates: bool = True

    # Receipt settings
    emit_generation_receipts: bool = True

    # Template fallback config
    template_config: Optional[TemplateConfig] = None


# =============================================================================
# Generation Receipt
# =============================================================================


@dataclass
class GenerationReceipt:
    """Receipt for an AI generation operation."""

    receipt_id: str
    timestamp: str
    mode: str
    prompt_hash: str
    spec_hash: str
    output_path: str
    files_generated: List[str]
    generation_time_ms: int
    success: bool
    error_message: Optional[str] = None
    fallback_used: bool = False
    fallback_template: Optional[str] = None
    provider_name: Optional[str] = None
    model_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "receipt_id": self.receipt_id,
            "timestamp": self.timestamp,
            "mode": self.mode,
            "prompt_hash": self.prompt_hash,
            "spec_hash": self.spec_hash,
            "output_path": self.output_path,
            "files_generated": self.files_generated,
            "generation_time_ms": self.generation_time_ms,
            "success": self.success,
            "error_message": self.error_message,
            "fallback_used": self.fallback_used,
            "fallback_template": self.fallback_template,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
        }


# =============================================================================
# AntigravityUIProvider
# =============================================================================


class AntigravityUIProvider(BaseProvider):
    """
    Antigravity-powered generative UI scaffolding provider.

    Provides GENERATIVE mode for AI-powered UI generation.
    Falls back to template-based scaffolding when:
    - Antigravity feature flag is disabled
    - No configured flagship generation backend is available
    - Generation fails or produces invalid output
    - DETERMINISTIC mode is requested
    """

    def __init__(self, config: Optional[AntigravityUIConfig] = None):
        """
        Initialize the AntigravityUIProvider.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self.config = config or AntigravityUIConfig()
        self._last_health_check: Optional[str] = None
        self._antigravity_available: Optional[bool] = None
        self._profile_registry: Optional[ProfileRegistry] = None
        self._generation_backend: Optional[GenerationBackend] = None
        self._last_generation_backend: Optional[GenerationBackend] = None

        # Initialize template fallback
        self._template_fallback = TemplateScaffolder(
            config=self.config.template_config
        )

        # Generation receipts
        self._receipts: List[GenerationReceipt] = []

    @property
    def provider_id(self) -> str:
        """Unique provider identifier."""
        return "ui_antigravity"

    @property
    def capabilities(self) -> List[Capability]:
        """List of capabilities this provider implements."""
        return [Capability.UI_BUILDER]

    # =========================================================================
    # Health Check
    # =========================================================================

    def health_check(self) -> ProviderHealth:
        """
        Check provider health.

        Checks if Antigravity is enabled and a flagship generation backend is
        configured for live model-backed scaffolding.
        """
        self._last_health_check = datetime.now(timezone.utc).isoformat()

        # Check feature flag
        if not FEATURE_TOOLS_ANTIGRAVITY:
            self._antigravity_available = False
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.OFFLINE,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                error_message="FEATURE_TOOLS_ANTIGRAVITY is disabled",
                metadata={
                    "feature_enabled": False,
                    "fallback_available": self.config.fallback_to_templates,
                },
            )

        # Check if config disables the provider
        if not self.config.enabled:
            self._antigravity_available = False
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.OFFLINE,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                error_message="Provider is disabled in configuration",
                metadata={
                    "feature_enabled": True,
                    "config_enabled": False,
                    "fallback_available": self.config.fallback_to_templates,
                },
            )

        # Check live generation backend availability
        service_available = self._check_antigravity_service()
        self._antigravity_available = service_available

        metadata = {
            "feature_enabled": True,
            "generation_lane": self.config.generation_lane,
            "fallback_available": self.config.fallback_to_templates,
            "providers_considered": self._provider_candidates(),
        }

        if service_available and self._generation_backend:
            metadata.update({
                "service_available": True,
                "mode": "generative",
                "generation_provider": self._generation_backend.provider_name,
                "generation_model": self._generation_backend.model_name,
            })
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.HEALTHY,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                metadata=metadata,
            )

        metadata.update({
            "service_available": False,
            "mode": "fallback-only",
        })
        return ProviderHealth(
            provider_id=self.provider_id,
            state=ProviderState.DEGRADED,
            version="1.0.0",
            last_check=self._last_health_check,
            capabilities=[c.value for c in self.capabilities],
            degraded_reasons=["No configured flagship generation backend"],
            metadata=metadata,
        )

    def _check_antigravity_service(self) -> bool:
        """Check if a live Antigravity generation backend is available."""
        return self._resolve_generation_backend(refresh=True) is not None

    def _resolve_generation_backend(
        self,
        refresh: bool = False,
    ) -> Optional[GenerationBackend]:
        """Resolve the configured flagship backend used for UI generation."""
        if self._generation_backend is not None and not refresh:
            return self._generation_backend

        self._generation_backend = None
        registry = self._get_profile_registry()
        if registry is None:
            return None

        for provider_name in self._provider_candidates():
            if not registry.has_provider(provider_name):
                continue

            try:
                profile = registry.get_profile(provider_name)
            except ConfigError as exc:
                logger.warning("Skipping invalid provider profile %s: %s", provider_name, exc)
                continue

            client = FlagshipClient(provider_name, profile)
            if client.is_configured():
                lane_config = getattr(profile, self.config.generation_lane)
                self._generation_backend = GenerationBackend(
                    provider_name=provider_name,
                    model_name=lane_config.model,
                    lane=self.config.generation_lane,
                    client=client,
                )
                return self._generation_backend

        return None

    def _get_profile_registry(self) -> Optional[ProfileRegistry]:
        """Load the shared provider profile registry lazily."""
        if self._profile_registry is not None:
            return self._profile_registry

        try:
            self._profile_registry = ProfileRegistry()
        except (ConfigError, FileNotFoundError) as exc:
            logger.warning("Unable to load provider profiles for UI generation: %s", exc)
            self._profile_registry = None

        return self._profile_registry

    def _provider_candidates(self) -> List[str]:
        """Return provider candidates in selection order."""
        preferred = self.config.preferred_provider or os.getenv("LANCELOT_PROVIDER", "")
        preferred = _PROVIDER_ALIASES.get(preferred.lower(), preferred.lower())

        ordered: List[str] = []
        if preferred in _SUPPORTED_GENERATION_PROVIDERS:
            ordered.append(preferred)
        for provider_name in _SUPPORTED_GENERATION_PROVIDERS:
            if provider_name not in ordered:
                ordered.append(provider_name)
        return ordered

    # =========================================================================
    # UIBuilder Capability
    # =========================================================================

    def scaffold(
        self,
        template_id: str,
        spec: Dict[str, Any],
        workspace: str,
        mode: UIBuilderMode = UIBuilderMode.DETERMINISTIC,
    ) -> ScaffoldResult:
        """
        Scaffold a UI project.

        In GENERATIVE mode, uses the configured flagship backend to generate
        a project manifest and scaffold files. In DETERMINISTIC mode or when
        generation is unavailable, falls back to template-based scaffolding.
        """
        start_time = time.time()
        self._last_generation_backend = None

        # DETERMINISTIC mode always uses templates
        if mode == UIBuilderMode.DETERMINISTIC:
            return self._fallback_scaffold(
                template_id=template_id,
                spec=spec,
                workspace=workspace,
                reason="DETERMINISTIC mode requested",
            )

        # GENERATIVE mode attempts Antigravity
        if mode == UIBuilderMode.GENERATIVE:
            # Check availability
            if self._antigravity_available is None:
                self.health_check()

            if not self._antigravity_available:
                if self.config.fallback_to_templates:
                    return self._fallback_scaffold(
                        template_id=template_id,
                        spec=spec,
                        workspace=workspace,
                        reason="Antigravity generation backend not available",
                    )
                return ScaffoldResult(
                    success=False,
                    output_path=workspace,
                    template_id=template_id,
                    error_message="Antigravity generation backend not available and fallback disabled",
                )

            # Attempt generative scaffolding
            try:
                result = self._generative_scaffold(
                    prompt=template_id,
                    spec=spec,
                    workspace=workspace,
                )

                # Create receipt
                if self.config.emit_generation_receipts:
                    self._create_receipt(
                        mode="generative",
                        prompt=template_id,
                        spec=spec,
                        workspace=workspace,
                        files=result.files_created,
                        duration_ms=int((time.time() - start_time) * 1000),
                        success=result.success,
                        error=result.error_message,
                        provider_name=(
                            self._last_generation_backend.provider_name
                            if self._last_generation_backend
                            else None
                        ),
                        model_name=(
                            self._last_generation_backend.model_name
                            if self._last_generation_backend
                            else None
                        ),
                    )

                return result

            except Exception as exc:
                logger.exception("Generative scaffolding failed")

                if self.config.fallback_to_templates:
                    return self._fallback_scaffold(
                        template_id=template_id,
                        spec=spec,
                        workspace=workspace,
                        reason=f"Generation failed: {str(exc)[:100]}",
                    )
                return ScaffoldResult(
                    success=False,
                    output_path=workspace,
                    error_message=f"Generation failed: {str(exc)[:200]}",
                )

        # Unknown mode
        return ScaffoldResult(
            success=False,
            output_path=workspace,
            error_message=f"Unknown mode: {mode}",
        )

    def _generative_scaffold(
        self,
        prompt: str,
        spec: Dict[str, Any],
        workspace: str,
    ) -> ScaffoldResult:
        """
        Generate a scaffold by asking the active flagship model for a file map.

        The model must return a JSON object with a validated list of relative
        file paths and file contents. Generated files are written into a temp
        workspace first so invalid or partial output cannot pollute the target.
        """
        backend = self._resolve_generation_backend()
        if backend is None:
            raise RuntimeError("No configured flagship provider available for Antigravity generation")

        self._last_generation_backend = backend
        payload = self._request_generation_payload(backend, prompt, spec)

        staging_root = tempfile.mkdtemp(prefix="ui_antigravity_")
        try:
            files_created = self._write_generated_files(payload["files"], staging_root)
            build_verified = self.verify_build(staging_root)
            if not build_verified:
                raise RuntimeError("Generated scaffold failed build verification")

            self._copy_generated_tree(staging_root, workspace)
            template_id = payload.get("template_id")
            if not isinstance(template_id, str) or not template_id.strip():
                template_id = (
                    f"antigravity:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:8]}"
                )

            return ScaffoldResult(
                success=True,
                output_path=workspace,
                template_id=template_id,
                files_created=files_created,
                build_verified=True,
            )
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def _request_generation_payload(
        self,
        backend: GenerationBackend,
        prompt: str,
        spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Request and validate a file manifest from the model backend."""
        generation_prompt = self._build_generation_prompt(prompt, spec)

        try:
            raw_response = backend.client.complete(
                generation_prompt,
                lane=backend.lane,
                timeout=float(self.config.generation_timeout_s),
            )
        except FlagshipError as exc:
            raise RuntimeError(f"Generation backend request failed: {exc}") from exc

        payload = self._parse_generation_response(raw_response)
        return self._validate_generation_payload(payload)

    def _build_generation_prompt(self, prompt: str, spec: Dict[str, Any]) -> str:
        """Build a strict JSON-only generation prompt for the active model."""
        spec_json = json.dumps(spec, indent=2, sort_keys=True)
        return f"""You are generating a small but real project scaffold for Lancelot Tool Fabric.

Return JSON only. Do not include markdown fences, prose, or commentary.

Required JSON shape:
{{
  "template_id": "antigravity:<short-id>",
  "project_summary": "one sentence",
  "files": [
    {{
      "path": "relative/path/to/file.ext",
      "content": "full file contents"
    }}
  ]
}}

Hard rules:
- Return between 2 and {self.config.max_generated_files} files.
- Use only relative paths. Never use absolute paths, drive letters, or "..".
- Produce only UTF-8 text files. No binaries, no base64 blobs.
- Do not include secrets, API keys, tokens, passwords, or environment values.
- Prefer a runnable minimal scaffold over a large incomplete one.
- If the spec suggests Python, include a real entrypoint such as main.py or app.py.
- If the spec suggests a web app, include the minimal package.json and source files needed.
- Keep each file under {self.config.max_file_bytes} bytes.

Generation request:
{prompt}

Project spec:
{spec_json}
"""

    def _parse_generation_response(self, raw_response: str) -> Dict[str, Any]:
        """Parse the model response into a JSON object."""
        candidates: List[str] = []
        stripped = raw_response.strip()
        if stripped:
            candidates.append(stripped)

        for match in _JSON_FENCE_RE.finditer(raw_response):
            candidates.append(match.group("payload").strip())

        start = raw_response.find("{")
        end = raw_response.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(raw_response[start:end + 1].strip())

        seen = set()
        parse_errors: List[str] = []
        for candidate in candidates:
            if candidate in seen or not candidate:
                continue
            seen.add(candidate)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError as exc:
                parse_errors.append(str(exc))
                continue

            if isinstance(parsed, dict):
                return parsed
            parse_errors.append("top-level response was not a JSON object")

        raise ValueError(
            "Generation response was not valid JSON: "
            + "; ".join(parse_errors[:3] or ["no JSON object found"])
        )

    def _validate_generation_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize the generation payload."""
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("Generation response did not include any files")
        if len(files) > self.config.max_generated_files:
            raise ValueError(
                f"Generation response included too many files ({len(files)} > "
                f"{self.config.max_generated_files})"
            )

        normalized_files: List[Dict[str, str]] = []
        seen_paths = set()
        for index, file_entry in enumerate(files):
            if not isinstance(file_entry, dict):
                raise ValueError(f"File entry {index} was not an object")

            normalized_path = self._normalize_relative_path(file_entry.get("path"))
            content = file_entry.get("content")
            if not isinstance(content, str):
                raise ValueError(f"File entry {normalized_path} missing string content")

            content_size = len(content.encode("utf-8"))
            if content_size > self.config.max_file_bytes:
                raise ValueError(
                    f"File {normalized_path} exceeded size limit "
                    f"({content_size} > {self.config.max_file_bytes})"
                )
            if normalized_path in seen_paths:
                raise ValueError(f"Duplicate generated file path: {normalized_path}")

            normalized_files.append({
                "path": normalized_path,
                "content": content,
            })
            seen_paths.add(normalized_path)

        payload["files"] = normalized_files
        return payload

    def _normalize_relative_path(self, raw_path: Any) -> str:
        """Validate and normalize a relative file path from the model output."""
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Generated file path must be a non-empty string")

        candidate = raw_path.strip().replace("\\", "/")
        if re.match(r"^[A-Za-z]:", candidate):
            raise ValueError(f"Drive-qualified path not allowed: {raw_path}")

        pure_path = PurePosixPath(candidate)
        if pure_path.is_absolute():
            raise ValueError(f"Absolute path not allowed: {raw_path}")
        if any(part == ".." for part in pure_path.parts):
            raise ValueError(f"Path traversal not allowed: {raw_path}")

        normalized = "/".join(part for part in pure_path.parts if part not in ("", "."))
        if not normalized:
            raise ValueError(f"Invalid generated path: {raw_path}")
        return normalized

    def _write_generated_files(
        self,
        files: List[Dict[str, str]],
        workspace: str,
    ) -> List[str]:
        """Write validated generated files into a staging workspace."""
        os.makedirs(workspace, exist_ok=True)
        files_created: List[str] = []

        for file_entry in files:
            rel_path = file_entry["path"]
            full_path = os.path.join(workspace, *rel_path.split("/"))
            os.makedirs(os.path.dirname(full_path) or workspace, exist_ok=True)

            with open(full_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(file_entry["content"])

            files_created.append(rel_path)

        return files_created

    def _copy_generated_tree(self, source_root: str, dest_root: str) -> None:
        """Copy a validated generated tree into the target workspace."""
        os.makedirs(dest_root, exist_ok=True)

        for current_root, _, files in os.walk(source_root):
            rel_root = os.path.relpath(current_root, source_root)
            for file_name in files:
                source_path = os.path.join(current_root, file_name)
                if rel_root == ".":
                    rel_path = file_name
                else:
                    rel_path = os.path.join(rel_root, file_name)

                dest_path = os.path.join(dest_root, rel_path)
                os.makedirs(os.path.dirname(dest_path) or dest_root, exist_ok=True)
                shutil.copy2(source_path, dest_path)

    def _fallback_scaffold(
        self,
        template_id: str,
        spec: Dict[str, Any],
        workspace: str,
        reason: str,
    ) -> ScaffoldResult:
        """
        Fall back to template-based scaffolding.

        Logs the fallback reason and delegates to TemplateScaffolder.
        """
        logger.info("Falling back to templates: %s", reason)

        # Map generative prompts to closest template
        mapped_template = self._map_to_template(template_id)

        result = self._template_fallback.scaffold(
            template_id=mapped_template,
            spec=spec,
            workspace=workspace,
            mode=UIBuilderMode.DETERMINISTIC,
        )

        # Create fallback receipt
        if self.config.emit_generation_receipts:
            self._create_receipt(
                mode="fallback",
                prompt=template_id,
                spec=spec,
                workspace=workspace,
                files=result.files_created,
                duration_ms=0,
                success=result.success,
                error=result.error_message,
                fallback_template=mapped_template,
            )

        return result

    def _map_to_template(self, prompt_or_id: str) -> str:
        """
        Map a generative prompt to the closest matching template.

        Uses keyword matching to find the best template.
        """
        prompt_lower = prompt_or_id.lower()

        # Direct template ID
        if prompt_or_id in TEMPLATES:
            return prompt_or_id

        # Keyword mapping
        if any(kw in prompt_lower for kw in ["next", "react", "dashboard", "admin"]):
            return "nextjs_shadcn_dashboard"
        if any(kw in prompt_lower for kw in ["fastapi", "api", "async", "backend"]):
            return "fastapi_service"
        if any(kw in prompt_lower for kw in ["streamlit", "data", "analytics", "chart"]):
            return "streamlit_dashboard"
        if any(kw in prompt_lower for kw in ["flask", "rest", "web"]):
            return "flask_api"

        # Default
        return "fastapi_service"

    def list_templates(self) -> List[Dict[str, Any]]:
        """
        List available templates.

        In GENERATIVE mode, also indicates live model-backed generation
        capability when a backend is configured.
        """
        templates = self._template_fallback.list_templates()

        # Add generative capability info
        if self._antigravity_available:
            templates.append({
                "id": "generative",
                "name": "AI-Generated (Antigravity)",
                "description": "Generate custom UI from natural language prompts using the active flagship model",
                "framework": "any",
                "features": ["ai-generated", "custom", "natural-language", "model-backed"],
            })

        return templates

    def verify_build(self, workspace: str) -> bool:
        """Verify the scaffolded project builds successfully."""
        return self._template_fallback.verify_build(workspace)

    # =========================================================================
    # Receipt Management
    # =========================================================================

    def _create_receipt(
        self,
        mode: str,
        prompt: str,
        spec: Dict[str, Any],
        workspace: str,
        files: List[str],
        duration_ms: int,
        success: bool,
        error: Optional[str] = None,
        fallback_template: Optional[str] = None,
        provider_name: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> GenerationReceipt:
        """Create and store a generation receipt."""
        import uuid

        receipt = GenerationReceipt(
            receipt_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            mode=mode,
            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
            spec_hash=hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16],
            output_path=workspace,
            files_generated=files,
            generation_time_ms=duration_ms,
            success=success,
            error_message=error,
            fallback_used=mode == "fallback",
            fallback_template=fallback_template,
            provider_name=provider_name,
            model_name=model_name,
        )

        self._receipts.append(receipt)
        logger.debug("Generation receipt created: %s", receipt.receipt_id)

        return receipt

    def get_receipts(self) -> List[Dict[str, Any]]:
        """Get all generation receipts."""
        return [r.to_dict() for r in self._receipts]

    def clear_receipts(self) -> None:
        """Clear stored receipts."""
        self._receipts = []


# =============================================================================
# Factory Function
# =============================================================================


def create_antigravity_ui_provider(
    enabled: bool = True,
    fallback_to_templates: bool = True,
    emit_receipts: bool = True,
) -> AntigravityUIProvider:
    """
    Factory function for creating AntigravityUIProvider.

    Args:
        enabled: Whether Antigravity generation is enabled
        fallback_to_templates: Whether to fall back to templates on failure
        emit_receipts: Whether to emit generation receipts

    Returns:
        Configured AntigravityUIProvider
    """
    config = AntigravityUIConfig(
        enabled=enabled,
        fallback_to_templates=fallback_to_templates,
        emit_generation_receipts=emit_receipts,
    )
    return AntigravityUIProvider(config=config)
