# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.
# Patent Pending: US Provisional Application #63/982,183

"""Tests for Federated Kill Switch Engine."""

from pathlib import Path

import pytest
from src.federation.kill_switch import (
    FederatedKillSwitch,
    KillAuthority,
    KillCommandState,
    KillCommandType,
    PropagationAck,
)


@pytest.fixture
def engine():
    return FederatedKillSwitch(
        self_instance_id="self-001",
        peer_ids=["peer-a", "peer-b"],
        local_kill_handler=lambda reason: 3,  # Pretend 3 agents killed
    )


class TestAuthorityValidation:
    def test_l1_can_do_everything(self, engine):
        for ct in KillCommandType:
            ok, _ = engine.validate_authority(KillAuthority.L1_FEDERATION_ROOT, ct)
            assert ok

    def test_l2_local_kill(self, engine):
        ok, _ = engine.validate_authority(
            KillAuthority.L2_LOCAL_INSTANCE, KillCommandType.LOCAL_KILL
        )
        assert ok

    def test_l2_cannot_federation_kill(self, engine):
        ok, _ = engine.validate_authority(
            KillAuthority.L2_LOCAL_INSTANCE, KillCommandType.FEDERATION_KILL
        )
        assert not ok

    def test_l2_targeted_self_only(self, engine):
        ok, _ = engine.validate_authority(
            KillAuthority.L2_LOCAL_INSTANCE,
            KillCommandType.TARGETED_KILL,
            target_instance_id="self-001",
        )
        assert ok

    def test_l2_targeted_remote_blocked(self, engine):
        ok, _ = engine.validate_authority(
            KillAuthority.L2_LOCAL_INSTANCE,
            KillCommandType.TARGETED_KILL,
            target_instance_id="peer-a",
        )
        assert not ok

    def test_l3_can_cascade(self, engine):
        ok, _ = engine.validate_authority(
            KillAuthority.L3_AUTOMATED, KillCommandType.CASCADING_KILL
        )
        assert ok

    def test_l3_cannot_feature_kill(self, engine):
        ok, _ = engine.validate_authority(
            KillAuthority.L3_AUTOMATED, KillCommandType.FEATURE_KILL
        )
        assert not ok


class TestIssueCommand:
    def test_local_kill(self, engine):
        cmd = engine.issue_command(
            "cmd-1", KillCommandType.LOCAL_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test reason",
        )
        assert cmd.state == KillCommandState.PROPAGATING
        assert len(cmd.targets) == 1
        assert cmd.targets[0].instance_id == "self-001"

    def test_federation_kill(self, engine):
        cmd = engine.issue_command(
            "cmd-2", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "emergency",
        )
        assert len(cmd.targets) == 3  # self + 2 peers

    def test_empty_reason_raises(self, engine):
        with pytest.raises(ValueError, match="reason"):
            engine.issue_command(
                "cmd-3", KillCommandType.LOCAL_KILL,
                KillAuthority.L1_FEDERATION_ROOT,
                "self-001", "",
            )

    def test_unauthorized_raises(self, engine):
        with pytest.raises(ValueError):
            engine.issue_command(
                "cmd-4", KillCommandType.FEDERATION_KILL,
                KillAuthority.L2_LOCAL_INSTANCE,
                "self-001", "reason",
            )


