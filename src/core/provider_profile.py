"""
ProviderProfile registry & runtime config loader (Prompt 14).

Single-owner module for loading models.yaml / router.yaml and providing
typed access to provider profiles, lane configurations, and routing rules.

Public API:
    LaneConfig, ProviderProfile, LocalConfig, RoutingLane, EscalationTrigger
    load_models_config(path=None)   → dict
    load_router_config(path=None)   → dict
    ProfileRegistry(models_path=None, router_path=None)
"""

import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "config"


class ConfigError(Exception):
    """Raised when a configuration file is invalid or missing."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LaneConfig:
    """Configuration for a single routing lane (fast / deep / cache)."""
    model: str
    max_tokens: int
    temperature: float
    thinking: Optional[dict] = None  # Extended thinking config: {"enabled": True, "budget_tokens": N}


@dataclass(frozen=True)
class ProviderProfile:
    """A flagship provider's lane configuration."""
    name: str
    display_name: str
    fast: LaneConfig
    deep: LaneConfig
    cache: Optional[LaneConfig] = None
    mode: str = "sdk"  # "sdk" (full SDK features) or "api" (raw HTTP)


@dataclass(frozen=True)
class LocalConfig:
    """Local utility model configuration."""
    enabled: bool
    url: str


@dataclass(frozen=True)
class RoutingLane:
    """A single entry in the routing order."""
    lane: str
    priority: int
    description: str
    enabled: bool = True


@dataclass(frozen=True)
class EscalationTrigger:
    """A trigger for fast → deep lane escalation."""
    type: str
    description: str


@dataclass(frozen=True)
class ReceiptsConfig:
    """Receipt generation configuration."""
    enabled: bool = True
    include_rationale: bool = True
    include_timing: bool = True


# ---------------------------------------------------------------------------
# Config file loaders
# ---------------------------------------------------------------------------

def load_models_config(path: Optional[str] = None) -> dict:
    """Load and validate models.yaml.

    Args:
        path: Optional override path. Defaults to config/models.yaml.

    Returns:
        Parsed and validated config dict.

    Raises:
        ConfigError on missing file, bad YAML, or schema violation.
    """
    config_path = pathlib.Path(path) if path else _CONFIG_DIR / "models.yaml"

    if not config_path.exists():
        raise ConfigError(f"Models config not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in models config: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Models config must be a YAML mapping")

    _validate_models_config(data)
    return data


def load_router_config(path: Optional[str] = None) -> dict:
    """Load and validate router.yaml.

    Args:
        path: Optional override path. Defaults to config/router.yaml.

    Returns:
        Parsed and validated config dict.

    Raises:
        ConfigError on missing file, bad YAML, or schema violation.
    """
    config_path = pathlib.Path(path) if path else _CONFIG_DIR / "router.yaml"

    if not config_path.exists():
        raise ConfigError(f"Router config not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in router config: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Router config must be a YAML mapping")

    _validate_router_config(data)
    return data


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_lane(lane_data: dict, lane_name: str, provider: str) -> None:
    """Validate a single lane config dict."""
    required = ("model", "max_tokens", "temperature")
    for key in required:
        if key not in lane_data:
            raise ConfigError(
                f"Provider '{provider}' lane '{lane_name}' missing '{key}'"
            )
    if not isinstance(lane_data["model"], str) or not lane_data["model"]:
        raise ConfigError(
            f"Provider '{provider}' lane '{lane_name}': model must be a non-empty string"
        )
    if not isinstance(lane_data["max_tokens"], int) or lane_data["max_tokens"] < 1:
        raise ConfigError(
            f"Provider '{provider}' lane '{lane_name}': max_tokens must be a positive integer"
        )
    if not isinstance(lane_data["temperature"], (int, float)):
        raise ConfigError(
            f"Provider '{provider}' lane '{lane_name}': temperature must be numeric"
        )


def _validate_models_config(data: dict) -> None:
    """Validate the full models config structure."""
    if "version" not in data:
        raise ConfigError("Models config missing 'version'")

    if "local" not in data:
        raise ConfigError("Models config missing 'local' section")
    local = data["local"]
    if "enabled" not in local:
        raise ConfigError("Local config missing 'enabled'")
    if "url" not in local:
        raise ConfigError("Local config missing 'url'")

    if "providers" not in data:
        raise ConfigError("Models config missing 'providers' section")
    providers = data["providers"]
    if not isinstance(providers, dict) or len(providers) == 0:
        raise ConfigError("'providers' must be a non-empty mapping")

    for name, profile in providers.items():
        if not isinstance(profile, dict):
            raise ConfigError(f"Provider '{name}' must be a mapping")
        if "display_name" not in profile:
            raise ConfigError(f"Provider '{name}' missing 'display_name'")
        if "fast" not in profile:
            raise ConfigError(f"Provider '{name}' missing 'fast' lane")
        if "deep" not in profile:
            raise ConfigError(f"Provider '{name}' missing 'deep' lane")

        _validate_lane(profile["fast"], "fast", name)
        _validate_lane(profile["deep"], "deep", name)

        if "cache" in profile:
            _validate_lane(profile["cache"], "cache", name)


