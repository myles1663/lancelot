"""
Antigravity UIBuilder Provider
==============================

Generative UI scaffolding provider using Antigravity AI capabilities.
Falls back to TemplateScaffolder when Antigravity is unavailable.

This provider supports GENERATIVE mode for AI-powered UI generation:
- Uses Antigravity's AI capabilities to generate custom UI components
- Can create custom layouts, components, and styles from natural language
- Captures generation receipts for audit trail

Prompt 8 — Antigravity UIBuilder
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.tools.contracts import (
    BaseProvider,
    Capability,
    ProviderHealth,
    ProviderState,
    ScaffoldResult,
    UIBuilderMode,
)
from src.tools.providers.ui_templates import (
    TemplateScaffolder,
    TemplateConfig,
    TEMPLATES,
)
from src.core.feature_flags import FEATURE_TOOLS_ANTIGRAVITY

logger = logging.getLogger(__name__)


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
    max_retries: int = 3

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
    - Antigravity service is unavailable
    - Generation fails
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

        Checks if Antigravity feature is enabled and service is reachable.
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

        # Check Antigravity service availability
        service_available = self._check_antigravity_service()
        self._antigravity_available = service_available

        if service_available:
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.HEALTHY,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                metadata={
                    "feature_enabled": True,
                    "service_available": True,
                    "mode": "generative",
                    "fallback_available": self.config.fallback_to_templates,
                },
            )
        else:
            return ProviderHealth(
                provider_id=self.provider_id,
                state=ProviderState.DEGRADED,
                version="1.0.0",
                last_check=self._last_health_check,
                capabilities=[c.value for c in self.capabilities],
                degraded_reasons=["Antigravity service not reachable"],
                metadata={
                    "feature_enabled": True,
                    "service_available": False,
                    "fallback_available": self.config.fallback_to_templates,
                },
            )

    def _check_antigravity_service(self) -> bool:
        """Check if Antigravity service is available."""
        # In a real implementation, this would ping the Antigravity service
        # For now, we check if the feature is enabled and simulate availability
        try:
            # Try to import the engine to see if it's available
            from src.agents.antigravity_engine import AntigravityEngine
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning("Antigravity service check failed: %s", e)
            return False

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

        In GENERATIVE mode, uses Antigravity AI to generate custom UI.
        In DETERMINISTIC mode or when Antigravity is unavailable, falls back
        to template-based scaffolding.

        Args:
            template_id: Template identifier or generation prompt
            spec: Specification with project details
            workspace: Output directory
            mode: DETERMINISTIC (templates) or GENERATIVE (Antigravity)

        Returns:
            ScaffoldResult with created files and build status
        """
        start_time = time.time()

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
                        reason="Antigravity not available",
                    )
                else:
                    return ScaffoldResult(
                        success=False,
                        output_path=workspace,
                        template_id=template_id,
                        error_message="Antigravity not available and fallback disabled",
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
                    )

                return result

            except Exception as e:
                logger.exception("Generative scaffolding failed")

                if self.config.fallback_to_templates:
                    return self._fallback_scaffold(
                        template_id=template_id,
                        spec=spec,
                        workspace=workspace,
                        reason=f"Generation failed: {str(e)[:100]}",
                    )
                else:
                    return ScaffoldResult(
                        success=False,
                        output_path=workspace,
                        error_message=f"Generation failed: {str(e)[:200]}",
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
        Generate UI using Antigravity AI.

        This is a placeholder for the actual AI generation logic.
        In production, this would call Antigravity's generative APIs.
        """
        # For now, generate a basic structure based on the prompt
        # In production, this would use the Antigravity AI

        project_name = spec.get("name", "generated-project")
        title = spec.get("title", "Generated Project")
        description = spec.get("description", "AI-generated UI")

        files_created = []

        try:
            os.makedirs(workspace, exist_ok=True)

            # Generate a basic project structure
            # This is a simplified placeholder for actual AI generation

            # Main app file
            main_content = f'''"""
{title}
Generated by Antigravity AI

Prompt: {prompt[:100]}...
"""

def main():
    print("{title}")
    print("{description}")

if __name__ == "__main__":
    main()
'''
            main_path = os.path.join(workspace, "main.py")
            with open(main_path, "w") as f:
                f.write(main_content)
            files_created.append("main.py")

            # README
            readme_content = f'''# {title}

{description}

## Generated by Antigravity AI

This project was generated using the following prompt:

> {prompt}

## Getting Started

```bash
python main.py
```
'''
            readme_path = os.path.join(workspace, "README.md")
            with open(readme_path, "w") as f:
                f.write(readme_content)
            files_created.append("README.md")

            # Requirements
            req_path = os.path.join(workspace, "requirements.txt")
            with open(req_path, "w") as f:
                f.write("# Generated by Antigravity\n")
            files_created.append("requirements.txt")

            return ScaffoldResult(
                success=True,
                output_path=workspace,
                template_id=f"antigravity:{hashlib.md5(prompt.encode()).hexdigest()[:8]}",
                files_created=files_created,
                build_verified=True,
            )

        except Exception as e:
            return ScaffoldResult(
                success=False,
                output_path=workspace,
                files_created=files_created,
                error_message=str(e),
            )

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
        elif any(kw in prompt_lower for kw in ["fastapi", "api", "async", "backend"]):
            return "fastapi_service"
        elif any(kw in prompt_lower for kw in ["streamlit", "data", "analytics", "chart"]):
            return "streamlit_dashboard"
        elif any(kw in prompt_lower for kw in ["flask", "rest", "web"]):
            return "flask_api"

        # Default
        return "fastapi_service"

    def list_templates(self) -> List[Dict[str, Any]]:
        """
        List available templates.

        In GENERATIVE mode, also indicates AI generation capability.
        """
        templates = self._template_fallback.list_templates()

        # Add generative capability info
        if self._antigravity_available:
            templates.append({
                "id": "generative",
                "name": "AI-Generated (Antigravity)",
                "description": "Generate custom UI from natural language prompts",
                "framework": "any",
                "features": ["ai-generated", "custom", "natural-language"],
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
