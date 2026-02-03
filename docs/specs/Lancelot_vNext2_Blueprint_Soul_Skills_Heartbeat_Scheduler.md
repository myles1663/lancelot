
# Lancelot vNext2 — Implementation Blueprint & Prompt Pack  
## Soul · Skills · Heartbeat · Scheduler (Chron)

**Status:** Draft (Execution-Ready)  
**Target Release:** vNext2  
**Depends On:**  
- Lancelot vNext Upgrade Spec – Integrated Edition  
- Lancelot vNext2 Spec – Soul, Skills, Heartbeat & Scheduler  
- Lancelot Engineering SOP – Add-Ons & Extensions  

**Audience:** Core engineers + code-generation agents (Claude, GPT-CLI)

---

## How to Use This Blueprint

1. Execute chunks in order.  
2. Each chunk is broken into micro-steps.  
3. Use the prompt pack to implement one step per commit:
   - write tests first
   - run unit tests
   - run integration tests when env is configured
   - commit

Integration tests use real services and are **env-gated** (skipped if not configured).

---

## 1) Target End State

### Soul
- Stored in `soul/` as versioned YAML
- Immutable once activated
- Amendments via proposal + lint + owner approval + activation
- Referenced by version, never stored inside recursive memory

### Skills
- Stored in `skills/<skill_name>/` with `skill.yaml`, `execute.py`, tests
- Registered in a persisted Skill Registry
- Permissioned and observable (receipts)
- Can be authored by humans or proposed by Lancelot (Skill Factory pipeline)
- Marketplace-ready packaging/signing boundaries (architecture supports; marketplace can ship later)

### Heartbeat
- `/health/live` and `/health/ready`
- Health monitor loop updates a cached HealthSnapshot
- Emits receipts on state changes
- War Room shows status and remediation actions

### Scheduler (Chron)
- Config-driven (`config/scheduler.yaml`)
- Persistent job store (SQLite) so restarts don’t lose jobs
- Job pipeline: Scheduler → Orchestrator → Router → Sentry → Skill → Receipts
- Jobs gated by:
  - onboarding READY
  - dependencies healthy
  - Soul constraints
  - approvals

---

## 2) Build Order (Why This Sequence)

1) **Contracts first** (Soul schema, Skill schema, HealthSnapshot, JobSpec)  
2) **Read-only visibility** (endpoints + War Room panels)  
3) **Controlled mutation flows** (Soul amendments, skill factory proposals)  
4) **Execution runtime** (scheduler and job runner)  
5) **Hardening and monetization hooks** (signing/ownership, receipts, governance)

This prevents “hidden background behavior” and keeps Lancelot operable at every step.

---

## 3) Chunking Round 1 (Right-Sized Chunks)

- **Chunk A — Soul Store + Linter + Versioning**
- **Chunk B — Skills Contract + Registry + Basic Execution**
- **Chunk C — Heartbeat Endpoints + Health Monitor**
- **Chunk D — Scheduler Core + Config + Persistent Store**
- **Chunk E — War Room Panels (Soul / Skills / Health / Scheduler)**
- **Chunk F — Skill Factory (agent-proposed skill workflow)**
- **Chunk G — Governance & Marketplace-Ready Hooks (signing, ownership, packaging)**
- **Chunk H — Hardening + Regression Suite**

---

## 4) Chunking Round 2 (Micro-Steps)

### Chunk A — Soul
A1. Define Soul YAML schema + loader  
A2. Implement Soul linter (invariant checks)  
A3. Implement soul versioning + active pointer  
A4. Implement amendment proposal object + diff output  
A5. Implement activation workflow (owner approval only)  
A6. Add receipts: soul_proposed, soul_activated  

### Chunk B — Skills
B1. Define Skill YAML schema + validator  
B2. Implement Skill Registry (persisted, versioned)  
B3. Implement Skill loader + enable/disable  
B4. Implement Skill execution adapter (through orchestrator)  
B5. Add receipts: skill_installed, skill_enabled, skill_ran, skill_failed  
B6. Add unit tests for contract enforcement and permissions gating  

