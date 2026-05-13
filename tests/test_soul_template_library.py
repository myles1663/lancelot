from pathlib import Path

import pytest

from src.core.soul.templates import (
    get_template,
    invalidate_cache,
    list_template_metadata,
    load_templates,
)


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

EXPECTED_BUILTIN_TEMPLATES = {
    "accounts-payable-automation": "finance",
    "access-provisioner": "it-security",
    "appointment-scheduling-coordinator": "healthcare",
    "benefits-administrator": "hr",
    "board-reporting-agent": "executive",
    "campaign-analyst": "marketing",
    "clinical-documentation-assistant": "healthcare",
    "competitive-intelligence-analyst": "executive",
    "content-operations-agent": "marketing",
    "contract-review-analyst": "legal",
    "curriculum-assessment-assistant": "education",
    "customer-feedback-analyst": "customer-support",
    "ediscovery-litigation-hold": "legal",
    "employee-onboarding-coordinator": "hr",
    "escalation-manager": "customer-support",
    "finance-compliance-monitor": "finance",
    "finance-internal-ops": "finance",
    "finance-reporting-analyst": "finance",
    "helpdesk-triage-agent": "it-security",
    "insurance-claims-processor": "healthcare",
    "inventory-demand-monitor": "operations",
    "lead-qualification-agent": "sales",
    "legal-research-assistant": "legal",
    "patient-records-accessor": "healthcare",
    "payroll-operations-monitor": "hr",
    "pipeline-analyst": "sales",
    "procurement-agent": "operations",
    "proposal-quote-generator": "sales",
    "recruiting-screener": "hr",
    "regulatory-filing-agent": "legal",
    "security-incident-responder": "it-security",
    "social-media-monitor": "marketing",
    "student-records-administrator": "education",
    "tax-audit-preparation": "finance",
    "tier-1-support-agent": "customer-support",
    "treasury-cash-management": "finance",
    "vendor-risk-assessor": "operations",
    "vulnerability-patch-monitor": "it-security",
}


@pytest.fixture(autouse=True)
def clear_template_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def test_builtin_soul_templates_load_and_cover_complete_library():
    templates = load_templates(str(TEMPLATES_DIR), force_reload=True)
    loaded = {template.name: template for template in templates}

    assert set(loaded) == set(EXPECTED_BUILTIN_TEMPLATES)
    for name, industry in EXPECTED_BUILTIN_TEMPLATES.items():
        template = loaded[name]
        assert template.metadata.industry == industry
        assert template.raw_yaml
        assert "_template_metadata" not in template.soul_dict
        assert template.soul_dict["approval_rules"]["channels"]
        assert template.soul_dict["scheduling_boundaries"]["no_autonomous_irreversible"] is True


def test_representative_templates_include_structured_governance_fields():
    templates = load_templates(str(TEMPLATES_DIR), force_reload=True)
    loaded = {template.name: template for template in templates}

    for name in EXPECTED_BUILTIN_TEMPLATES:
        soul_dict = loaded[name].soul_dict
        assert soul_dict.get("risk_overrides"), name
        assert (
            soul_dict.get("data_boundaries")
            or soul_dict.get("external_transmission_rules")
            or soul_dict.get("connector_policies")
            or soul_dict.get("kill_switch_rules")
        ), name


def test_builtin_template_metadata_filtering_by_industry():
    load_templates(str(TEMPLATES_DIR), force_reload=True)

    healthcare = list_template_metadata(str(TEMPLATES_DIR), industry="healthcare")
    healthcare_names = {metadata["name"] for metadata in healthcare}

    assert healthcare_names == {
        "patient-records-accessor",
        "clinical-documentation-assistant",
        "insurance-claims-processor",
        "appointment-scheduling-coordinator",
    }


@pytest.mark.parametrize(
    ("template_name", "required_approval"),
    [
        ("treasury-cash-management", "execute_external_transfer"),
        ("patient-records-accessor", "bulk_phi_export"),
        ("clinical-documentation-assistant", "diagnostic_statement"),
        ("contract-review-analyst", "modify_executed_agreement"),
        ("recruiting-screener", "access_compensation_data"),
        ("security-incident-responder", "isolate_host"),
        ("proposal-quote-generator", "discount_beyond_threshold"),
        ("social-media-monitor", "publish_public_response"),
        ("student-records-administrator", "external_student_record_disclosure"),
    ],
)
def test_representative_templates_encode_domain_approval_gates(
    template_name,
    required_approval,
):
    template = get_template(template_name, str(TEMPLATES_DIR))

    assert template is not None
    requires_approval = template.soul_dict["autonomy_posture"]["requires_approval"]
    assert required_approval in requires_approval
