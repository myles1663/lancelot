# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Soul version handshake protocol."""

import time
import pytest
from src.federation.soul_handshake import (
    HandshakeState,
    SoulPushResult,
    SoulVersionHandshake,
    check_timeouts,
    create_handshakes,
    evaluate_push_result,
    process_response,
)


class TestCreateHandshakes:
    def test_creates_for_each_peer(self):
        hs_list = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a", "peer-b", "peer-c"],
            old_soul_hash="oldhash",
            new_soul_hash="newhash",
        )
        assert len(hs_list) == 3
        targets = {h.target_instance_id for h in hs_list}
        assert targets == {"peer-a", "peer-b", "peer-c"}

    def test_all_initiated(self):
        hs_list = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a"],
            old_soul_hash="old",
            new_soul_hash="new",
        )
        assert all(h.state == HandshakeState.INITIATED for h in hs_list)
        assert all(not h.is_terminal() for h in hs_list)

    def test_fields_set(self):
        hs = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a"],
            old_soul_hash="old123",
            new_soul_hash="new456",
            timeout_s=10.0,
        )[0]
        assert hs.initiator_instance_id == "inst-1"
        assert hs.old_soul_hash == "old123"
        assert hs.new_soul_hash == "new456"
        assert hs.timeout_at is not None

    def test_empty_peers(self):
        hs_list = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=[],
            old_soul_hash="old",
            new_soul_hash="new",
        )
        assert len(hs_list) == 0


class TestProcessResponse:
    def test_acknowledge(self):
        hs = SoulVersionHandshake(
            initiator_instance_id="inst-1",
            target_instance_id="peer-a",
        )
        result = process_response(hs, HandshakeState.ACKNOWLEDGED)
        assert result.state == HandshakeState.ACKNOWLEDGED
        assert result.is_terminal()
        assert result.is_success()

    def test_reject(self):
        hs = SoulVersionHandshake(
            initiator_instance_id="inst-1",
            target_instance_id="peer-a",
        )
        result = process_response(
            hs, HandshakeState.REJECTED, reason="incompatible soul",
        )
        assert result.state == HandshakeState.REJECTED
        assert result.is_terminal()
        assert not result.is_success()
        assert result.reason_if_rejected == "incompatible soul"

    def test_governance_denial(self):
        hs = SoulVersionHandshake(
            initiator_instance_id="inst-1",
            target_instance_id="peer-a",
        )
        result = process_response(hs, HandshakeState.GOVERNANCE_DENIAL)
        assert result.state == HandshakeState.GOVERNANCE_DENIAL
        assert result.is_terminal()

    def test_ignores_double_response(self):
        hs = SoulVersionHandshake(
            initiator_instance_id="inst-1",
            target_instance_id="peer-a",
        )
        process_response(hs, HandshakeState.ACKNOWLEDGED)
        # Second response should be ignored
        result = process_response(hs, HandshakeState.REJECTED)
        assert result.state == HandshakeState.ACKNOWLEDGED  # Unchanged

    def test_peer_execution_state(self):
        hs = SoulVersionHandshake(
            initiator_instance_id="inst-1",
            target_instance_id="peer-a",
        )
        state = {"active_tasks": 3, "hive_agents": 2}
        result = process_response(
            hs, HandshakeState.ACKNOWLEDGED,
            peer_execution_state=state,
        )
        assert result.target_execution_state == state


class TestCheckTimeouts:
    def test_timeout_expired(self):
        hs = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a"],
            old_soul_hash="old",
            new_soul_hash="new",
            timeout_s=0.0,  # Immediately expires
        )[0]
        # Small sleep to ensure we're past the timeout
        time.sleep(0.01)
        timed_out = check_timeouts([hs])
        assert len(timed_out) == 1
        assert hs.state == HandshakeState.TIMEOUT

    def test_not_timed_out(self):
        hs = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a"],
            old_soul_hash="old",
            new_soul_hash="new",
            timeout_s=60.0,
        )[0]
        timed_out = check_timeouts([hs])
        assert len(timed_out) == 0
        assert hs.state == HandshakeState.INITIATED

    def test_already_terminal_skipped(self):
        hs = SoulVersionHandshake(
            initiator_instance_id="inst-1",
            target_instance_id="peer-a",
            timeout_at="2020-01-01T00:00:00+00:00",  # Long ago
        )
        process_response(hs, HandshakeState.ACKNOWLEDGED)
        timed_out = check_timeouts([hs])
        assert len(timed_out) == 0
        assert hs.state == HandshakeState.ACKNOWLEDGED


class TestEvaluatePushResult:
    def test_all_acknowledged(self):
        hs_list = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a", "peer-b"],
            old_soul_hash="old",
            new_soul_hash="new",
        )
        for hs in hs_list:
            process_response(hs, HandshakeState.ACKNOWLEDGED)
        result = evaluate_push_result(hs_list, "new")
        assert result.all_acknowledged
        assert result.governance_gaps == []

    def test_some_rejected(self):
        hs_list = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a", "peer-b"],
            old_soul_hash="old",
            new_soul_hash="new",
        )
        process_response(hs_list[0], HandshakeState.ACKNOWLEDGED)
        process_response(hs_list[1], HandshakeState.REJECTED)
        result = evaluate_push_result(hs_list, "new")
        assert not result.all_acknowledged
        assert "peer-b" in result.governance_gaps

    def test_to_dict(self):
        hs_list = create_handshakes(
            initiator_instance_id="inst-1",
            target_instance_ids=["peer-a"],
            old_soul_hash="old",
            new_soul_hash="new",
        )
        process_response(hs_list[0], HandshakeState.ACKNOWLEDGED)
        result = evaluate_push_result(hs_list, "new")
        d = result.to_dict()
        assert d["total_peers"] == 1
        assert d["acknowledged"] == 1
        assert d["all_acknowledged"] is True

    def test_empty_handshakes(self):
        result = evaluate_push_result([], "new")
        assert not result.all_acknowledged
        assert result.governance_gaps == []


class TestSoulVersionHandshake:
    def test_to_dict(self):
        hs = SoulVersionHandshake(
            initiator_instance_id="inst-1",
            target_instance_id="peer-a",
            old_soul_hash="old",
            new_soul_hash="new",
        )
        d = hs.to_dict()
        assert d["initiator_instance_id"] == "inst-1"
        assert d["state"] == "initiated"