def _validate_router_config(data: dict) -> None:
    """Validate the full router config structure."""
    if "version" not in data:
        raise ConfigError("Router config missing 'version'")

    if "routing_order" not in data:
        raise ConfigError("Router config missing 'routing_order'")
    order = data["routing_order"]
    if not isinstance(order, list) or len(order) == 0:
        raise ConfigError("'routing_order' must be a non-empty list")

    for entry in order:
        if not isinstance(entry, dict):
            raise ConfigError("Each routing_order entry must be a mapping")
        for key in ("lane", "priority", "description"):
            if key not in entry:
                raise ConfigError(f"Routing entry missing '{key}'")

    if "escalation" in data:
        esc = data["escalation"]
        if "triggers" not in esc:
            raise ConfigError("Escalation section missing 'triggers'")
        for trigger in esc["triggers"]:
            if "type" not in trigger:
                raise ConfigError("Escalation trigger missing 'type'")


# ---------------------------------------------------------------------------
# ProfileRegistry
# ---------------------------------------------------------------------------

class ProfileRegistry:
    """Registry of provider profiles and routing configuration.

    Loads models.yaml and router.yaml at construction time.
    Provides typed access to profiles, lanes, and routing rules.
    """

    def __init__(
        self,
        models_path: Optional[str] = None,
        router_path: Optional[str] = None,
    ):
        self._models_data = load_models_config(models_path)
        self._router_data = load_router_config(router_path)

        self._profiles: dict[str, ProviderProfile] = {}
        self._local: Optional[LocalConfig] = None
        self._routing_order: list[RoutingLane] = []
        self._escalation_triggers: list[EscalationTrigger] = []
        self._receipts: Optional[ReceiptsConfig] = None
        self._local_tasks: list[str] = []

        self._build()

    def _build(self) -> None:
        """Parse raw config dicts into typed objects."""
        # Local config
        local = self._models_data["local"]
        self._local = LocalConfig(
            enabled=bool(local["enabled"]),
            url=str(local["url"]),
        )

        # Provider profiles
        for name, raw in self._models_data["providers"].items():
            fast = LaneConfig(
                model=raw["fast"]["model"],
                max_tokens=raw["fast"]["max_tokens"],
                temperature=raw["fast"]["temperature"],
                thinking=raw["fast"].get("thinking"),
            )
            deep = LaneConfig(
                model=raw["deep"]["model"],
                max_tokens=raw["deep"]["max_tokens"],
                temperature=raw["deep"]["temperature"],
                thinking=raw["deep"].get("thinking"),
            )
            cache = None
            if "cache" in raw:
                cache = LaneConfig(
                    model=raw["cache"]["model"],
                    max_tokens=raw["cache"]["max_tokens"],
                    temperature=raw["cache"]["temperature"],
                    thinking=raw["cache"].get("thinking"),
                )
            self._profiles[name] = ProviderProfile(
                name=name,
                display_name=raw["display_name"],
                fast=fast,
                deep=deep,
                cache=cache,
                mode=raw.get("mode", "sdk"),
            )

        # Routing order
        for entry in self._router_data["routing_order"]:
            self._routing_order.append(RoutingLane(
                lane=entry["lane"],
                priority=entry["priority"],
                description=entry["description"],
                enabled=entry.get("enabled", True),
            ))
        self._routing_order.sort(key=lambda r: r.priority)

        # Escalation triggers
        if "escalation" in self._router_data:
            for trigger in self._router_data["escalation"]["triggers"]:
                self._escalation_triggers.append(EscalationTrigger(
                    type=trigger["type"],
                    description=trigger.get("description", ""),
                ))

        # Receipts config
        receipts = self._router_data.get("receipts", {})
        self._receipts = ReceiptsConfig(
            enabled=receipts.get("enabled", True),
            include_rationale=receipts.get("include_rationale", True),
            include_timing=receipts.get("include_timing", True),
        )

        # Local utility task types
        self._local_tasks = self._router_data.get("local_utility_tasks", [])

    # ------------------------------------------------------------------
    # Version info
    # ------------------------------------------------------------------

    @property
    def models_version(self) -> str:
        return self._models_data.get("version", "unknown")

    @property
    def router_version(self) -> str:
        return self._router_data.get("version", "unknown")

    # ------------------------------------------------------------------
    # Local config
    # ------------------------------------------------------------------

    @property
    def local(self) -> LocalConfig:
        return self._local

    @property
    def local_utility_tasks(self) -> list[str]:
        """Task types routed to the local model."""
        return list(self._local_tasks)

    # ------------------------------------------------------------------
    # Provider lookup
    # ------------------------------------------------------------------

    @property
    def provider_names(self) -> list[str]:
        """List of registered provider names."""
        return list(self._profiles.keys())

    def get_profile(self, name: str) -> ProviderProfile:
        """Get a provider profile by name.

        Raises ConfigError if the provider is not registered.
        """
        if name not in self._profiles:
            raise ConfigError(
                f"Unknown provider: '{name}'. "
                f"Available: {', '.join(self._profiles.keys())}"
            )
        return self._profiles[name]

    def has_provider(self, name: str) -> bool:
        """Check if a provider is registered."""
        return name in self._profiles

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    @property
    def routing_order(self) -> list[RoutingLane]:
        """Routing lanes in priority order (lowest priority number first)."""
        return list(self._routing_order)

    @property
    def enabled_lanes(self) -> list[RoutingLane]:
        """Only enabled routing lanes, in priority order."""
        return [r for r in self._routing_order if r.enabled]

    @property
    def escalation_triggers(self) -> list[EscalationTrigger]:
        return list(self._escalation_triggers)

    @property
    def receipts_config(self) -> ReceiptsConfig:
        return self._receipts

    def is_local_task(self, task_type: str) -> bool:
        """Check if a task type should be routed to the local model."""
        return task_type in self._local_tasks
