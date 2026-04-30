# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Soul Template Registry — loads, validates, and serves Soul templates.

Templates are YAML files in the templates/ directory tree. Each template
contains a valid Soul document plus a `_template_metadata` key with
display info (name, description, industry, version, author). The
metadata key is stripped before Soul validation.

Public API:
    TemplateMetadata       — dataclass for template display info
    SoulTemplate           — validated template with metadata + Soul dict
    load_templates()       → list[SoulTemplate]
    get_template(name)     → SoulTemplate | None
    apply_template(name, customizations, operator_id, session_id) → SoulAmendmentProposal
"""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from src.core.soul.store import Soul, SoulStoreError

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "templates"
)


# ---------------------------------------------------------------------------
# Template Metadata
# ---------------------------------------------------------------------------

class TemplateMetadata(BaseModel):
    """Display metadata stripped from template YAML before Soul validation."""
    name: str = Field(..., description="Unique template identifier (e.g. 'finance-reporting-analyst')")
    display_name: str = Field(..., description="Human-readable name for UI display")
    description: str = Field("", description="What this template is for")
    industry: str = Field("general", description="Industry vertical (e.g. 'finance', 'healthcare')")
    version: str = Field("1.0", description="Template version")
    author: str = Field("Lancelot", description="Template author")
    tags: List[str] = Field(default_factory=list, description="Searchable tags")


# ---------------------------------------------------------------------------
# Soul Template
# ---------------------------------------------------------------------------

@dataclass
class SoulTemplate:
    """A validated Soul template with metadata."""
    metadata: TemplateMetadata
    soul_dict: Dict[str, Any]
    raw_yaml: str
    file_path: str

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def display_name(self) -> str:
        return self.metadata.display_name

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API responses."""
        return {
            "metadata": self.metadata.model_dump(),
            "soul_dict": self.soul_dict,
            "raw_yaml": self.raw_yaml,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_template_cache: Optional[List[SoulTemplate]] = None


def _resolve_templates_dir(templates_dir: Optional[str] = None) -> Path:
    """Resolve the templates directory path."""
    if templates_dir:
        return Path(templates_dir)
    return Path(_DEFAULT_TEMPLATES_DIR).resolve()


def load_templates(
    templates_dir: Optional[str] = None,
    *,
    force_reload: bool = False,
) -> List[SoulTemplate]:
    """Load all templates from the templates directory tree.

    Walks all subdirectories, loads .yaml files, strips _template_metadata,
    validates the remaining dict against the Soul model + linter.

    Args:
        templates_dir: Override path to templates directory.
        force_reload: Bypass cache and reload from disk.

    Returns:
        List of validated SoulTemplate objects.
    """
    global _template_cache
    if _template_cache is not None and not force_reload:
        return _template_cache

    d = _resolve_templates_dir(templates_dir)
    if not d.exists():
        logger.warning("Templates directory not found: %s", d)
        _template_cache = []
        return _template_cache

    templates: List[SoulTemplate] = []

    for yaml_file in sorted(d.rglob("*.yaml")):
        try:
            raw = yaml_file.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                logger.warning("Skipping non-mapping template: %s", yaml_file)
                continue

            # Extract and validate metadata
            meta_dict = data.pop("_template_metadata", None)
            if meta_dict is None:
                logger.warning("Skipping template without _template_metadata: %s", yaml_file)
                continue

            metadata = TemplateMetadata(**meta_dict)

            # Validate soul dict against Pydantic model (no linter yet — linter
            # runs at apply time so template authors get feedback in the proposal)
            soul = Soul(**data)

            # Run linter to verify invariants
            from src.core.soul.linter import lint, LintSeverity
            issues = lint(soul)
            critical = [i for i in issues if i.severity == LintSeverity.CRITICAL]
            if critical:
                details = "; ".join(f"[{i.rule}] {i.message}" for i in critical)
                logger.error(
                    "Template '%s' has critical linter issues, skipping: %s",
                    metadata.name, details,
                )
                continue

            # Build raw_yaml without metadata for proposal use
            soul_only_yaml = yaml.dump(data, default_flow_style=False, sort_keys=False)

            templates.append(SoulTemplate(
                metadata=metadata,
                soul_dict=data,
                raw_yaml=soul_only_yaml,
                file_path=str(yaml_file),
            ))

            logger.info("Loaded template: %s (%s)", metadata.name, yaml_file.name)

        except Exception as exc:
            logger.error("Failed to load template %s: %s", yaml_file, exc)
            continue

    _template_cache = templates
    logger.info("Loaded %d soul templates", len(templates))
    return templates


def get_template(
    name: str,
    templates_dir: Optional[str] = None,
) -> Optional[SoulTemplate]:
    """Get a single template by name.

    Args:
        name: Template name (e.g. 'finance-reporting-analyst').
        templates_dir: Override path to templates directory.

    Returns:
        SoulTemplate if found, None otherwise.
    """
    for t in load_templates(templates_dir):
        if t.name == name:
            return t
    return None


def list_template_metadata(
    templates_dir: Optional[str] = None,
    industry: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List template metadata (lightweight, no full YAML).

    Args:
        templates_dir: Override path.
        industry: Optional filter by industry.

    Returns:
        List of metadata dicts.
    """
    templates = load_templates(templates_dir)
    if industry:
        templates = [t for t in templates if t.metadata.industry == industry]
    return [t.metadata.model_dump() for t in templates]


def invalidate_cache() -> None:
    """Clear the template cache, forcing reload on next access."""
    global _template_cache
    _template_cache = None


# ---------------------------------------------------------------------------
# Template Application — creates a Soul Amendment Proposal
# ---------------------------------------------------------------------------

def apply_template(
    template_name: str,
    customizations: Optional[Dict[str, Any]] = None,
    operator_id: Optional[str] = None,
    session_id: Optional[str] = None,
    soul_dir: Optional[str] = None,
    templates_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a template by creating a Soul Amendment Proposal.

    The template YAML is used as the proposed soul content. Optional
    customizations are deep-merged before proposal creation. The author
    is set to "template:{template_name}" for auditability.

    After the proposal is created and activated, a SOUL_TEMPLATE_APPLIED
    receipt is emitted.

    Args:
        template_name: Template to apply.
        customizations: Optional field overrides (e.g. {"mission": "..."}).
        operator_id: Required — who is applying the template.
        session_id: Session ID for the receipt.
        soul_dir: Path to soul directory.
        templates_dir: Path to templates directory.

    Returns:
        Dict with proposal details.

    Raises:
        SoulStoreError if template not found or validation fails.
    """
    template = get_template(template_name, templates_dir)
    if template is None:
        raise SoulStoreError(f"Template not found: {template_name}")

    # Deep copy and apply customizations
    soul_dict = copy.deepcopy(template.soul_dict)
    if customizations:
        _deep_merge(soul_dict, customizations)

    # Re-validate after customizations
    try:
        soul = Soul(**soul_dict)
    except Exception as exc:
        raise SoulStoreError(
            f"Template '{template_name}' with customizations failed validation: {exc}"
        ) from exc

    # Run linter on customized soul
    from src.core.soul.linter import lint_or_raise
    lint_or_raise(soul)

    # Generate YAML for the proposal
    proposed_yaml = yaml.dump(soul_dict, default_flow_style=False, sort_keys=False)

    # Create proposal via the amendment workflow
    from src.core.soul.store import get_active_version
    from src.core.soul.amendments import create_proposal

    current_version = get_active_version(soul_dir)
    proposal = create_proposal(
        from_version=current_version,
        proposed_yaml_text=proposed_yaml,
        author=f"template:{template_name}",
        soul_dir=soul_dir,
    )

    fields_customized = list(customizations.keys()) if customizations else []

    return {
        "proposal_id": proposal.id,
        "proposed_version": proposal.proposed_version,
        "diff_summary": proposal.diff_summary,
        "template_name": template_name,
        "template_version": template.metadata.version,
        "fields_customized": fields_customized,
        "status": proposal.status.value,
    }


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> None:
    """Deep merge overrides into base dict, in place."""
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
