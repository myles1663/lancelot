#!/usr/bin/env python3
"""Test script: Federation peer registration + handoff test.

Run inside the ROOT container:
    python /tmp/test_federation_handshake.py
"""

import asyncio
import json
import sys
import os

# Add src paths
sys.path.insert(0, "/home/lancelot/app/src")
sys.path.insert(0, "/home/lancelot/app/src/core")


async def main():
    from src.federation.api import (
        _identity, _topology_registry, _peer_protocol,
        _handoff_protocol, _command_relay,
    )

    if not _identity:
        print("ERROR: Federation not initialized on this instance!")
        return False

    print(f"ROOT instance: {_identity.instance_id}")
    print(f"ROOT fingerprint: {_identity.fingerprint}")
    print(f"ROOT public key: {_identity.public_key_hex()}")
    print()

    # --- Step 1: Register peer ---
    print("=" * 60)
    print("STEP 1: Initiating peer registration handshake")
    print("=" * 60)

    PEER_ADDRESS = "http://lancelot_peer:8000"

    # First, verify we can reach the peer
    import httpx
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{PEER_ADDRESS}/api/federation/identity", timeout=5.0)
            peer_info = resp.json()
            print(f"PEER instance: {peer_info['instance_id']}")
            print(f"PEER fingerprint: {peer_info['fingerprint']}")
            print(f"PEER public key: {peer_info['public_key']}")
            print()
        except Exception as e:
            print(f"ERROR: Cannot reach peer at {PEER_ADDRESS}: {e}")
            return False

    # Initiate registration
    result = await _peer_protocol.initiate_registration(
        target_address=PEER_ADDRESS,
        target_role="child",
    )

    print(f"Registration result:")
    print(f"  success: {result.success}")
    print(f"  peer_instance_id: {result.peer_instance_id}")
    print(f"  peer_fingerprint: {result.peer_fingerprint}")
    print(f"  mutual: {result.mutual}")
    if result.error:
        print(f"  error: {result.error}")
    print()

    if not result.success:
        print("FAILED: Peer registration did not succeed.")
        return False

    # Verify both sides see each other
    print("=" * 60)
    print("STEP 2: Verifying topology on both sides")
    print("=" * 60)

    root_peers = _topology_registry.list_peers()
    print(f"ROOT sees {len(root_peers)} peer(s):")
    for p in root_peers:
        print(f"  - {p.instance_id} (role={p.role}, fingerprint={p.fingerprint[:8]})")

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{PEER_ADDRESS}/api/federation/status", timeout=5.0)
        peer_status = resp.json()
        print(f"PEER sees {peer_status['peer_count']} peer(s)")
        print(f"PEER deployment mode: {peer_status['deployment_mode']}")

    print()

    # --- Step 3: Test handoff ---
    print("=" * 60)
    print("STEP 3: Initiating task handoff ROOT → PEER")
    print("=" * 60)

    if not _handoff_protocol:
        print("ERROR: Handoff protocol not available!")
        return False

    handoff_result = await _handoff_protocol.initiate_handoff(
        target_instance_id=result.peer_instance_id,
        task_context={
            "goal": "Analyze system logs for anomalies",
            "priority": "high",
            "constraints": ["read-only access", "max 5 minutes"],
            "data_source": "/var/log/syslog",
        },
        soul_context={
            "parent_soul_version": "v1",
            "restrictions": ["no-network-access", "no-file-writes"],
        },
        contract={
            "success_criteria": [
                "Anomalies identified with severity ratings",
                "Summary report generated",
            ],
            "timeout_s": 300,
            "max_actions": 50,
        },
        federation_quest_id="quest-log-analysis-001",
    )

    print(f"Handoff result:")
    print(f"  success: {handoff_result.get('success', False)}")
    print(f"  handoff_id: {handoff_result.get('handoff_id', 'N/A')}")
    if handoff_result.get("error"):
        print(f"  error: {handoff_result['error']}")
    print()

    if not handoff_result.get("success"):
        print("FAILED: Handoff did not succeed.")
        # Print the full result for debugging
        print(f"Full result: {json.dumps(handoff_result, indent=2)}")
        return False

    # Verify handoff is tracked on the peer
    async with httpx.AsyncClient() as client:
        handoff_id = handoff_result["handoff_id"]
        resp = await client.get(
            f"{PEER_ADDRESS}/api/federation/handoff/status/{handoff_id}",
            timeout=5.0,
        )
        if resp.status_code == 200:
            peer_handoff = resp.json()
            print(f"PEER handoff status:")
            print(f"  state: {peer_handoff.get('state', 'unknown')}")
            print(f"  quest_id: {peer_handoff.get('federation_quest_id', 'N/A')}")
        else:
            print(f"PEER handoff status check returned {resp.status_code}: {resp.text}")

    print()

    # --- Step 4: Report handoff completion ---
    print("=" * 60)
    print("STEP 4: Reporting handoff completion")
    print("=" * 60)

    completion_result = await _handoff_protocol.report_completion(
        handoff_id=handoff_result["handoff_id"],
        target_instance_id=result.peer_instance_id,
        result_data={
            "status": "success",
            "findings": [
                {"severity": "warning", "message": "High memory usage at 14:23"},
                {"severity": "info", "message": "3 failed SSH attempts from 10.0.0.5"},
            ],
            "summary": "2 anomalies found: 1 warning (memory), 1 info (SSH).",
        },
        federation_quest_id="quest-log-analysis-001",
    )

    print(f"Completion result:")
    print(f"  success: {completion_result.get('success', False)}")
    if completion_result.get("error"):
        print(f"  error: {completion_result['error']}")
    print()

    # --- Step 5: Test kill command propagation ---
    print("=" * 60)
    print("STEP 5: Testing kill command propagation ROOT → PEER")
    print("=" * 60)

    if not _command_relay:
        print("SKIP: Command relay not available")
    else:
        kill_results = await _command_relay.propagate_kill(
            command={
                "command_id": "kill-test-001",
                "command_type": "emergency_stop",
                "authority": "L1",
                "reason": "Federation handshake test — kill propagation check",
            },
            target_ids=[result.peer_instance_id],
        )
        print(f"Kill propagation results: {len(kill_results)} target(s)")
        for kr in kill_results:
            print(f"  - {kr.get('peer_id', 'unknown')}: success={kr.get('success', False)}")
            if kr.get("error"):
                print(f"    error: {kr['error']}")

    print()
    print("=" * 60)
    print("ALL TESTS PASSED" if True else "SOME TESTS FAILED")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
