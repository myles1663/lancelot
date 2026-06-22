"""Central authority grants for governed UAB actions."""

from __future__ import annotations

import hmac
import json
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Optional

from src.core.governance.risk_terminology import validate_uab_risk_label


DEFAULT_UAB_GRANT_TTL_SECONDS = 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class UABGrantValidation:
    valid: bool
    reason: str = ""


@dataclass
class UABAuthorityGrant:
    """Scoped, signed, time-limited authority for a governed UAB action."""

    grant_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    issued_at: str = field(default_factory=lambda: _to_iso(_utc_now()))
    expires_at: str = ""
    nonce: str = field(default_factory=lambda: str(uuid.uuid4()))
    risk_tier: str = ""
    uab_risk: str = ""
    capability: str = ""
    app_name: str = ""
    app_pid: Optional[int] = None
    action: str = ""
    selector_scope: str = ""
    sensitive_read: bool = False
    mutating: bool = False
    destructive: bool = False
    external_submission: bool = False
    credential_sensitive: bool = False
    policy_version: str = ""
    soul_version: str = ""
    workflow_id: str = ""
    run_id: str = ""
    parent_receipt_id: Optional[str] = None
    approval_id: Optional[str] = None
    signature: str = ""
    _parse_errors: list[str] = field(default_factory=list, repr=False, compare=False)

    REQUIRED_FIELDS = (
        "grant_id",
        "issued_at",
        "expires_at",
        "nonce",
        "risk_tier",
        "uab_risk",
        "capability",
        "app_name",
        "action",
        "policy_version",
        "soul_version",
    )

    def __post_init__(self) -> None:
        if not self.expires_at:
            issued = _parse_time(self.issued_at)
            self.expires_at = _to_iso(
                issued + timedelta(seconds=DEFAULT_UAB_GRANT_TTL_SECONDS)
            )
        if self.uab_risk:
            validate_uab_risk_label(self.uab_risk)

    def to_dict(self, include_signature: bool = True) -> dict[str, Any]:
        data = {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if not item.name.startswith("_")
        }
        if not include_signature:
            data.pop("signature", None)
        return data

    def canonical_payload(self) -> str:
        return json.dumps(
            self.to_dict(include_signature=False),
            sort_keys=True,
            separators=(",", ":"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UABAuthorityGrant":
        allowed = {item.name for item in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in allowed}
        parse_errors = [
            f"missing required fields: {name}"
            for name in cls.REQUIRED_FIELDS
            if name not in data
        ]
        grant = cls(**kwargs)
        grant._parse_errors = parse_errors
        return grant

    @classmethod
    def from_json(cls, payload: str) -> "UABAuthorityGrant":
        return cls.from_dict(json.loads(payload))

    def missing_required_fields(self) -> list[str]:
        missing: list[str] = []
        for error in self._parse_errors:
            if error.startswith("missing required fields: "):
                missing.extend(error.removeprefix("missing required fields: ").split(", "))
        for name in self.REQUIRED_FIELDS:
            value = getattr(self, name)
            if value is None or value == "":
                missing.append(name)
        return sorted(set(missing), key=missing.index)

    def sign(self, secret: str | bytes) -> "UABAuthorityGrant":
        self.signature = sign_uab_grant_payload(self.canonical_payload(), secret)
        return self

    def verify_signature(self, secret: str | bytes) -> bool:
        if not self.signature:
            return False
        expected = sign_uab_grant_payload(self.canonical_payload(), secret)
        return hmac.compare_digest(self.signature, expected)

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc) >= _parse_time(self.expires_at)

    def matches_scope(
        self,
        *,
        app_name: str,
        action: str,
        app_pid: int | None = None,
        selector_scope: str | None = None,
    ) -> bool:
        if self.app_name != app_name or self.action != action:
            return False
        if self.app_pid is not None and app_pid is not None and self.app_pid != app_pid:
            return False
        if (
            selector_scope is not None
            and self.selector_scope
            and self.selector_scope != selector_scope
        ):
            return False
        return True

    def validate(
        self,
        secret: str | bytes,
        *,
        now: datetime | None = None,
        app_name: str | None = None,
        action: str | None = None,
        app_pid: int | None = None,
        selector_scope: str | None = None,
    ) -> UABGrantValidation:
        missing = self.missing_required_fields()
        if missing:
            return UABGrantValidation(False, f"missing required fields: {', '.join(missing)}")
        try:
            validate_uab_risk_label(self.uab_risk)
        except ValueError as exc:
            return UABGrantValidation(False, str(exc))
        if self.is_expired(now):
            return UABGrantValidation(False, "grant expired")
        if not self.verify_signature(secret):
            return UABGrantValidation(False, "invalid grant signature")
        if app_name is not None or action is not None:
            if self.app_pid is not None and app_pid is None:
                return UABGrantValidation(False, "grant scope missing app_pid")
            if self.selector_scope and selector_scope is None:
                return UABGrantValidation(False, "grant scope missing selector_scope")
            if not self.matches_scope(
                app_name=app_name or self.app_name,
                action=action or self.action,
                app_pid=app_pid,
                selector_scope=selector_scope,
            ):
                return UABGrantValidation(False, "grant scope mismatch")
        return UABGrantValidation(True, "valid")


def sign_uab_grant_payload(payload: str, secret: str | bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(key, payload.encode("utf-8"), sha256).hexdigest()


def create_uab_authority_grant(
    *,
    secret: str | bytes,
    risk_tier: str,
    uab_risk: str,
    capability: str,
    app_name: str,
    action: str,
    policy_version: str,
    soul_version: str,
    app_pid: int | None = None,
    selector_scope: str = "",
    sensitive_read: bool = False,
    mutating: bool = False,
    destructive: bool = False,
    external_submission: bool = False,
    credential_sensitive: bool = False,
    workflow_id: str = "",
    run_id: str = "",
    parent_receipt_id: str | None = None,
    approval_id: str | None = None,
    issued_at: datetime | None = None,
    ttl_seconds: int = DEFAULT_UAB_GRANT_TTL_SECONDS,
) -> UABAuthorityGrant:
    issued = issued_at or _utc_now()
    grant = UABAuthorityGrant(
        issued_at=_to_iso(issued),
        expires_at=_to_iso(issued + timedelta(seconds=ttl_seconds)),
        risk_tier=risk_tier,
        uab_risk=uab_risk,
        capability=capability,
        app_name=app_name,
        app_pid=app_pid,
        action=action,
        selector_scope=selector_scope,
        sensitive_read=sensitive_read,
        mutating=mutating,
        destructive=destructive,
        external_submission=external_submission,
        credential_sensitive=credential_sensitive,
        policy_version=policy_version,
        soul_version=soul_version,
        workflow_id=workflow_id,
        run_id=run_id,
        parent_receipt_id=parent_receipt_id,
        approval_id=approval_id,
    )
    return grant.sign(secret)
