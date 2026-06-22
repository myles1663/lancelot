"""Shared risk terminology validation for governance, Tool Fabric, and UAB."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from src.core.governance.models import RiskTier


UAB_RISK_LABELS = ("safe", "moderate", "destructive")
ACTION_RISK_CATEGORY_TO_UAB = {
    "read_only": "safe",
    "mutating": "moderate",
    "destructive": "destructive",
}


class ToolRiskLabel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RiskTerminologyBand:
    tool_fabric: str
    uab: str
    governance: str
    governance_tiers: tuple[RiskTier, ...]


RISK_TERMINOLOGY_BANDS = {
    "safe": RiskTerminologyBand(
        tool_fabric="low",
        uab="safe",
        governance="T0/T1",
        governance_tiers=(RiskTier.T0_INERT, RiskTier.T1_REVERSIBLE),
    ),
    "moderate": RiskTerminologyBand(
        tool_fabric="medium",
        uab="moderate",
        governance="T1/T2",
        governance_tiers=(RiskTier.T1_REVERSIBLE, RiskTier.T2_CONTROLLED),
    ),
    "destructive": RiskTerminologyBand(
        tool_fabric="high",
        uab="destructive",
        governance="T3",
        governance_tiers=(RiskTier.T3_IRREVERSIBLE,),
    ),
}


def validate_uab_risk_label(label: str) -> str:
    """Return a known UAB risk label or raise so callers fail closed."""
    if label not in RISK_TERMINOLOGY_BANDS:
        raise ValueError(f"Unknown UAB risk label: {label}")
    return label


def tool_risk_for_uab_label(label: str):
    return ToolRiskLabel(RISK_TERMINOLOGY_BANDS[validate_uab_risk_label(label)].tool_fabric)


def uab_label_for_tool_risk(risk) -> str:
    try:
        parsed = risk if isinstance(risk, ToolRiskLabel) else ToolRiskLabel(risk)
    except ValueError as exc:
        raise ValueError(f"Unknown Tool Fabric risk label: {risk}") from exc

    for band in RISK_TERMINOLOGY_BANDS.values():
        if band.tool_fabric == parsed.value:
            return band.uab
    raise ValueError(f"Unmapped Tool Fabric risk label: {parsed.value}")


def governance_tiers_for_uab_label(label: str) -> tuple[RiskTier, ...]:
    return RISK_TERMINOLOGY_BANDS[validate_uab_risk_label(label)].governance_tiers


def validate_action_risk_manifest(
    manifest: Mapping[str, object],
    known_actions: Iterable[str] | None = None,
) -> None:
    """Validate the shared UAB action-risk manifest.

    Unknown categories or duplicate action entries are rejected because they can
    make the Python bridge and TypeScript daemon classify the same action
    differently.
    """
    required = set(ACTION_RISK_CATEGORY_TO_UAB) | {"sensitive_app_patterns"}
    actual = set(manifest)
    missing = required - actual
    extra = actual - required
    if missing:
        raise ValueError(f"UAB action risk manifest missing keys: {sorted(missing)}")
    if extra:
        raise ValueError(f"UAB action risk manifest has unknown keys: {sorted(extra)}")

    seen: dict[str, str] = {}
    for category in ACTION_RISK_CATEGORY_TO_UAB:
        entries = manifest[category]
        if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
            raise ValueError(f"UAB action risk manifest category must be a string list: {category}")
        validate_uab_risk_label(ACTION_RISK_CATEGORY_TO_UAB[category])
        for action in entries:
            if action in seen:
                raise ValueError(
                    f"UAB action {action!r} appears in both {seen[action]!r} and {category!r}"
                )
            seen[action] = category

    sensitive_patterns = manifest["sensitive_app_patterns"]
    if not isinstance(sensitive_patterns, list) or not all(
        isinstance(item, str) for item in sensitive_patterns
    ):
        raise ValueError("UAB action risk manifest sensitive_app_patterns must be a string list")

    if known_actions is not None:
        unknown = sorted(set(known_actions) - set(seen))
        if unknown:
            raise ValueError(f"UAB actions missing risk classification: {unknown}")


def assert_tool_fabric_terminology_alignment() -> None:
    """Fail if Tool Fabric's exported terminology drifts from this mapping."""
    from src.tools.contracts import RISK_TERMINOLOGY as TOOL_RISK_TERMINOLOGY
    from src.tools.contracts import RiskLevel

    expected_tool_risks = {RiskLevel(band.tool_fabric) for band in RISK_TERMINOLOGY_BANDS.values()}
    actual_tool_risks = set(TOOL_RISK_TERMINOLOGY)
    if actual_tool_risks != expected_tool_risks:
        raise ValueError(
            "Tool Fabric risk terminology labels drifted: "
            f"{sorted(risk.value for risk in actual_tool_risks)}"
        )

    for label, band in RISK_TERMINOLOGY_BANDS.items():
        tool_risk = RiskLevel(band.tool_fabric)
        tool_entry = TOOL_RISK_TERMINOLOGY.get(tool_risk)
        if tool_entry is None:
            raise ValueError(f"Tool Fabric risk terminology missing {tool_risk.value}")
        if tool_entry.get("uab") != label:
            raise ValueError(
                f"Tool Fabric risk {tool_risk.value} maps to "
                f"{tool_entry.get('uab')!r}, expected {label!r}"
            )
        if tool_entry.get("governance") != band.governance:
            raise ValueError(
                f"Tool Fabric risk {tool_risk.value} maps to governance "
                f"{tool_entry.get('governance')!r}, expected {band.governance!r}"
            )
