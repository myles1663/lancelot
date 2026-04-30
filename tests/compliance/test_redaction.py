# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Unit tests for the Compliance Redaction module.

Tests verify IP address removal, nested dict redaction, list handling,
pre-identity-migration flagging, and idempotency.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "core"))

import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass, field, asdict

from src.compliance.redaction import (
    redact_receipt,
    redact_dict,
    redact_receipts,
    is_pre_identity_migration,
    PRE_IDENTITY_MIGRATION_NOTE,
)
from src.shared.receipts import Receipt, ActionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_receipt(**overrides):
    """Create a Receipt with sensible defaults, applying overrides."""
    defaults = dict(
        id="r-001",
        timestamp="2026-01-15T00:00:00Z",
        action_type=ActionType.KILL_SWITCH_ISSUED.value,
        action_name="kill_switch",
        inputs={"target": "agent-1"},
        outputs={"result": "ok"},
        status="success",
        operator_id="op-001",
        session_id="sess-001",
        metadata={},
    )
    defaults.update(overrides)
    return Receipt(**defaults)


# ---------------------------------------------------------------------------
# IP Address Redaction
# ---------------------------------------------------------------------------

class TestIpAddressRedaction:
    def test_ip_address_removed_from_top_level(self):
        data = {"id": "r1", "ip_address": "192.168.1.1", "action_type": "system"}
        result = redact_dict(data)
        assert "ip_address" not in result
        assert result["id"] == "r1"

    def test_ip_address_removed_from_metadata(self):
        receipt = _make_receipt(
            metadata={"ip_address": "10.0.0.1", "browser": "Chrome"}
        )
        result = redact_receipt(receipt)
        assert "ip_address" not in result.get("metadata", {})
        assert result["metadata"]["browser"] == "Chrome"

    def test_ip_address_removed_from_nested_dict(self):
        data = {
            "id": "r1",
            "details": {
                "ip_address": "172.16.0.1",
                "user_agent": "Mozilla",
                "inner": {"ip_address": "10.0.0.5", "value": 42},
            },
        }
        result = redact_dict(data)
        assert "ip_address" not in result["details"]
        assert "ip_address" not in result["details"]["inner"]
        assert result["details"]["inner"]["value"] == 42

    def test_multiple_ip_addresses_in_same_dict(self):
        data = {
            "ip_address": "1.1.1.1",
            "metadata": {"ip_address": "2.2.2.2"},
            "inputs": {"ip_address": "3.3.3.3", "tool": "ping"},
        }
        result = redact_dict(data)
        assert "ip_address" not in result
        assert "ip_address" not in result["metadata"]
        assert "ip_address" not in result["inputs"]
        assert result["inputs"]["tool"] == "ping"

    def test_ip_address_removed_from_list_of_dicts(self):
        data = {
            "entries": [
                {"ip_address": "10.0.0.1", "name": "a"},
                {"ip_address": "10.0.0.2", "name": "b"},
            ]
        }
        result = redact_dict(data)
        for entry in result["entries"]:
            assert "ip_address" not in entry
        assert result["entries"][0]["name"] == "a"
        assert result["entries"][1]["name"] == "b"

    def test_non_dict_list_items_preserved(self):
        data = {"tags": ["admin", "operator"], "id": "r1"}
        result = redact_dict(data)
        assert result["tags"] == ["admin", "operator"]


# ---------------------------------------------------------------------------
# Pre-Identity Migration Flagging
# ---------------------------------------------------------------------------

class TestPreIdentityMigration:
    def test_null_operator_id_with_action_type_flagged(self):
        data = {
            "operator_id": None,
            "action_type": "kill_switch_issued",
            "id": "r1",
        }
        result = redact_dict(data)
        assert result.get("pre_identity_migration") is True
        assert result.get("pre_identity_migration_note") == PRE_IDENTITY_MIGRATION_NOTE

    def test_post_migration_receipt_not_flagged(self):
        data = {
            "operator_id": "op-001",
            "action_type": "kill_switch_issued",
            "id": "r1",
        }
        result = redact_dict(data)
        assert "pre_identity_migration" not in result

    def test_null_operator_without_action_type_not_flagged(self):
        """Receipts without action_type (non-governance) should not be flagged."""
        data = {"operator_id": None, "id": "r1", "status": "success"}
        result = redact_dict(data)
        assert "pre_identity_migration" not in result

    def test_is_pre_identity_migration_helper(self):
        pre = _make_receipt(operator_id=None)
        post = _make_receipt(operator_id="op-001")
        assert is_pre_identity_migration(pre) is True
        assert is_pre_identity_migration(post) is False


# ---------------------------------------------------------------------------
# Preserving Non-Sensitive Fields
# ---------------------------------------------------------------------------

class TestFieldPreservation:
    def test_all_non_sensitive_fields_preserved(self):
        receipt = _make_receipt(
            metadata={"browser": "Chrome", "version": "120"}
        )
        result = redact_receipt(receipt)
        assert result["id"] == "r-001"
        assert result["action_type"] == ActionType.KILL_SWITCH_ISSUED.value
        assert result["action_name"] == "kill_switch"
        assert result["operator_id"] == "op-001"
        assert result["metadata"]["browser"] == "Chrome"

    def test_empty_metadata_handled(self):
        receipt = _make_receipt(metadata={})
        result = redact_receipt(receipt)
        assert result["metadata"] == {}

    def test_empty_inputs_and_outputs(self):
        receipt = _make_receipt(inputs={}, outputs={})
        result = redact_receipt(receipt)
        assert result["inputs"] == {}
        assert result["outputs"] == {}


# ---------------------------------------------------------------------------
# Batch Redaction
# ---------------------------------------------------------------------------

class TestBatchRedaction:
    def test_redact_receipts_processes_list(self):
        receipts = [
            _make_receipt(id="r1", metadata={"ip_address": "1.1.1.1"}),
            _make_receipt(id="r2", metadata={"ip_address": "2.2.2.2"}),
        ]
        results = redact_receipts(receipts)
        assert len(results) == 2
        for r in results:
            assert "ip_address" not in r.get("metadata", {})

    def test_redact_empty_list(self):
        assert redact_receipts([]) == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_redact_twice_same_result(self):
        data = {
            "id": "r1",
            "ip_address": "10.0.0.1",
            "action_type": "system",
            "operator_id": None,
            "metadata": {"ip_address": "172.16.0.1", "tag": "test"},
        }
        first = redact_dict(data)
        second = redact_dict(first)
        assert first == second
