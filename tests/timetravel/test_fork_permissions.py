# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for fork_permissions — Soul-governed validation for time-travel ops.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock

from src.timetravel.fork_permissions import (
    TimeTravelMode,
    ForkDecision,
    evaluate_inspect_request,
    evaluate_replay_request,
    evaluate_fork_request,
    create_rejection_receipt_data,
)
from src.core.soul.store import ForkPermissions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_soul(
    allow_fork=True,
    require_approval_tier=3,
    modifiable_fields=None,
    prohibited_modifications=None,
    has_fork_permissions=True,
):
    """Build a mock Soul with the given fork_permissions settings."""
    soul = MagicMock()
    if not has_fork_permissions:
        soul.fork_permissions = None
        # getattr fallback
        del soul.fork_permissions
        return soul

    if prohibited_modifications is None:
        prohibited_modifications = [
            "operator_id", "session_id", "quest_id", "timestamp", "id",
        ]
    if modifiable_fields is None:
        modifiable_fields = ["inputs"]

    soul.fork_permissions = ForkPermissions(
        allow_fork=allow_fork,
        require_approval_tier=require_approval_tier,
        modifiable_fields=modifiable_fields,
        prohibited_modifications=prohibited_modifications,
    )
    return soul


# ---------------------------------------------------------------------------
# TimeTravelMode enum
# ---------------------------------------------------------------------------

class TestTimeTravelMode:
    def test_inspect_value(self):
        assert TimeTravelMode.INSPECT.value == "inspect"

    def test_replay_value(self):
        assert TimeTravelMode.REPLAY.value == "replay"

    def test_fork_value(self):
        assert TimeTravelMode.FORK.value == "fork"


# ---------------------------------------------------------------------------
# evaluate_inspect_request
# ---------------------------------------------------------------------------

class TestEvaluateInspectRequest:
    def test_always_allowed(self):
        soul = _make_soul(allow_fork=False)
        decision = evaluate_inspect_request(soul)
        assert decision.allowed is True
        assert decision.mode == TimeTravelMode.INSPECT

    def test_allowed_even_without_fork_permissions(self):
        soul = _make_soul(has_fork_permissions=False)
        decision = evaluate_inspect_request(soul)
        assert decision.allowed is True
        assert decision.mode == TimeTravelMode.INSPECT

    def test_required_approval_tier_is_zero(self):
        soul = _make_soul()
        decision = evaluate_inspect_request(soul)
        assert decision.required_approval_tier == 0


# ---------------------------------------------------------------------------
# evaluate_replay_request
# ---------------------------------------------------------------------------

class TestEvaluateReplayRequest:
    def test_allowed_when_allow_fork_true(self):
        soul = _make_soul(allow_fork=True, require_approval_tier=2)
        decision = evaluate_replay_request(soul)
        assert decision.allowed is True
        assert decision.mode == TimeTravelMode.REPLAY
        assert decision.required_approval_tier == 2

    def test_denied_when_allow_fork_false(self):
        soul = _make_soul(allow_fork=False)
        decision = evaluate_replay_request(soul)
        assert decision.allowed is False
        assert decision.mode == TimeTravelMode.REPLAY
        assert "false" in decision.reason.lower()

    def test_denied_when_no_fork_permissions(self):
        soul = _make_soul(has_fork_permissions=False)
        decision = evaluate_replay_request(soul)
        assert decision.allowed is False
        assert decision.mode == TimeTravelMode.REPLAY
        assert "no fork_permissions" in decision.reason.lower()


# ---------------------------------------------------------------------------
# evaluate_fork_request
# ---------------------------------------------------------------------------

