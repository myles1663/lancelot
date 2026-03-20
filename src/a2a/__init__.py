# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
A2A Protocol — Governed Agent-to-Agent Interoperability.

Implements Google's A2A protocol with full governance: Soul evaluation,
risk tier classification, Network Allowlist enforcement, receipt generation,
and kill switch governance for both inbound and outbound directions.

Every cross-agent interaction is receipted, attributed, and killable.

Feature-gated: FEATURE_A2A (default: false)
Runtime kill switch: A2A_ALL (blocks all A2A traffic when deactivated)
Per-agent kill switches: A2A_[AGENT_ID]
"""