### Chunk C — Heartbeat
C1. Define HealthSnapshot schema  
C2. Implement /health/live and /health/ready endpoints  
C3. Implement Health Monitor loop (dependency checks)  
C4. Add “freshness” checks: last_tick_at, scheduler_tick_at  
C5. Emit receipts on health state changes  
C6. Integration test: local-llm ping + snapshot updates (env-gated)  

### Chunk D — Scheduler
D1. Define scheduler.yaml schema and loader  
D2. Implement Scheduler service with persistent SQLite job store  
D3. Register built-in jobs from config  
D4. Implement “run now” and “enable/disable job”  
D5. Gate job execution by READY + Soul + approvals  
D6. Emit receipts: scheduled_job_run/failed/skipped  
D7. Integration test: run-now executes a small builtin job and emits receipt  

### Chunk E — War Room
E1. Add Soul panel (version + pending proposals)  
E2. Add Skills panel (installed/enabled, permissions, last run)  
E3. Add Health panel (live/ready + degraded reasons)  
E4. Add Scheduler panel (jobs list, run-now, enable/disable)  
E5. Add approvals queue panel (existing Sentry)  
E6. Ensure panels work in degraded mode (backend down)  

### Chunk F — Skill Factory
F1. Define SkillProposal object (diff, permissions, tests pass?)  
F2. Implement “generate skill skeleton” tool flow  
F3. Implement “run tests” and “propose to owner”  
F4. Owner approval flow in War Room  
F5. Register the approved skill automatically  
F6. Regression tests: proposal cannot auto-enable  

### Chunk G — Governance & Marketplace Hooks
G1. Add ownership metadata to Skill Registry (system/user/marketplace)  
G2. Add package format for skills (zip/tar) with manifest  
G3. Add signing interface (implementation can be stubbed but contract must exist)  
G4. Add policy: marketplace skills cannot request unsafe perms by default  
G5. War Room displays ownership + signature state  

### Chunk H — Hardening
H1. Add regression suite for Soul invariants  
H2. Add regression suite for scheduler gating  
H3. Ensure no endpoint leaks secrets or stack traces  
H4. Add “kill switch” feature flags for each subsystem  
H5. Document runbooks for health and scheduler  

---

## 5) Prompt Pack (Sequential, Test-Driven, Wired)

> Run these prompts in order. Each should result in a clean commit.

---

### Prompt 0 — Baseline: markers + test runners
```text
Ensure pytest markers exist and integration tests are supported.

Tasks:
- Add pytest.ini with markers: integration
- Add scripts/test.sh that runs:
  - pytest -q
  - pytest -q -m integration
- Confirm tests directory exists; add a minimal smoke test if missing.

Constraints:
- No refactors.
- Keep changes minimal.
```

---

### Prompt 1 — Soul schema + loader (A1)
```text
Implement Soul schema and loader.

Goals:
- Create soul/soul.yaml (initial version v1) with fields:
  - mission, allegiance, autonomy_posture, risk_rules, approval_rules,
    tone_invariants, memory_ethics, scheduling_boundaries
- Add src/core/soul/store.py:
  - load_active_soul()
  - list_versions()
  - get_active_version()
- Add Pydantic Soul model for validation.

Tests:
- Load valid soul.yaml
- Invalid soul.yaml fails with clear error
```

---

### Prompt 2 — Soul linter invariants (A2)
```text
Implement Soul linter invariant checks.

Goals:
- Add src/core/soul/linter.py:
  - lint(soul) -> list[issues]
- Enforce invariants like:
  - destructive actions require approval
  - no silent degradation
  - scheduling cannot execute irreversible actions autonomously
- Wire linter into soul loading (fail if critical issues).

Tests:
- Linter catches missing required invariant
- Linter passes canonical v1 soul
```

---

