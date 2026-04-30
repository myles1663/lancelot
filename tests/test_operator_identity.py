"""Tests for the Operator Identity subsystem (operator_identity.py)."""

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from dataclasses import FrozenInstanceError

from src.core.operator_identity import (
    OperatorIdentity,
    SYSTEM_IDENTITY,
    IDENTITY_REQUIRED_TYPES,
    IdentityRequiredError,
    InvalidIdentityError,
    resolve_operator_id,
    inject_identity_into_metadata,
)
from src.shared.receipts import ActionType


# ── OperatorIdentity dataclass basics ──────────────────────────────


class TestOperatorIdentityCreation:

    def test_create_with_required_fields(self):
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        assert ident.operator_id == "op-1"
        assert ident.display_name == "Alice"
        assert ident.session_id == ""
        assert ident.session_started_at == ""
        assert ident.auth_method == "local"
        assert ident.ip_address == ""

    def test_create_with_all_fields(self):
        ident = OperatorIdentity(
            operator_id="op-2",
            display_name="Bob",
            session_id="sess-1",
            session_started_at="2026-01-01T00:00:00Z",
            auth_method="api_key",
            ip_address="10.0.0.1",
        )
        assert ident.operator_id == "op-2"
        assert ident.auth_method == "api_key"
        assert ident.ip_address == "10.0.0.1"

    def test_frozen_immutability(self):
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        with pytest.raises(FrozenInstanceError):
            ident.operator_id = "changed"

    def test_frozen_immutability_display_name(self):
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        with pytest.raises(FrozenInstanceError):
            ident.display_name = "changed"