class TestEvaluateForkRequest:
    def test_denied_when_allow_fork_false(self):
        soul = _make_soul(allow_fork=False)
        decision = evaluate_fork_request(soul, {"inputs.query": "new"})
        assert decision.allowed is False
        assert decision.mode == TimeTravelMode.FORK

    def test_denied_when_no_fork_permissions(self):
        soul = _make_soul(has_fork_permissions=False)
        decision = evaluate_fork_request(soul, {"inputs.query": "new"})
        assert decision.allowed is False

    def test_prohibited_field_rejected(self):
        soul = _make_soul()
        decision = evaluate_fork_request(soul, {"operator_id": "evil"})
        assert decision.allowed is False
        assert "operator_id" in decision.rejected_fields

    def test_allowed_modifiable_field(self):
        soul = _make_soul(modifiable_fields=["inputs"])
        decision = evaluate_fork_request(soul, {"inputs.query": "new prompt"})
        assert decision.allowed is True
        assert decision.mode == TimeTravelMode.FORK

    def test_prefix_matching(self):
        """'inputs' in modifiable_fields should match 'inputs.text'."""
        soul = _make_soul(modifiable_fields=["inputs"])
        decision = evaluate_fork_request(soul, {"inputs.text": "hello"})
        assert decision.allowed is True

    def test_exact_field_matching(self):
        soul = _make_soul(modifiable_fields=["inputs.query"])
        decision = evaluate_fork_request(soul, {"inputs.query": "test"})
        assert decision.allowed is True

    def test_empty_modifiable_fields_rejects_all(self):
        soul = _make_soul(modifiable_fields=[])
        decision = evaluate_fork_request(soul, {"inputs.query": "x"})
        assert decision.allowed is False
        assert "inputs.query" in decision.rejected_fields

    def test_no_modifications_treated_as_replay(self):
        soul = _make_soul()
        decision = evaluate_fork_request(soul, {})
        assert decision.allowed is True
        assert "no modifications" in decision.reason.lower()

    def test_multiple_prohibited_fields_all_detected(self):
        soul = _make_soul()
        mods = {"operator_id": "a", "session_id": "b", "inputs.q": "c"}
        decision = evaluate_fork_request(soul, mods)
        assert decision.allowed is False
        assert "operator_id" in decision.rejected_fields
        assert "session_id" in decision.rejected_fields
        # inputs.q should NOT be in rejected (it's not prohibited)
        assert "inputs.q" not in decision.rejected_fields

    def test_rejected_fields_populated_correctly(self):
        soul = _make_soul(modifiable_fields=["inputs"])
        decision = evaluate_fork_request(soul, {"config.model": "gpt-5"})
        assert decision.allowed is False
        assert decision.rejected_fields == ["config.model"]

    def test_field_not_in_allowlist_rejected(self):
        soul = _make_soul(modifiable_fields=["inputs"])
        decision = evaluate_fork_request(soul, {"outputs.text": "override"})
        assert decision.allowed is False
        assert "outputs.text" in decision.rejected_fields

    def test_prohibited_root_field_blocks_nested(self):
        """If 'quest_id' is prohibited, 'quest_id' should be blocked."""
        soul = _make_soul()
        decision = evaluate_fork_request(soul, {"quest_id": "new-id"})
        assert decision.allowed is False
        assert "quest_id" in decision.rejected_fields


# ---------------------------------------------------------------------------
# Default prohibited_modifications
# ---------------------------------------------------------------------------

class TestDefaultProhibitedModifications:
    def test_defaults_include_all_identity_fields(self):
        fp = ForkPermissions()
        expected = {"operator_id", "session_id", "quest_id", "timestamp", "id"}
        assert set(fp.prohibited_modifications) == expected


# ---------------------------------------------------------------------------
# create_rejection_receipt_data
# ---------------------------------------------------------------------------

class TestCreateRejectionReceiptData:
    def test_produces_correct_structure(self):
        decision = ForkDecision(
            allowed=False,
            mode=TimeTravelMode.FORK,
            reason="Denied",
            rejected_fields=["operator_id"],
        )
        data = create_rejection_receipt_data(
            decision, quest_id="q-123", operator_id="op-1"
        )
        assert data["action_type"] == "fork_soul_rejected"
        assert data["status"] == "failure"
        assert data["quest_id"] == "q-123"
        assert data["operator_id"] == "op-1"
        assert data["inputs"]["requested_mode"] == "fork"
        assert data["metadata"]["subsystem"] == "time_travel"
        assert data["metadata"]["rejected_fields"] == ["operator_id"]

    def test_action_name_includes_mode(self):
        decision = ForkDecision(
            allowed=False,
            mode=TimeTravelMode.REPLAY,
            reason="Denied",
        )
        data = create_rejection_receipt_data(decision, quest_id="q-1")
        assert "replay" in data["action_name"]


# ---------------------------------------------------------------------------
# ForkDecision.to_dict
# ---------------------------------------------------------------------------

class TestForkDecisionToDict:
    def test_serialization(self):
        d = ForkDecision(
            allowed=True,
            mode=TimeTravelMode.FORK,
            reason="Allowed",
            rejected_fields=["a", "b"],
            required_approval_tier=2,
        )
        result = d.to_dict()
        assert result["allowed"] is True
        assert result["mode"] == "fork"
        assert result["reason"] == "Allowed"
        assert result["rejected_fields"] == ["a", "b"]
        assert result["required_approval_tier"] == 2

    def test_defaults_in_serialization(self):
        d = ForkDecision(
            allowed=False,
            mode=TimeTravelMode.INSPECT,
            reason="nope",
        )
        result = d.to_dict()
        assert result["rejected_fields"] == []
        assert result["required_approval_tier"] == 0