### Prompt 3 — Soul versioning + activation pointer (A3)
```text
Add Soul versioning and active pointer.

Goals:
- Store versions in soul/soul_versions/soul_v*.yaml
- Active pointer file: soul/ACTIVE (contains version string)
- store.py loads active version based on pointer
- Add receipt events: soul_loaded (optional)

Tests:
- Switching ACTIVE changes loaded soul
- Missing ACTIVE defaults to latest valid version
```

---

### Prompt 4 — Soul amendment proposals + diff (A4)
```text
Add Soul amendment proposal workflow objects.

Goals:
- Add src/core/soul/amendments.py:
  - SoulAmendmentProposal model: proposed_version, diff_summary, author, created_at, status
  - create_proposal(from_version, proposed_yaml_text)
  - compute_yaml_diff (human-readable)
- Store proposals in data/soul_proposals.json

Tests:
- Proposal created and persisted
- Diff includes expected changed keys
```

---

### Prompt 5 — Soul activation (owner approval only) (A5)
```text
Implement Soul activation with owner approval.

Goals:
- Add API endpoints:
  - GET /soul/status (active version, proposals)
  - POST /soul/proposals/{id}/approve
  - POST /soul/proposals/{id}/activate
- Enforce:
  - only owner can approve/activate (use existing identity mechanism)
  - activation runs linter; fails if invariants violated
- Emit receipts: soul_proposed, soul_approved, soul_activated

Tests:
- Non-owner cannot activate
- Activation fails when linter fails
```

---

### Prompt 6 — Skill schema + validator (B1)
```text
Implement skill.yaml schema and validator.

Goals:
- Create src/core/skills/schema.py:
  - SkillManifest model: name, version, inputs, outputs, risk, permissions,
    required_brain, scheduler_eligible, sentry_requirements, receipts
- Add validator to load a skill.yaml and validate.

Tests:
- Valid manifest passes
- Missing permissions or version fails
```

---

### Prompt 7 — Skill registry persistence (B2)
```text
Implement Skill Registry.

Goals:
- Create src/core/skills/registry.py:
  - install_skill(path)
  - enable_skill(name)
  - disable_skill(name)
  - list_skills()
  - get_skill(name)
- Persist to data/skills_registry.json
- Track ownership metadata: system/user/marketplace and signature_state placeholder

Tests:
- Install + enable persists across reload
- Disable persists
```

---

### Prompt 8 — Skill loader + execution adapter (B3–B4)
```text
Implement skill loader and execution adapter wired through orchestrator.

Goals:
- Define skill runtime interface: execute(context, inputs) -> outputs
- Implement loading execute.py safely
- Wire orchestrator to invoke a skill by name
- Add receipts:
  - skill_installed
  - skill_enabled
  - skill_ran
  - skill_failed

Tests:
- Create a simple built-in skill "echo" and run it end-to-end
- Receipt created on run
```

---

### Prompt 9 — Heartbeat endpoints + HealthSnapshot (C1–C2)
```text
Implement heartbeat endpoints.

Goals:
- Add src/core/health/types.py: HealthSnapshot model
- Add endpoints:
  - GET /health/live (always 200 if process running)
  - GET /health/ready (returns ready=false with degraded reasons)
- Include fields:
  - onboarding_state
  - local_llm_ready
  - scheduler_running
  - last_health_tick_at
  - last_scheduler_tick_at
  - degraded_reasons[]

Tests:
- Endpoints return required keys
- No stack traces leaked
```

---

### Prompt 10 — Health monitor loop + receipts (C3–C5)
```text
Add Health Monitor loop.

Goals:
- Add src/core/health/monitor.py:
  - start_monitor()
  - compute_snapshot()
- Monitor checks:
  - local-llm ping
  - scheduler freshness
  - onboarding READY
- Cache snapshot in orchestrator for fast endpoint responses
- Emit receipts on state change:
  - health_ok, health_degraded, health_recovered

Tests:
- Unit test compute_snapshot using injected check functions
```

---

### Prompt 11 — Scheduler config + schema (D1)
```text
Implement scheduler.yaml schema and loader.

Goals:
- Add config/scheduler.example.yaml
- On first run, create config/scheduler.yaml from example
- Add src/core/scheduler/schema.py:
  - JobSpec model (id, name, trigger, enabled, requires_ready, requires_approvals, timeout_s)
- Tests:
  - valid config loads
  - invalid triggers fail validation
```