class TestOperatorIdentityToDict:

    def test_to_dict_contains_all_fields(self):
        ident = OperatorIdentity(
            operator_id="op-1",
            display_name="Alice",
            session_id="s1",
            session_started_at="2026-01-01T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        )
        d = ident.to_dict()
        assert d["operator_id"] == "op-1"
        assert d["display_name"] == "Alice"
        assert d["session_id"] == "s1"
        assert d["session_started_at"] == "2026-01-01T00:00:00Z"
        assert d["auth_method"] == "local"
        assert d["ip_address"] == "127.0.0.1"

    def test_to_dict_returns_plain_dict(self):
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        d = ident.to_dict()
        assert isinstance(d, dict)
        assert len(d) == 6


class TestOperatorIdentityFromDict:

    def test_from_dict_roundtrip(self):
        original = OperatorIdentity(
            operator_id="op-1",
            display_name="Alice",
            session_id="s1",
            session_started_at="2026-01-01T00:00:00Z",
            auth_method="api_key",
            ip_address="10.0.0.1",
        )
        restored = OperatorIdentity.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_with_missing_keys_uses_defaults(self):
        restored = OperatorIdentity.from_dict({"operator_id": "op-1", "display_name": "Bob"})
        assert restored.operator_id == "op-1"
        assert restored.display_name == "Bob"
        assert restored.session_id == ""
        assert restored.auth_method == "local"

    def test_from_dict_empty_dict(self):
        restored = OperatorIdentity.from_dict({})
        assert restored.operator_id == ""
        assert restored.display_name == ""
        assert restored.auth_method == "local"


class TestOperatorIdentityProperties:

    def test_is_system_true(self):
        assert SYSTEM_IDENTITY.is_system is True

    def test_is_system_false_for_regular_identity(self):
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        assert ident.is_system is False

    def test_is_valid_true(self):
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        assert ident.is_valid is True

    def test_is_valid_false_missing_operator_id(self):
        ident = OperatorIdentity(operator_id="", display_name="Alice")
        assert ident.is_valid is False

    def test_is_valid_false_missing_display_name(self):
        ident = OperatorIdentity(operator_id="op-1", display_name="")
        assert ident.is_valid is False

    def test_is_valid_false_both_empty(self):
        ident = OperatorIdentity(operator_id="", display_name="")
        assert ident.is_valid is False


# ── SYSTEM_IDENTITY constant ──────────────────────────────────────


class TestSystemIdentity:

    def test_system_identity_operator_id(self):
        assert SYSTEM_IDENTITY.operator_id == "SYSTEM"

    def test_system_identity_display_name(self):
        assert SYSTEM_IDENTITY.display_name == "Lancelot Automation"

    def test_system_identity_auth_method(self):
        assert SYSTEM_IDENTITY.auth_method == "system"


# ── resolve_operator_id ───────────────────────────────────────────


class TestResolveOperatorId:

    def test_deterministic_same_input(self):
        id1 = resolve_operator_id("myles")
        id2 = resolve_operator_id("myles")
        assert id1 == id2

    def test_different_inputs_produce_different_ids(self):
        id1 = resolve_operator_id("alice")
        id2 = resolve_operator_id("bob")
        assert id1 != id2

    def test_returns_valid_uuid_string(self):
        result = resolve_operator_id("test_user")
        parsed = uuid.UUID(result)
        assert parsed.version == 5

    def test_empty_string_input(self):
        # Should not raise, empty string is a valid input to uuid5
        result = resolve_operator_id("")
        assert isinstance(result, str)
        uuid.UUID(result)  # Should parse without error


# ── inject_identity_into_metadata ────────────────────────────────


class TestInjectIdentityIntoMetadata:

    def test_injects_all_fields(self):
        ident = OperatorIdentity(
            operator_id="op-1",
            display_name="Alice",
            session_id="s1",
            auth_method="local",
        )
        result = inject_identity_into_metadata({}, ident)
        assert result["operator_id"] == "op-1"
        assert result["operator_display_name"] == "Alice"
        assert result["session_id"] == "s1"
        assert result["auth_method"] == "local"

    def test_preserves_existing_metadata(self):
        metadata = {"foo": "bar", "count": 42}
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        result = inject_identity_into_metadata(metadata, ident)
        assert result["foo"] == "bar"
        assert result["count"] == 42
        assert result["operator_id"] == "op-1"

    def test_does_not_mutate_original(self):
        metadata = {"key": "value"}
        ident = OperatorIdentity(operator_id="op-1", display_name="Alice")
        result = inject_identity_into_metadata(metadata, ident)
        assert "operator_id" not in metadata
        assert "operator_id" in result

    def test_none_identity_sets_null_fields(self):
        result = inject_identity_into_metadata({"x": 1}, None)
        assert result["operator_id"] is None
        assert result["operator_display_name"] is None
        assert result["session_id"] is None
        assert result["auth_method"] is None
        assert result["x"] == 1

    def test_system_identity_injection(self):
        result = inject_identity_into_metadata({}, SYSTEM_IDENTITY)
        assert result["operator_id"] == "SYSTEM"
        assert result["operator_display_name"] == "Lancelot Automation"


# ── IDENTITY_REQUIRED_TYPES ──────────────────────────────────────


class TestIdentityRequiredTypes:

    def test_contains_kill_switch_types(self):
        assert ActionType.KILL_SWITCH_ISSUED.value in IDENTITY_REQUIRED_TYPES
        assert ActionType.KILL_SWITCH_LIFTED.value in IDENTITY_REQUIRED_TYPES

    def test_contains_t3_types(self):
        assert ActionType.T3_APPROVED.value in IDENTITY_REQUIRED_TYPES
        assert ActionType.T3_REJECTED.value in IDENTITY_REQUIRED_TYPES

    def test_contains_soul_governance_types(self):
        assert ActionType.SOUL_UPDATED.value in IDENTITY_REQUIRED_TYPES
        assert ActionType.SOUL_VERSION_PINNED.value in IDENTITY_REQUIRED_TYPES

    def test_contains_credential_types(self):
        assert ActionType.CREDENTIAL_REGISTERED.value in IDENTITY_REQUIRED_TYPES
        assert ActionType.CREDENTIAL_REVOKED.value in IDENTITY_REQUIRED_TYPES

    def test_does_not_contain_automated_types(self):
        assert ActionType.TOOL_CALL.value not in IDENTITY_REQUIRED_TYPES
        assert ActionType.LLM_CALL.value not in IDENTITY_REQUIRED_TYPES
        assert ActionType.SYSTEM.value not in IDENTITY_REQUIRED_TYPES

    def test_is_a_set(self):
        assert isinstance(IDENTITY_REQUIRED_TYPES, set)


# ── Exceptions ───────────────────────────────────────────────────


class TestExceptions:

    def test_identity_required_error_message(self):
        err = IdentityRequiredError("kill_switch_issued")
        assert "kill_switch_issued" in str(err)
        assert "requires OperatorIdentity" in str(err)
        assert err.receipt_type == "kill_switch_issued"

    def test_invalid_identity_error_message(self):
        ident = OperatorIdentity(operator_id="", display_name="")
        err = InvalidIdentityError("t3_approved", ident)
        assert "t3_approved" in str(err)
        assert "invalid OperatorIdentity" in str(err)
        assert err.receipt_type == "t3_approved"
        assert err.identity is ident
