# Lancelot Tool Fabric Blueprint

**Document Version:** 1.0
**Status:** In Progress
**Last Updated:** 2026-02-05
**Spec Reference:** docs/specs/Lancelot_ToolFabric_Spec.md

---

## 1. Build Order Overview

### Round 1 — Milestones

1. Foundation types + receipts
2. LocalSandboxProvider MVP
3. Policy engine
4. Router + health
5. Orchestrator wiring
6. RepoOps + FileOps
7. UIBuilder templates
8. Antigravity providers
9. War Room panel
10. Hardening & resilience

---

## 2. Round 2 — Fine-Grained Steps (Prompts)

### Prompt 1 — Contracts + Receipts (Foundation)

**Files:**
- `src/tools/contracts.py`
- `src/tools/receipts.py`
- `tests/test_tool_contracts.py`

**Deliverables:**
- Capability interfaces (Protocol classes)
- ExecResult, ProviderHealth, ToolIntent types
- ToolReceipt extension schema
- Receipt builders for tool calls
- Unit tests for schema validation and serialization

**Acceptance:**
- No runtime behavior change yet
- All tests pass
- Types importable without error

---

### Prompt 2 — LocalSandboxProvider MVP

**Files:**
- `src/tools/providers/local_sandbox.py`
- `tests/test_local_sandbox.py`

**Deliverables:**
- Docker runner wrapper implementation
- ShellExec capability via sandbox
- Workspace mount, timeout, bounded output
- Integration tests (Docker required)

**Acceptance:**
- `run("git --version")` returns version string
- `run("python -c 'print(1)'")` returns "1"
- No host execution occurs

---

### Prompt 3 — Policies

**Files:**
- `src/tools/policies.py`
- `tests/test_tool_policies.py`

**Deliverables:**
- RiskLevel enum (LOW, MEDIUM, HIGH)
- PolicyEngine class
- Command allowlist/denylist evaluation
- Network policy enforcement
- Path traversal detection

**Acceptance:**
- `rm -rf /` blocked
- `../../etc/passwd` blocked
- Network denied by default

---

### Prompt 4 — Router + Health

**Files:**
- `src/tools/router.py`
- `src/tools/health.py`
- `tests/test_tool_router.py`

**Deliverables:**
- ProviderRouter with capability→provider mapping
- Failover logic
- Health probe system
- Provider state tracking (HEALTHY/DEGRADED/OFFLINE)

**Acceptance:**
- Router selects LocalSandbox when healthy
- Router fails over when provider unhealthy
- Health probes run on startup

---

### Prompt 5 — Orchestrator Wiring

**Files:**
- `src/tools/fabric.py`
- `src/core/orchestrator.py` (modify)
- `tests/test_tool_fabric_integration.py`

**Deliverables:**
- ToolFabric main class
- Integration with LancelotOrchestrator.execute_command
- Receipt emission for tool calls

**Acceptance:**
- `execute_command("pytest")` runs in sandbox
- Receipt generated with duration, exit code

---

### Prompt 6 — RepoOps + FileOps

**Files:**
- `src/tools/providers/local_sandbox.py` (extend)
- `tests/test_repo_file_ops.py`

**Deliverables:**
- RepoOps: status, diff, apply_patch, commit, branch
- FileOps: read, write, list, apply_diff
- File hash tracking for receipts

**Acceptance:**
- Apply patch + commit works in sandbox
- Receipt includes file hashes before/after

---

### Prompt 7 — UIBuilder Templates

**Files:**
- `src/tools/providers/ui_templates.py`
- `templates/` directory
- `tests/test_ui_templates.py`

**Deliverables:**
- TemplateScaffolder implementation
- Template pack: nextjs_shadcn_dashboard, fastapi_service, etc.
- Scaffold + build verification

**Acceptance:**
- scaffold("nextjs_shadcn_dashboard", spec) produces valid project
- Project builds without Antigravity

---

### Prompt 8 — Antigravity UIBuilder

**Files:**
- `src/tools/providers/ui_antigravity.py`
- `tests/test_ui_antigravity.py`

**Deliverables:**
- AntigravityUIProvider adapter
- Integration with existing antigravity_engine.py
- Graceful fallback when unavailable

**Acceptance:**
- When enabled: uses Antigravity for generative UI
- When disabled: falls back to templates
- Receipt captures Antigravity invocation

---

### Prompt 9 — VisionControl

**Files:**
- `src/tools/providers/vision_antigravity.py`
- `tests/test_vision_control.py`

**Deliverables:**
- VisionControl capability interface
- AntigravityVisionProvider implementation
- capture_screen, locate_element, perform_action, verify_state

**Acceptance:**
- VisionControl routes to Antigravity when enabled
- Explicit failure when Antigravity unavailable (no silent downgrade)
- Vision receipts with screenshots hashed

---

### Prompt 10 — War Room Panel

**Files:**
- `src/ui/panels/tools_panel.py`
- `src/ui/war_room.py` (modify)
- `tests/test_tools_panel.py`

**Deliverables:**
- Tool Fabric panel in War Room
- Provider toggles, health display
- Routing policy summary
- Recent tool receipts viewer
- Safe Mode toggle

**Acceptance:**
- Panel shows all providers with health state
- Receipts filterable by capability/provider
- Safe Mode disables all optional providers

---

### Prompt 11 — Hardening

**Files:**
- `tests/test_tool_fabric_hardening.py`
- Updates to policies.py, router.py

**Deliverables:**
- Command denylist regression tests
- Path traversal tests
- Network enforcement tests
- Redaction tests
- Provider offline degradation tests

**Acceptance:**
- All security gates block unsafe actions
- "All providers offline" → core flows still work
- Malformed provider output → receipt valid, failure captured

---

## 3. Test Strategy

### Unit Tests (No Network, No Docker)

- contracts serialization
- policy evaluation
- router selection logic
- receipt redaction + bounding

### Integration Tests (Docker Required)

- run commands in sandbox
- git apply patch + commit
- scaffold UI template + build
- provider health probes

### Resilience Tests

- "all optional providers offline" → still passes core flows
- "sandbox unavailable" → clear error, no host execution fallback
- "provider returns malformed output" → receipt still valid, failure captured

---

## 4. Rollout Plan

### Phase 1 (Internal)

- Tool Fabric enabled but only LocalSandboxProvider + templates
- Feature flags default: `FEATURE_TOOLS_FABRIC=true`, others false

### Phase 2 (Hybrid)

- Add optional CLI providers one at a time
- Router uses them only when healthy

### Phase 3 (Decoupled)

- Gemini CLI/Antigravity no longer required for baseline
- Google tools are optional providers only

---

## 5. PR Checklist (Per Prompt)

- [ ] Spec created/updated
- [ ] Blueprint chunk completed
- [ ] Feature gated (`FEATURE_TOOLS_*`)
- [ ] Contracts defined
- [ ] Unit tests added
- [ ] Integration tests added (env-gated)
- [ ] Receipts emitted
- [ ] War Room visibility added (if applicable)
- [ ] CHANGELOG updated

---

**End of Blueprint**
