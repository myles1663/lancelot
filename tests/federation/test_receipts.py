# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federation receipts helper and receipt manager."""

import os
import tempfile
import pytest
from src.shared.receipts import ActionType, get_receipt_service
from src.federation.receipts import emit_federation_receipt, _FEDERATION_ACTION_TYPES
from src.federation.receipt_manager import FederationReceiptManager


class TestFederationActionTypes:
    """Verify federation ActionType enum values exist."""

    def test_all_federation_types_in_enum(self):
        assert ActionType.FEDERATION_HEARTBEAT_EVENT.value == "federation_heartbeat_event"
        assert ActionType.FEDERATION_IDENTITY_EVENT.value == "federation_identity_event"
        assert ActionType.FEDERATION_TOPOLOGY_EVENT.value == "federation_topology_event"
        assert ActionType.FEDERATION_HANDOFF_EVENT.value == "federation_handoff_event"
        assert ActionType.FEDERATION_SOUL_EVENT.value == "federation_soul_event"
        assert ActionType.FEDERATION_BUDGET_EVENT.value == "federation_budget_event"

    def test_action_type_mapping(self):
        assert len(_FEDERATION_ACTION_TYPES) == 6
        for key in ["heartbeat", "identity", "topology", "handoff", "soul", "budget"]:
            assert key in _FEDERATION_ACTION_TYPES


class TestEmitFederationReceipt:
    """Test the emit_federation_receipt helper."""

    @pytest.fixture
    def data_dir(self):
        return tempfile.mkdtemp()

    def test_emit_basic_receipt(self, data_dir):
        receipt = emit_federation_receipt(
            event_type="heartbeat",
            action_name="heartbeat_emitted",
            inputs={"deployment_mode": "standalone"},
            instance_id="test-instance-123",
            data_dir=data_dir,
        )
        assert receipt.id
        assert receipt.action_type == "federation_heartbeat_event"
        assert receipt.action_name == "heartbeat_emitted"
        assert receipt.metadata["federation_subsystem"] == "heartbeat"
        assert receipt.metadata["instance_id"] == "test-instance-123"
        persisted = get_receipt_service(data_dir).get(receipt.id)
        assert persisted is not None
        assert persisted.status == "success"

    def test_emit_with_federation_fields(self, data_dir):
        receipt = emit_federation_receipt(
            event_type="handoff",
            action_name="handoff_initiated",
            inputs={"target": "peer-456"},
            instance_id="inst-123",
            federation_quest_id="fq-789",
            handoff_id="ho-001",
            soul_version_hash="abc123",
            data_dir=data_dir,
        )
        assert receipt.metadata["instance_id"] == "inst-123"
        assert receipt.metadata["federation_quest_id"] == "fq-789"
        assert receipt.metadata["handoff_id"] == "ho-001"
        assert receipt.metadata["soul_version_hash"] == "abc123"

    def test_emit_invalid_event_type_raises(self, data_dir):
        with pytest.raises(ValueError, match="Unknown federation event type"):
            emit_federation_receipt(
                event_type="invalid",
                action_name="test",
                inputs={},
                data_dir=data_dir,
            )

    def test_emit_each_event_type(self, data_dir):
        for event_type in _FEDERATION_ACTION_TYPES:
            receipt = emit_federation_receipt(
                event_type=event_type,
                action_name=f"test_{event_type}",
                inputs={"test": True},
                data_dir=data_dir,
            )
            assert receipt.action_type == f"federation_{event_type}_event"


class TestFederationReceiptManager:
    """Test typed receipt manager methods."""

    @pytest.fixture
    def manager(self):
        tmpdir = tempfile.mkdtemp()
        yield FederationReceiptManager(
            instance_id="test-mgr-instance",
            data_dir=tmpdir,
        )

    # ── Heartbeat ──

    def test_record_heartbeat_emitted(self, manager):
        rid = manager.record_heartbeat_emitted(
            soul_version_hash="hash123",
            deployment_mode="federated",
            peer_count=3,
        )
        assert rid  # UUID string

    def test_record_staleness_detected(self, manager):
        rid = manager.record_staleness_detected(
            peer_instance_id="peer-1",
            staleness_level="warning",
            age_seconds=15.5,
        )
        assert rid

    # ── Identity ──

    def test_record_identity_generated(self, manager):
        rid = manager.record_identity_generated(fingerprint="abc123def456")
        assert rid

    def test_record_identity_loaded(self, manager):
        rid = manager.record_identity_loaded(fingerprint="abc123def456")
        assert rid

    # ── Topology ──

    def test_record_peer_registered(self, manager):
        rid = manager.record_peer_registered(
            peer_instance_id="peer-2",
            peer_fingerprint="fp-peer2",
            peer_address="192.168.1.50:8000",
        )
        assert rid

    def test_record_peer_removed(self, manager):
        rid = manager.record_peer_removed(
            peer_instance_id="peer-2",
            reason="lost connectivity",
        )
        assert rid

    def test_record_topology_change(self, manager):
        rid = manager.record_topology_change(
            old_mode="standalone",
            new_mode="federated",
            peer_count=2,
        )
        assert rid

    # ── Handoff ──

    def test_record_handoff_initiated(self, manager):
        rid = manager.record_handoff_initiated(
            handoff_id="ho-001",
            target_instance_id="target-instance",
            workflow_summary="process invoice",
            federation_quest_id="fq-100",
        )
        assert rid

    def test_record_handoff_received(self, manager):
        rid = manager.record_handoff_received(
            handoff_id="ho-001",
            source_instance_id="source-instance",
            federation_quest_id="fq-100",
        )
        assert rid

    def test_record_handoff_rejected(self, manager):
        rid = manager.record_handoff_rejected(
            handoff_id="ho-001",
            source_instance_id="source-instance",
            reason="soul incompatible",
            federation_quest_id="fq-100",
        )
        assert rid

    # ── Soul ──

    def test_record_soul_version_push(self, manager):
        rid = manager.record_soul_version_push(
            soul_version_hash="soul-v2-hash",
            target_instance_ids=["child-1", "child-2"],
        )
        assert rid

    def test_record_soul_handshake_ack(self, manager):
        rid = manager.record_soul_handshake_ack(
            parent_instance_id="parent-instance",
            soul_version_hash="soul-v2-hash",
            compatible=True,
        )
        assert rid

    def test_record_divergence(self, manager):
        rid = manager.record_divergence(
            peer_instance_id="peer-3",
            staleness_seconds=45.0,
            soul_version_hash="hash-at-divergence",
        )
        assert rid

    def test_record_reconnection(self, manager):
        rid = manager.record_reconnection(
            peer_instance_id="peer-3",
            divergence_duration_s=120.5,
            reconciliation_result="compatible",
        )
        assert rid

    # ── Budget ──

    def test_record_spawn_receipt(self, manager):
        rid = manager.record_spawn_receipt(
            agent_id="agent-001",
            model_tier="flagship_fast",
            estimated_cost=0.05,
            federation_quest_id="fq-200",
        )
        assert rid

    def test_record_budget_threshold(self, manager):
        rid = manager.record_budget_threshold(
            threshold_level="T2",
            utilization_pct=85.0,
            action_taken="restricted new spawns",
        )
        assert rid
