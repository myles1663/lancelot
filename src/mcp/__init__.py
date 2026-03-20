# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
Governed MCP (Model Context Protocol) — Phase 1.

Every MCP tool invocation routes through the full governance stack:

    Gate 1: Soul Permission       — mcp_permissions block in active Soul
    Gate 2: Kill Switch           — FEATURE_MCP master + per-server switches
    Gate 3: Server Status         — registered, not suspended/error
    Gate 4: Network Allowlist     — endpoint domain in allowlist
    Gate 5: Argument Screening    — deep injection detection (SQL, path traversal,
                                    command injection, prompt injection, SSRF,
                                    NoSQL, size limits) + InputSanitizer
    Gate 6: Credential Resolution — vault-backed, scoped access policy
    Gate 7: MCP Execution         — HTTP+SSE transport (no stdio)
    Gate 7b: Response Guard       — credential leak scrubbing, prompt injection
                                    removal, payload size enforcement
    Gate 8: Receipt Persistence   — MANDATORY. Receipt write failure = result
                                    discarded. Ungoverned success is not allowed.

No MCP server is reachable unless explicitly permitted by the active Soul,
its kill switch is active, and its endpoint is in the network allowlist.
Fail-closed on every gate. HTTP+SSE transport only — no stdio.

Modules:
    permissions.py      — Soul MCP permission evaluator + federation ceiling
    registry.py         — Vault-backed server config store
    kill_switches.py    — Master + per-server kill switches
    receipts.py         — MCP receipt manager (mandatory for proxy)
    argument_screen.py  — Deep injection pattern detection
    network_policy.py   — Endpoint validation + allowlist management
    response_guard.py   — Response scrubbing (credential/injection/size)
    client.py           — HTTP+SSE MCP protocol client
    proxy.py            — Governed proxy orchestrator (all gates)
"""