class TestPropagation:
    def test_local_propagation(self, engine):
        engine.issue_command(
            "cmd-1", KillCommandType.LOCAL_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        killed = engine.propagate_local("cmd-1")
        assert killed == 3
        cmd = engine.get_command("cmd-1")
        assert cmd.state == KillCommandState.COMPLETED

    def test_federation_partial(self, engine):
        engine.issue_command(
            "cmd-1", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        engine.propagate_local("cmd-1")
        # Only local acked — peer-a and peer-b still pending
        cmd = engine.get_command("cmd-1")
        assert cmd.state == KillCommandState.PROPAGATING

    def test_full_ack_completes(self, engine):
        engine.issue_command(
            "cmd-1", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        engine.propagate_local("cmd-1")
        engine.record_ack("cmd-1", "peer-a", 2)
        engine.record_ack("cmd-1", "peer-b", 1)
        cmd = engine.get_command("cmd-1")
        assert cmd.state == KillCommandState.COMPLETED

    def test_rejection_partial(self, engine):
        engine.issue_command(
            "cmd-1", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        engine.propagate_local("cmd-1")
        engine.record_ack("cmd-1", "peer-a", 2)
        engine.record_rejection("cmd-1", "peer-b", "unauthorized")
        cmd = engine.get_command("cmd-1")
        assert cmd.state == KillCommandState.PARTIAL

    def test_timeout(self, engine):
        cmd = engine.issue_command(
            "cmd-1", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
            timeout_seconds=0,  # Instant timeout
        )
        timed_out = engine.check_timeouts("cmd-1")
        assert len(timed_out) == 3  # All targets

    def test_get_command_sweeps_timeouts(self, engine):
        engine.issue_command(
            "cmd-sweep", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
            timeout_seconds=0,
        )
        cmd = engine.get_command("cmd-sweep")
        assert cmd.state == KillCommandState.FAILED
        assert all(target.ack_state != PropagationAck.PENDING for target in cmd.targets)

    def test_local_kill_failure_rejects_local_target(self):
        engine = FederatedKillSwitch(
            self_instance_id="self-001",
            peer_ids=["peer-a"],
            local_kill_handler=lambda reason: (_ for _ in ()).throw(RuntimeError("kill failed")),
        )
        engine.issue_command(
            "cmd-fail", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )

        killed = engine.propagate_local("cmd-fail")

        assert killed == 0
        cmd = engine.get_command("cmd-fail")
        local = next(t for t in cmd.targets if t.instance_id == "self-001")
        assert local.ack_state == PropagationAck.REJECTED
        assert "kill failed" in local.reject_reason
        assert cmd.state == KillCommandState.PROPAGATING


class TestLift:
    def test_lift_completed(self, engine):
        engine.issue_command(
            "cmd-1", KillCommandType.LOCAL_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        engine.propagate_local("cmd-1")
        assert engine.lift_kill("cmd-1", "admin", "reviewed and safe")
        cmd = engine.get_command("cmd-1")
        assert cmd.state == KillCommandState.LIFTED

    def test_lift_requires_notes(self, engine):
        engine.issue_command(
            "cmd-1", KillCommandType.LOCAL_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        engine.propagate_local("cmd-1")
        with pytest.raises(ValueError, match="review notes"):
            engine.lift_kill("cmd-1", "admin", "")

    def test_lift_nonexistent(self, engine):
        assert not engine.lift_kill("nope", "admin", "notes")


class TestQueries:
    def test_get_active_commands(self, engine):
        engine.issue_command(
            "cmd-1", KillCommandType.LOCAL_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        assert len(engine.get_active_commands()) == 1
        engine.propagate_local("cmd-1")
        engine.lift_kill("cmd-1", "admin", "ok")
        assert len(engine.get_active_commands()) == 0

    def test_to_dict(self, engine):
        cmd = engine.issue_command(
            "cmd-1", KillCommandType.LOCAL_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "self-001", "test",
        )
        d = cmd.to_dict()
        assert d["command_id"] == "cmd-1"
        assert d["command_type"] == "local_kill"


class TestPersistence:
    def test_commands_survive_restart(self, tmp_path: Path):
        persistence_path = tmp_path / "kill_commands.json"
        engine = FederatedKillSwitch(
            self_instance_id="self-001",
            peer_ids=["peer-a", "peer-b"],
            local_kill_handler=lambda reason: 3,
            persistence_path=str(persistence_path),
        )
        engine.issue_command(
            "cmd-1", KillCommandType.FEDERATION_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "root-001", "test",
        )
        engine.propagate_local("cmd-1")
        engine.record_ack("cmd-1", "peer-a", 2)

        reloaded = FederatedKillSwitch(
            self_instance_id="self-001",
            peer_ids=["peer-a", "peer-b"],
            local_kill_handler=lambda reason: 3,
            persistence_path=str(persistence_path),
        )

        cmd = reloaded.get_command("cmd-1")
        assert cmd is not None
        assert cmd.state == KillCommandState.PROPAGATING
        assert any(t.instance_id == "peer-a" and t.ack_state == PropagationAck.ACKNOWLEDGED for t in cmd.targets)
        assert any(t.instance_id == "peer-b" and t.ack_state == PropagationAck.PENDING for t in cmd.targets)

    def test_lifted_state_persists(self, tmp_path: Path):
        persistence_path = tmp_path / "kill_commands.json"
        engine = FederatedKillSwitch(
            self_instance_id="self-001",
            peer_ids=[],
            local_kill_handler=lambda reason: 1,
            persistence_path=str(persistence_path),
        )
        engine.issue_command(
            "cmd-2", KillCommandType.LOCAL_KILL,
            KillAuthority.L1_FEDERATION_ROOT,
            "root-001", "test",
        )
        engine.propagate_local("cmd-2")
        assert engine.lift_kill("cmd-2", "admin", "reviewed")

        reloaded = FederatedKillSwitch(
            self_instance_id="self-001",
            peer_ids=[],
            local_kill_handler=lambda reason: 1,
            persistence_path=str(persistence_path),
        )

        cmd = reloaded.get_command("cmd-2")
        assert cmd is not None
        assert cmd.state == KillCommandState.LIFTED
