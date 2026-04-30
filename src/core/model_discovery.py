"""
ModelDiscovery — dynamic model listing and lane assignment (v8.3.0).

Discovers available models from the active provider's API at startup,
cross-references with the static model_profiles.yaml for cost/capability
data, and auto-assigns models to Lancelot's lanes (fast, deep, cache).

Public API:
    ModelDiscovery(provider, profiles_path)
    .refresh()           → re-query provider API
    .get_stack()         → current model stack for API/UI
    .get_lane_model(lane) → model ID for a specific lane
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from providers.base import ProviderClient, ModelInfo

logger = logging.getLogger(__name__)

# Default profiles path relative to project root
_DEFAULT_PROFILES_PATH = "config/model_profiles.yaml"


def _load_profiles(profiles_path: str = None) -> dict:
    """Load static model profiles from YAML file."""
    path = profiles_path or _DEFAULT_PROFILES_PATH
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return data.get("profiles", {})
    except FileNotFoundError:
        logger.warning("Model profiles not found at %s — using empty profiles", path)
        return {}
    except Exception as e:
        logger.warning("Failed to load model profiles: %s", e)
        return {}


class ModelDiscovery:
    """Discovers available models from the active provider and assigns to lanes."""

    def __init__(
        self,
        provider: ProviderClient,
        profiles_path: str = None,
        lane_overrides: Optional[dict] = None,
        fallback_lanes: Optional[dict] = None,
    ):
        self._provider = provider
        self._profiles = _load_profiles(profiles_path)
        self._lane_overrides = lane_overrides or {}
        self._fallback_lanes = fallback_lanes or {}
        self._discovered: list[ModelInfo] = []
        self._lane_assignments: dict[str, str] = {}
        self._lane_sources: dict[str, str] = {}
        self._last_refresh: Optional[datetime] = None

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    @property
    def discovered_models(self) -> list[ModelInfo]:
        return list(self._discovered)

    @property
    def lane_assignments(self) -> dict[str, str]:
        return dict(self._lane_assignments)

    @property
    def lane_sources(self) -> dict[str, str]:
        return dict(self._lane_sources)

    def refresh(self) -> None:
        """Query provider API for available models and assign to lanes."""
        try:
            self._discovered = self._provider.list_models()
            logger.info(
                "Model discovery: found %d models from %s",
                len(self._discovered),
                self._provider.provider_name,
            )
        except Exception as e:
            logger.warning("Model discovery failed: %s — using lane overrides only", e)
            self._discovered = []

        # Enrich discovered models with profile data
        self._enrich_with_profiles()

        # Auto-assign lanes (overrides take priority)
        self._lane_assignments = self._auto_assign_lanes()
        self._last_refresh = datetime.now(timezone.utc)

        logger.info("Lane assignments: %s", self._lane_assignments)

    def _enrich_with_profiles(self) -> None:
        """Merge static profile data into discovered models."""
        for model in self._discovered:
            profile = self._profiles.get(model.id, {})
            if profile:
                if not model.context_window:
                    model.context_window = profile.get("context_window", 0)
                if not model.input_cost_per_1k:
                    model.input_cost_per_1k = profile.get("cost_input_per_1k", 0.0)
                if not model.output_cost_per_1k:
                    model.output_cost_per_1k = profile.get("cost_output_per_1k", 0.0)
                if profile.get("capability_tier"):
                    model.capability_tier = profile["capability_tier"]
                if profile.get("supports_tools") is not None:
                    model.supports_tools = profile["supports_tools"]

    def _auto_assign_lanes(self) -> dict[str, str]:
        """Assign best model per lane based on capability scoring.

        Rules:
        - fast lane: cheapest model with tool support
        - deep lane: highest-capability model with tool support
        - cache lane: cheapest model (tools not required)

        Explicit lane overrides take priority. Profile lane defaults are used
        only as fallback when discovery cannot assign a lane.
        """
        assignments = {}
        sources = {}

        # Apply overrides first
        for lane in ("fast", "deep", "cache"):
            if lane in self._lane_overrides:
                assignments[lane] = self._lane_overrides[lane]
                sources[lane] = "override"

        if self._discovered:
            # Filter models with tool support
            tool_models = [m for m in self._discovered if m.supports_tools]
            all_models = list(self._discovered)

            # Sort by cost (ascending) for fast/cache selection
            tool_models_by_cost = sorted(
                tool_models,
                key=lambda m: m.output_cost_per_1k or 999.0,
            )
            all_by_cost = sorted(
                all_models,
                key=lambda m: m.output_cost_per_1k or 999.0,
            )

            # Sort by capability for deep selection
            _TIER_RANK = {"fast": 1, "standard": 2, "deep": 3}
            tool_models_by_cap = sorted(
                tool_models,
                key=lambda m: (
                    _TIER_RANK.get(m.capability_tier, 2),
                    _model_version_score(m.id),
                    m.context_window or 0,
                    m.output_cost_per_1k or 0.0,
                ),
                reverse=True,
            )

            # Fast lane: cheapest with tools
            if "fast" not in assignments and tool_models_by_cost:
                assignments["fast"] = tool_models_by_cost[0].id
                sources["fast"] = "auto"

            # Deep lane: highest capability with tools
            if "deep" not in assignments and tool_models_by_cap:
                assignments["deep"] = tool_models_by_cap[0].id
                sources["deep"] = "auto"

            # Cache lane: cheapest overall
            if "cache" not in assignments:
                if all_by_cost:
                    assignments["cache"] = all_by_cost[0].id
                    sources["cache"] = "auto"
                elif "fast" in assignments:
                    assignments["cache"] = assignments["fast"]
                    sources["cache"] = sources.get("fast", "auto")

        for lane in ("fast", "deep", "cache"):
            if lane not in assignments and lane in self._fallback_lanes:
                assignments[lane] = self._fallback_lanes[lane]
                sources[lane] = "fallback"

        self._lane_sources = sources
        if not self._discovered:
            return assignments

        return assignments

    def set_lane_override(self, lane: str, model_id: str) -> None:
        """Override a single lane's model assignment at runtime."""
        self._lane_overrides[lane] = model_id
        self._lane_assignments[lane] = model_id
        self._lane_sources[lane] = "override"
        logger.info("Lane '%s' overridden to %s", lane, model_id)

    def reset_overrides(self) -> None:
        """Clear all lane overrides and re-run auto-assignment."""
        self._lane_overrides.clear()
        self._lane_assignments = self._auto_assign_lanes()
        logger.info("Lane overrides cleared — auto-assigned: %s", self._lane_assignments)

    def replace_provider(
        self,
        new_provider: ProviderClient,
        lane_overrides: Optional[dict] = None,
        fallback_lanes: Optional[dict] = None,
    ) -> None:
        """Hot-swap the underlying provider and re-run discovery."""
        self._provider = new_provider
        self._lane_overrides = lane_overrides or {}
        if fallback_lanes is not None:
            self._fallback_lanes = fallback_lanes or {}
        self.refresh()

    def get_lane_model(self, lane: str) -> Optional[str]:
        """Get the model ID assigned to a specific lane."""
        return self._lane_assignments.get(lane)

    def get_model_profile(self, model_id: str) -> dict:
        """Get enriched profile data for a specific model."""
        # Check discovered models first
        for m in self._discovered:
            if m.id == model_id:
                return {
                    "id": m.id,
                    "display_name": m.display_name,
                    "context_window": m.context_window,
                    "supports_tools": m.supports_tools,
                    "capability_tier": m.capability_tier,
                    "cost_input_per_1k": m.input_cost_per_1k,
                    "cost_output_per_1k": m.output_cost_per_1k,
                }
        # Fall back to static profiles
        profile = self._profiles.get(model_id, {})
        if profile:
            return {
                "id": model_id,
                "display_name": model_id,
                "context_window": profile.get("context_window", 0),
                "supports_tools": profile.get("supports_tools", False),
                "capability_tier": profile.get("capability_tier", "standard"),
                "cost_input_per_1k": profile.get("cost_input_per_1k", 0.0),
                "cost_output_per_1k": profile.get("cost_output_per_1k", 0.0),
            }
        return {"id": model_id, "display_name": model_id}

    def get_stack(self) -> dict:
        """Return current model stack for the API/UI."""
        lane_details = {}
        for lane, model_id in self._lane_assignments.items():
            profile = self.get_model_profile(model_id)
            lane_details[lane] = {
                "model": model_id,
                "display_name": profile.get("display_name", model_id),
                "context_window": profile.get("context_window", 0),
                "cost_output_per_1k": profile.get("cost_output_per_1k", 0.0),
                "supports_tools": profile.get("supports_tools", False),
                "source": self._lane_sources.get(lane, "auto"),
            }

        return {
            "provider": self._provider.provider_name,
            "provider_display_name": self._provider.provider_name.title(),
            "lanes": lane_details,
            "discovered_models": [
                {
                    "id": m.id,
                    "display_name": m.display_name,
                    "context_window": m.context_window,
                    "supports_tools": m.supports_tools,
                    "capability_tier": m.capability_tier,
                    "cost_input_per_1k": m.input_cost_per_1k,
                    "cost_output_per_1k": m.output_cost_per_1k,
                }
                for m in self._discovered
            ],
            "models_count": len(self._discovered),
            "last_refresh": (
                self._last_refresh.isoformat() if self._last_refresh else None
            ),
            "lane_sources": dict(self._lane_sources),
        }


def _model_version_score(model_id: str) -> tuple[int, ...]:
    """Best-effort recency score for model IDs that encode versions."""
    model = model_id.lower()
    match = re.search(r"gpt[-_]?(\d+(?:\.\d+){0,2})", model)
    if not match:
        match = re.search(r"gemini[-_](\d+(?:\.\d+){0,2})", model)
    if not match:
        match = re.search(r"grok[-_](\d+(?:\.\d+){0,2})", model)
    if not match:
        match = re.search(r"claude[-_](?:opus|sonnet|haiku)[-_](\d+)(?:[-_](\d+))?", model)
        if match:
            return tuple(int(part) for part in match.groups(default="0"))
    if match:
        return tuple(int(part) for part in match.group(1).split("."))
    return (0,)
