import json
from pathlib import Path

import pytest

from src.core.governance.models import RiskTier
from src.core.governance.risk_terminology import (
    ToolRiskLabel,
    RISK_TERMINOLOGY_BANDS,
    assert_tool_fabric_terminology_alignment,
    governance_tiers_for_uab_label,
    tool_risk_for_uab_label,
    uab_label_for_tool_risk,
    validate_action_risk_manifest,
    validate_uab_risk_label,
)
from src.tools.contracts import RiskLevel


def test_risk_terminology_mapping_is_deterministic():
    assert tool_risk_for_uab_label("safe") == ToolRiskLabel.LOW
    assert tool_risk_for_uab_label("moderate") == ToolRiskLabel.MEDIUM
    assert tool_risk_for_uab_label("destructive") == ToolRiskLabel.HIGH

    assert uab_label_for_tool_risk(RiskLevel.LOW) == "safe"
    assert uab_label_for_tool_risk("medium") == "moderate"
    assert uab_label_for_tool_risk(RiskLevel.HIGH) == "destructive"

    assert governance_tiers_for_uab_label("safe") == (
        RiskTier.T0_INERT,
        RiskTier.T1_REVERSIBLE,
    )
    assert governance_tiers_for_uab_label("moderate") == (
        RiskTier.T1_REVERSIBLE,
        RiskTier.T2_CONTROLLED,
    )
    assert governance_tiers_for_uab_label("destructive") == (
        RiskTier.T3_IRREVERSIBLE,
    )


def test_unknown_risk_terms_fail_closed():
    with pytest.raises(ValueError, match="Unknown UAB risk label"):
        validate_uab_risk_label("experimental")

    with pytest.raises(ValueError, match="Unknown Tool Fabric risk label"):
        uab_label_for_tool_risk("critical")


def test_tool_fabric_terminology_stays_aligned():
    assert_tool_fabric_terminology_alignment()


def test_tool_fabric_alignment_detects_label_or_governance_drift(monkeypatch):
    from src.tools import contracts

    drifted_mapping = {
        RiskLevel.LOW: {
            **contracts.RISK_TERMINOLOGY[RiskLevel.LOW],
            "governance": "T0",
        },
        RiskLevel.MEDIUM: contracts.RISK_TERMINOLOGY[RiskLevel.MEDIUM],
        RiskLevel.HIGH: contracts.RISK_TERMINOLOGY[RiskLevel.HIGH],
    }
    monkeypatch.setattr(contracts, "RISK_TERMINOLOGY", drifted_mapping)

    with pytest.raises(ValueError, match="governance"):
        assert_tool_fabric_terminology_alignment()


def test_python_risk_contract_matches_locked_uab_labels():
    assert tuple(RISK_TERMINOLOGY_BANDS) == ("safe", "moderate", "destructive")
    assert {
        label: {
            "tool_fabric": band.tool_fabric,
            "governance": band.governance,
        }
        for label, band in RISK_TERMINOLOGY_BANDS.items()
    } == {
        "safe": {"tool_fabric": "low", "governance": "T0/T1"},
        "moderate": {"tool_fabric": "medium", "governance": "T1/T2"},
        "destructive": {"tool_fabric": "high", "governance": "T3"},
    }


def test_uab_action_risk_manifest_rejects_unknown_or_duplicate_terms():
    manifest = json.loads(Path("packages/uab/data/action-risk.json").read_text(encoding="utf-8"))
    validate_action_risk_manifest(manifest)

    with pytest.raises(ValueError, match="unknown keys"):
        validate_action_risk_manifest({**manifest, "experimental": ["launchMissiles"]})

    duplicate = {
        **manifest,
        "mutating": [*manifest["mutating"], manifest["read_only"][0]],
    }
    with pytest.raises(ValueError, match="appears in both"):
        validate_action_risk_manifest(duplicate)


def test_risk_terminology_doc_mentions_all_locked_labels():
    text = Path("docs/risk-terminology.md").read_text(encoding="utf-8")
    for label in ("T0", "T1", "T2", "T3", "low", "medium", "high", "safe", "moderate", "destructive"):
        assert label in text


def test_tool_risk_lookup_is_safe_in_fresh_process():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.core.governance.risk_terminology import tool_risk_for_uab_label;"
            "print(tool_risk_for_uab_label('moderate').value)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "medium"