---

### Prompt 12 — Scheduler service + persistent job store (D2–D3)
```text
Implement Scheduler service with persistent store.

Goals:
- Add src/core/scheduler/service.py
- Use SQLite file data/scheduler.sqlite for persistence
- Register jobs from scheduler.yaml at startup
- Maintain last_scheduler_tick_at heartbeat field
- Provide methods:
  - list_jobs()
  - run_now(job_id)
  - enable_job(job_id)
  - disable_job(job_id)

Tests:
- Unit test job registration and persistence behavior
```

---

### Prompt 13 — Job execution pipeline + gating + receipts (D4–D6)
```text
Wire job execution through orchestrator, router, sentry, and skills.

Goals:
- Scheduler executes by:
  - resolving job -> skill invocation OR orchestrator intent
  - gating:
    - onboarding READY
    - local model healthy
    - Soul scheduling boundaries
    - approvals if required
- Emit receipts:
  - scheduled_job_run
  - scheduled_job_failed
  - scheduled_job_skipped (with reason)

Tests:
- Job skipped when not READY
- Job runs when READY and emits receipt
```

---

### Prompt 14 — War Room panels for Soul/Skills/Health/Scheduler (E1–E4)
```text
Add War Room control-plane panels.

Goals:
- Soul panel: active version, proposals, approve/activate buttons
- Skills panel: list skills, enable/disable, view permissions and ownership
- Health panel: live/ready status, degraded reasons, last tick
- Scheduler panel: list jobs, enable/disable, run-now, last run status

Constraints:
- Must handle backend down gracefully
- Must not expose secrets

No orphan UI: everything wired to endpoints.
```

---

### Prompt 15 — Skill Factory proposals (F1–F4)
```text
Implement Skill Factory proposal pipeline.

Goals:
- Add SkillProposal model:
  - skill manifest, code diff, requested permissions, tests_status
- Implement generating a new skill skeleton (manifest + execute.py + tests)
- Run tests locally and attach results to proposal
- Store proposals in data/skill_proposals.json
- War Room shows proposals and allows owner approval

Tests:
- Proposal cannot auto-enable itself
- Approval required for installation
```

---

### Prompt 16 — Governance & marketplace hooks (G1–G5)
```text
Implement marketplace-ready governance hooks.

Goals:
- Add ownership metadata to registry: system/user/marketplace
- Add skill packaging format:
  - build_skill_package(skill_name) -> .zip with manifest and files
- Add signing interface contract (no real crypto required yet):
  - SignatureState: unsigned/verified/invalid
- Policy: marketplace skills default to restricted perms unless explicitly approved

Tests:
- Packaging produces expected files
- Registry retains ownership/signature fields
```

---

### Prompt 17 — Hardening + kill switches + runbooks (H1–H5)
```text
Hardening pass.

Goals:
- Add feature flags:
  - FEATURE_SOUL, FEATURE_SKILLS, FEATURE_HEALTH_MONITOR, FEATURE_SCHEDULER
- Ensure system boots with each disabled
- Add regression tests for:
  - soul invariants
  - scheduler gating
  - safe errors
- Add runbooks:
  - docs/operations/runbooks/health.md
  - docs/operations/runbooks/scheduler.md
  - docs/operations/runbooks/skills.md
  - docs/operations/runbooks/soul.md

Constraints:
- No breaking changes to existing endpoints
- No stack traces returned
```

---

## 6) Definition of Done

- Soul immutable, versioned, linted, owner-activated  
- Skills registry persisted, permissioned, auditable  
- Skill Factory proposals gated and visible  
- Heartbeat endpoints accurate + monitored  
- Scheduler persistent and gated by Soul/READY/approvals  
- War Room exposes everything with recovery controls  
- Receipts exist for all state changes and executions  
- Feature flags allow safe incremental rollout  

---

## End of Blueprint
