
# Lancelot Engineering SOP  
## Add‑Ons, Extensions, and Incremental Upgrades

**Status:** Active  
**Applies to:** All new features, subsystems, and architectural add‑ons  
**Audience:** Engineers and code‑generation agents (e.g. Claude)  
**Last Updated:** 2026‑02‑03  

---

## 1. Purpose

This SOP defines **how new add‑ons and subsystems are introduced into the Lancelot repository** without destabilizing in‑flight work, breaking existing upgrades, or creating documentation drift.

This SOP is mandatory.  
All add‑ons **must comply** before implementation begins.

---

## 2. Core Principles (Non‑Negotiable)

### 2.1 No Moving Targets
Once a spec or blueprint is marked **In Progress**, it is **frozen**.

- ❌ Do not edit or extend an active spec
- ❌ Do not “fold in” new ideas mid‑implementation
- ✅ Create a **new spec + new blueprint**

---

### 2.2 Add‑Ons Are New Upgrade Slices
Any of the following automatically qualifies as a **new upgrade slice**:
- new subsystem (heartbeat, scheduler, memory engine, etc.)
- new background process
- new runtime dependency
- new control‑plane surface
- new persistent state
- new recurring behavior (cron, polling, watchers)

These **must not** be appended to an existing upgrade.

---

### 2.3 Everything Is Observable
If a feature runs:
- it must be visible
- it must be inspectable
- it must be recoverable

Invisible background logic is not allowed.

---

## 3. Required Workflow for Add‑Ons

### Step 1 — Create New Versioned Spec
Create a **new spec document**, never modify an active one.

**Naming convention**
```
docs/specs/
  Lancelot_vNext2_Spec_<Feature>.md
```

**Spec header must include**
- Status: Draft / Approved / In Progress
- Depends on: previous spec(s)
- Scope: what is explicitly included and excluded

---

### Step 2 — Create Matching Blueprint
Every spec **must** have a matching blueprint.

```
docs/blueprints/
  Lancelot_vNext2_Blueprint_<Feature>.md
```

Blueprint must include:
- build order
- chunking (round 1 + round 2)
- test strategy
- rollout notes

---

### Step 3 — Capability Gating (Feature Flags)
All new add‑ons must be gated.

Required pattern:
```env
FEATURE_<CAPABILITY_NAME>=true|false
```

Examples:
- FEATURE_HEALTH_MONITOR
- FEATURE_SCHEDULER
- FEATURE_BACKGROUND_JOBS

The system must boot and function with the feature disabled.

---

### Step 4 — Single Ownership Modules
New capabilities must have a **single owning module**.

✅ Good
```
src/core/health_monitor.py
src/core/scheduler/
```

❌ Bad
- logic spread across gateway, orchestrator, UI, and agents without a clear owner

---

### Step 5 — Define Contracts First
Before implementation, define:
- data models
- public methods
- endpoints (if any)
- receipts emitted
- health/readiness signals

Contracts are part of the spec, not “discovered” during coding.

---

## 4. Testing Standards (Strict)

### 4.1 Unit Tests (Required)
- deterministic
- no network
- no timers unless injected
- validate:
  - state transitions
  - config parsing
  - gating behavior
  - failure modes

---

### 4.2 Integration Tests (Required, Env‑Gated)
Integration tests must:
- use **real services**
- be skipped cleanly if env vars are missing
- never mock external APIs

Examples:
- local‑llm ping and inference
- scheduler job execution
- health heartbeat freshness
- provider API calls (when credentials exist)

Use:
```python
@pytest.mark.integration
```

---

## 5. Persistence & Receipts

### 5.1 Persistent State Rules
If state matters across restarts, it **must be persisted**:
- onboarding snapshots
- scheduler state
- job history
- cooldown timers

In‑memory only state is not acceptable for add‑ons.

---

### 5.2 Receipts Are Mandatory
Every add‑on must emit receipts for:
- state changes
- job execution
- failures
- skipped actions (with reason)

Receipt types must be named and documented.

---

## 6. War Room Requirements

Any add‑on that:
- runs in background
- makes decisions
- performs actions
- schedules work

**must surface in the War Room**.

Minimum visibility:
- status (running / degraded / stopped)
- last activity timestamp
- last error
- recovery actions

---

## 7. Onboarding & Gating Rules

### 7.1 Onboarding Integrity
If a feature is **mandatory**, it must be:
- installed
- verified
- persisted

before onboarding reaches READY.

No partial readiness states are allowed.

---

### 7.2 Scheduler & Background Gating
Background jobs must not run unless:
- onboarding state == READY
- required dependencies are healthy
- approvals (if required) are satisfied

Skipped jobs must record **why**.

---

## 8. Documentation Requirements

### 8.1 Required Docs per Add‑On
Every new add‑on requires:
- Spec (what)
- Blueprint (how)
- Architecture note (how it works)
- Runbook (how to fix it)

Minimum files:
```
docs/specs/
docs/blueprints/
docs/architecture/
docs/operations/runbooks/
```

---

### 8.2 CHANGELOG Entry
Each add‑on must add an entry to:
```
docs/CHANGELOG.md
```

Include:
- version
- summary
- link to spec
- link to blueprint

---

## 9. Prohibited Practices

🚫 Modifying active specs mid‑implementation  
🚫 Introducing background loops without visibility  
🚫 Silent degradation  
🚫 Hidden cron jobs  
🚫 Hardcoded schedules in code  
🚫 Mocked “fake” integrations for production paths  
🚫 Orphan modules not wired into runtime  

---

## 10. Enforcement

If an add‑on:
- violates this SOP
- bypasses documentation
- skips observability
- introduces hidden behavior

It **must be refactored or removed** before merge.

---

## 11. Engineer Acknowledgement

By implementing add‑ons in Lancelot, the engineer agrees to:
- respect frozen specs
- create new upgrade slices
- follow this SOP exactly

---

**This SOP is the execution contract for all future Lancelot extensions.**
