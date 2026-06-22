import pytest

from src.core.execution_authority import create_uab_authority_grant
from src.shared.receipts import ActionType, ReceiptStatus
from src.tools.receipts_uab import (
    UAB_RECEIPT_CANONICAL_DEFERRAL,
    UABReceiptMetadata,
    build_uab_receipt_metadata,
    create_uab_compatibility_receipt,
)


SECRET = "uab-receipt-metadata-test-secret"


def _grant(**overrides):
    values = {
        "secret": SECRET,
        "risk_tier": "T2_CONTROLLED",
        "uab_risk": "moderate",
        "capability": "uab_type",
        "app_name": "Notepad",
        "app_pid": 7,
        "action": "type",
        "selector_scope": "edit1",
        "policy_version": "policy-v1",
        "soul_version": "v1",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "parent_receipt_id": "parent-1",
        "approval_id": "approval-1",
        "mutating": True,
    }
    values.update(overrides)
    return create_uab_authority_grant(**values)


def _denied_context(**overrides):
    values = {
        "outcome": "denied",
        "app_name": "Outlook",
        "app_pid": 9,
        "action": "state",
        "selector_scope": "window:Outlook",
        "risk_tier": "T2_CONTROLLED",
        "uab_risk": "moderate",
        "parent_receipt_id": "parent-denied-1",
        "workflow_id": "workflow-denied-1",
        "run_id": "run-denied-1",
        "denial_reason": "missing_authority_grant",
    }
    values.update(overrides)
    return values


def test_success_metadata_extracts_governed_grant_fields():
    grant = _grant()

    metadata = build_uab_receipt_metadata(outcome="success", grant=grant)

    assert metadata.grant_id == grant.grant_id
    assert metadata.app_name == "Notepad"
    assert metadata.app_pid == 7
    assert metadata.action == "type"
    assert metadata.selector_scope == "edit1"
    assert metadata.risk_tier == "T2_CONTROLLED"
    assert metadata.uab_risk == "moderate"
    assert metadata.mutating is True
    assert metadata.approval_id == "approval-1"
    assert metadata.parent_receipt_id == "parent-1"
    assert metadata.workflow_id == "workflow-1"
    assert metadata.run_id == "run-1"
    assert metadata.local_uab_audit_is_canonical is False
    assert metadata.canonical_receipt_deferred_to == UAB_RECEIPT_CANONICAL_DEFERRAL


def test_denied_metadata_requires_reason_and_keeps_provider_reason():
    metadata = build_uab_receipt_metadata(
        **_denied_context(
            sensitive_read=True,
        ),
    )

    assert metadata.grant_id is None
    assert metadata.outcome == "denied"
    assert metadata.denial_reason == "missing_authority_grant"
    assert metadata.sensitive_read is True


def test_failed_metadata_requires_error_reason():
    metadata = build_uab_receipt_metadata(
        outcome="failed",
        grant=_grant(),
        error_reason="daemon offline",
    )

    assert metadata.outcome == "failed"
    assert metadata.error_reason == "daemon offline"


def test_metadata_rejects_missing_denial_and_error_reasons():
    with pytest.raises(ValueError, match="denial_reason"):
        build_uab_receipt_metadata(
            **_denied_context(denial_reason=None),
        )

    with pytest.raises(ValueError, match="error_reason"):
        build_uab_receipt_metadata(outcome="failed", grant=_grant())


@pytest.mark.parametrize(
    "missing_field",
    [
        "app_name",
        "app_pid",
        "action",
        "selector_scope",
        "risk_tier",
        "uab_risk",
        "parent_receipt_id",
        "workflow_id",
        "run_id",
    ],
)
def test_metadata_rejects_missing_required_context_for_denials(missing_field):
    values = _denied_context()
    values[missing_field] = 0 if missing_field == "app_pid" else ""

    with pytest.raises(ValueError, match=missing_field):
        build_uab_receipt_metadata(**values)


@pytest.mark.parametrize(
    "outcome, reason_field",
    [("success", None), ("failed", "error_reason")],
)
def test_governed_success_and_failure_metadata_require_grant_id(outcome, reason_field):
    values = {
        "outcome": outcome,
        "app_name": "Notepad",
        "app_pid": 7,
        "action": "type",
        "selector_scope": "edit1",
        "risk_tier": "T2_CONTROLLED",
        "uab_risk": "moderate",
        "parent_receipt_id": "parent-1",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
    }
    if reason_field:
        values[reason_field] = "daemon offline"

    with pytest.raises(ValueError, match="grant_id"):
        build_uab_receipt_metadata(**values)


def test_metadata_rejects_constructor_canonical_proof_override():
    with pytest.raises(TypeError):
        UABReceiptMetadata(
            **_denied_context(),
            local_uab_audit_is_canonical=True,
        )


def test_metadata_from_dict_ignores_canonical_proof_override():
    metadata = build_uab_receipt_metadata(**_denied_context())
    data = metadata.to_dict()
    data["local_uab_audit_is_canonical"] = True
    data["canonical_receipt_deferred_to"] = "UAB-local-audit"

    restored = UABReceiptMetadata.from_dict(data)

    assert restored.local_uab_audit_is_canonical is False
    assert restored.canonical_receipt_deferred_to == UAB_RECEIPT_CANONICAL_DEFERRAL


def test_compatibility_adapter_wraps_metadata_without_claiming_canonical_proof():
    metadata = build_uab_receipt_metadata(outcome="success", grant=_grant())

    receipt = create_uab_compatibility_receipt(
        metadata,
        duration_ms=12,
        operator_id="operator-1",
    )

    assert receipt.action_type == ActionType.UAB_ACTION.value
    assert receipt.action_name == "uab.type"
    assert receipt.status == ReceiptStatus.SUCCESS.value
    assert receipt.parent_id == "parent-1"
    assert receipt.quest_id == "workflow-1"
    assert receipt.session_id == "run-1"
    assert receipt.duration_ms == 12
    assert receipt.operator_id == "operator-1"
    assert receipt.outputs["uab_receipt_metadata"]["grant_id"] == metadata.grant_id
    assert receipt.metadata["canonical_receipt_deferred_to"] == "CORE-B3"
    assert receipt.metadata["local_uab_audit_is_canonical"] is False


def test_compatibility_adapter_marks_denied_and_failed_as_failure_status():
    denied = create_uab_compatibility_receipt(
        build_uab_receipt_metadata(
            **_denied_context(
                app_name="Notepad",
                app_pid=7,
                action="type",
                selector_scope="edit1",
            ),
        )
    )
    failed = create_uab_compatibility_receipt(
        build_uab_receipt_metadata(
            outcome="failed",
            grant=_grant(),
            error_reason="daemon offline",
        )
    )

    assert denied.status == ReceiptStatus.FAILURE.value
    assert denied.error_message == "missing_authority_grant"
    assert failed.status == ReceiptStatus.FAILURE.value
    assert failed.error_message == "daemon offline"


def test_metadata_round_trip_ignores_unknown_keys():
    metadata = build_uab_receipt_metadata(outcome="success", grant=_grant())
    data = metadata.to_dict()
    data["unexpected"] = "ignored"

    restored = UABReceiptMetadata.from_dict(data)

    assert restored == metadata
