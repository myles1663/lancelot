
# Lancelot Engineering SOP  
## Add-Ons, Extensions, Incremental Upgrades **and GitHub Development Standards**

**Status:** Active  
**Applies to:** All new features, subsystems, architectural add-ons, and any repo changes  
**Audience:** Engineers and code-generation agents (e.g. Claude, Codex)  
**Last Updated:** 2026-02-04  

---

## 1. Purpose

This SOP defines **how new add-ons and subsystems are introduced into the Lancelot repository** *and* **how changes are developed, branched, reviewed, and merged in GitHub**—without destabilizing in-flight work, breaking existing upgrades, or creating documentation drift.

This SOP is mandatory.  
All add-ons **must comply** before implementation begins.

---

## 2. Core Principles (Non-Negotiable)

### 2.1 No Moving Targets
Once a spec or blueprint is marked **In Progress**, it is **frozen**.

- ❌ Do not edit or extend an active spec
- ❌ Do not “fold in” new ideas mid-implementation
- ✅ Create a **new spec + new blueprint** (new upgrade slice)

---

### 2.2 Add-Ons Are New Upgrade Slices
Any of the following automatically qualifies as a **new upgrade slice**:
- new subsystem (heartbeat, scheduler, memory engine, etc.)
- new background process
- new runtime dependency
- new control-plane surface
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

### 2.4 Main Is Always Releasable
`main` must remain:
- green (tests pass)
- runnable (boot works)
- safe to deploy (no half-wired modules)

No direct commits to `main`.

---

## 3. Required Workflow for Add-Ons

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
All new add-ons must be gated.

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

## 4. GitHub Development Standards (Branching, PRs, Push/Pull)

### 4.1 Branching Model
We use **trunk-based development with short-lived branches**:

- `main` = stable, releasable, protected
- feature branches = small, scoped, merge fast

**Branch naming**
```
feat/<slug>
fix/<slug>
chore/<slug>
docs/<slug>
spec/<slug>
```

**One branch = one slice**
- Each branch should map to **one spec/blueprint slice**.
- If scope grows: **stop** and create a **new spec + new branch**.

---

### 4.2 Pull Before You Push
Before opening a PR and before any major push:
- sync with `main`
- resolve conflicts locally

Preferred:
- rebase your branch on `main`, or
- merge `main` into your branch

**Never** rebase `main`.

---

### 4.3 Pull Requests Are Mandatory
All changes land via PR.

PR must include:
- link to Spec and Blueprint
- feature flag used (or docs-only)
- test evidence
- rollout notes

---

### 4.4 Required PR Checklist
- [ ] Spec created (new, not modified)
- [ ] Blueprint created
- [ ] Feature gated
- [ ] Contracts defined
- [ ] Unit tests added
- [ ] Integration tests added (env-gated)
- [ ] Receipts emitted
- [ ] War Room visibility added (if required)
- [ ] Runbook added/updated
- [ ] CHANGELOG updated

---

### 4.5 Commit Standards
Commits should be small and descriptive:
- feat: add scheduler receipts
- fix: onboarding READY gating
- docs: add health monitor runbook

---

### 4.6 Merging Rules
- Prefer squash merges for messy histories
- Rebase merge only for clean histories
- Avoid merge commits unless necessary

Delete branch after merge.

---

### 4.7 Protected Branch Policy
`main` must:
- require PR approval
- require CI to pass
- disallow force-push
- disallow direct commits

---

### 4.8 Hotfixes
For broken `main`:
1. branch from `main`
2. minimal fix only
3. fast PR + merge

---

### 4.9 Repo Hygiene
Never commit:
- secrets or keys
- `.env` files
- private certs

Use `.env.example` and `.gitignore`.

---

## 5. Testing Standards (Strict)

### 5.1 Unit Tests
- deterministic
- no network
- injected timers only

---

### 5.2 Integration Tests
- real services only
- env-gated
- no mocks

Use:
```python
@pytest.mark.integration
```

---

## 6. Persistence & Receipts

### 6.1 Persistent State
State that matters must persist across restarts.

---

### 6.2 Receipts
Receipts required for:
- state changes
- job execution
- failures
- skipped actions

---

## 7. War Room Requirements

Background or decision-making add-ons must surface:
- status
- last activity
- last error
- recovery actions

---

## 8. Onboarding & Gating Rules

### 8.1 Onboarding Integrity
Mandatory features must be installed, verified, and persisted before READY.

---

### 8.2 Scheduler & Background Gating
Jobs run only when:
- onboarding == READY
- dependencies healthy

Skipped jobs must log why.

---

## 9. Documentation Requirements

Each add-on requires:
- Spec
- Blueprint
- Architecture note
- Runbook

---

## 10. Prohibited Practices

🚫 Modifying active specs
🚫 Hidden background logic
🚫 Silent failures
🚫 Direct commits to `main`
🚫 Secrets in repo

---

## 11. Enforcement

Violations must be refactored or removed before merge.

---

## 12. Engineer Acknowledgement

By contributing, engineers agree to follow this SOP exactly.

---

**This SOP is the execution contract for all future Lancelot extensions.**

