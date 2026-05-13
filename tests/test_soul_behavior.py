import os

from src.core.governance.config import load_governance_config
from src.core.soul.behavior import evaluate_soul_behavior
from src.core.soul.store import Soul
from src.core.soul.templates import get_template, invalidate_cache


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "governance.yaml")


def _template_soul(name: str) -> Soul:
    invalidate_cache()
    template = get_template(name)
    assert template is not None
    return Soul(**template.soul_dict)


def _evaluate(soul: Soul, capability: str):
    config = load_governance_config(CONFIG_PATH)
    return evaluate_soul_behavior(
        soul,
        capability,
        risk_config=config.risk_classification,
    )


def test_finance_compliance_monitor_allows_monitoring_actions():
    soul = _template_soul("finance-compliance-monitor")

    scan = _evaluate(soul, "scan_transactions")
    report = _evaluate(soul, "generate_compliance_report")

    assert scan.decision == "allowed"
    assert scan.requires_approval is False
    assert "autonomy_posture:allowed_autonomous" in scan.matched_controls
    assert report.decision == "allowed"
    assert "data_boundary:financial_compliance_evidence:allowed" in report.matched_controls


def test_finance_compliance_monitor_requires_approval_for_regulatory_actions():
    soul = _template_soul("finance-compliance-monitor")

    sar = _evaluate(soul, "file_sar")
    connector_sar = _evaluate(soul, "connector.compliance.file_sar")
    suspend = _evaluate(soul, "connector.compliance.suspend_account")

    assert sar.decision == "requires_approval"
    assert "autonomy_posture:requires_approval" in sar.matched_controls
    assert connector_sar.decision == "requires_approval"
    assert connector_sar.risk_tier == "T3"
    assert "external_transmission:regulatory_compliance_escalation" in connector_sar.matched_controls
    assert suspend.decision == "requires_approval"
    assert suspend.risk_tier == "T3"
    assert "risk_override" in suspend.matched_controls


def test_finance_compliance_monitor_blocks_evidence_tampering():
    soul = _template_soul("finance-compliance-monitor")

    rule_change = _evaluate(soul, "modify_compliance_rule")
    delete_attempt = _evaluate(soul, "attempted_delete_or_modify_compliance_evidence")

    assert rule_change.decision == "blocked"
    assert rule_change.blocked is True
    assert "data_boundary:financial_compliance_evidence:prohibited" in rule_change.matched_controls
    assert delete_attempt.decision == "blocked"
    assert delete_attempt.blocked is True
    assert "kill_switch:compliance_evidence_deletion" in delete_attempt.matched_controls


def test_customer_support_template_replaces_finance_specific_behavior():
    soul = _template_soul("tier-1-support-agent")

    support_answer = _evaluate(soul, "answer_knowledge_base")
    finance_scan = _evaluate(soul, "scan_transactions")
    refund = _evaluate(soul, "connector.support.refund_above_threshold")

    assert support_answer.decision == "allowed"
    assert "autonomy_posture:allowed_autonomous" in support_answer.matched_controls
    assert finance_scan.decision == "requires_approval"
    assert finance_scan.reasons == [
        "Capability is not explicitly allowed and classifies as high risk."
    ]
    assert refund.decision == "requires_approval"
    assert refund.risk_tier == "T3"
    assert "risk_override" in refund.matched_controls
