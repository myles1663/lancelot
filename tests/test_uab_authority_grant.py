from datetime import datetime, timedelta, timezone

import pytest

from src.core.execution_authority.uab_grant import (
    UABAuthorityGrant,
    create_uab_authority_grant,
)


SECRET = "test-uab-grant-secret"


def _grant(**overrides):
    values = {
        "secret": SECRET,
        "risk_tier": "T2_CONTROLLED",
        "uab_risk": "moderate",
        "capability": "uab.click",
        "app_name": "notepad",
        "app_pid": 1234,
        "action": "click",
        "selector_scope": "window:main > button:save",
        "mutating": True,
        "policy_version": "policy-v1",
        "soul_version": "soul-v1",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "parent_receipt_id": "receipt-parent",
        "approval_id": "approval-1",
    }
    values.update(overrides)
    return create_uab_authority_grant(**values)


def test_uab_authority_grant_serializes_and_verifies_signature():
    grant = _grant()

    payload = grant.to_json()
    restored = UABAuthorityGrant.from_json(payload)

    assert restored.grant_id == grant.grant_id
    assert restored.signature
    assert restored.verify_signature(SECRET) is True
    assert restored.validate(
        SECRET,
        app_name="notepad",
        app_pid=1234,
        action="click",
        selector_scope="window:main > button:save",
    ).valid is True


def test_uab_authority_grant_signature_rejects_tampering():
    grant = _grant()
    data = grant.to_dict()
    data["action"] = "type"
    tampered = UABAuthorityGrant.from_dict(data)

    result = tampered.validate(SECRET, app_name="notepad", app_pid=1234, action="type")

    assert result.valid is False
    assert result.reason == "invalid grant signature"


def test_uab_authority_grant_expired_grant_fails_validation():
    issued = datetime.now(timezone.utc) - timedelta(minutes=5)
    grant = _grant(issued_at=issued, ttl_seconds=1)

    result = grant.validate(SECRET, now=datetime.now(timezone.utc))

    assert result.valid is False
    assert result.reason == "grant expired"


def test_uab_authority_grant_missing_required_fields_fail_validation():
    grant = _grant()
    data = grant.to_dict()
    data["capability"] = ""
    missing = UABAuthorityGrant.from_dict(data)
    missing.sign(SECRET)

    result = missing.validate(SECRET)

    assert result.valid is False
    assert "capability" in result.reason


@pytest.mark.parametrize(
    "field_name",
    ["grant_id", "issued_at", "expires_at", "nonce", "uab_risk"],
)
def test_uab_authority_grant_parse_missing_defaulted_fields_fail_validation(field_name):
    grant = _grant()
    data = grant.to_dict()
    data.pop(field_name)
    parsed = UABAuthorityGrant.from_dict(data)
    parsed.sign(SECRET)

    result = parsed.validate(SECRET)

    assert result.valid is False
    assert field_name in result.reason


def test_uab_authority_grant_scope_helper_rejects_wrong_target():
    grant = _grant()

    assert grant.matches_scope(app_name="notepad", app_pid=1234, action="click") is True
    assert grant.matches_scope(app_name="notepad", app_pid=9999, action="click") is False
    assert grant.validate(SECRET, app_name="notepad", app_pid=9999, action="click").valid is False


def test_uab_authority_grant_validation_requires_scoped_pid_and_selector_context():
    grant = _grant()

    missing_pid = grant.validate(
        SECRET,
        app_name="notepad",
        action="click",
        selector_scope="window:main > button:save",
    )
    missing_selector = grant.validate(
        SECRET,
        app_name="notepad",
        app_pid=1234,
        action="click",
    )
    wrong_selector = grant.validate(
        SECRET,
        app_name="notepad",
        app_pid=1234,
        action="click",
        selector_scope="window:main > button:discard",
    )

    assert missing_pid.valid is False
    assert missing_pid.reason == "grant scope missing app_pid"
    assert missing_selector.valid is False
    assert missing_selector.reason == "grant scope missing selector_scope"
    assert wrong_selector.valid is False
    assert wrong_selector.reason == "grant scope mismatch"


def test_uab_authority_grant_unknown_uab_risk_fails_closed():
    with pytest.raises(ValueError, match="Unknown UAB risk label"):
        _grant(uab_risk="experimental")
