
# Lancelot vNext2 Specification  
## Soul, Skills, Heartbeat, and Scheduler (Chron)

**Status:** Draft (Authoritative)  
**Target Release:** vNext2  
**Depends On:**  
- Lancelot vNext Upgrade Spec – Integrated Edition  
- Lancelot Engineering SOP – Add‑Ons & Extensions  

---

## 0. Executive Summary

This specification defines **Lancelot vNext2**, introducing four tightly related subsystems:

1. **Soul** — Lancelot’s immutable constitutional identity  
2. **Skills** — modular, permissioned, monetizable capabilities  
3. **Heartbeat** — continuous health, readiness, and freshness signals  
4. **Scheduler (Chron)** — cron‑based and interval‑based autonomous execution  

These capabilities are introduced as a **new upgrade slice**, fully compliant with the Engineering SOP.

---

## 1. Core Principles

### 1.1 Soul Is Constitutional
- Soul defines *who Lancelot is*
- Soul is global, versioned, and audited
- Soul is not stored in recursive memory
- Soul cannot be modified directly by the model

### 1.2 Skills Are Modular Capabilities
- Skills define *what Lancelot can do*
- Skills are declarative, testable, permissioned, and versioned
- Skills can be created by humans or agents
- Skills may be monetized

### 1.3 Everything Autonomous Must Be Observable
- Heartbeat and Scheduler behavior must be visible
- All executions produce receipts
- War Room is the authoritative control plane

---

## 2. The Soul Subsystem

### 2.1 Definition
The Soul is Lancelot’s constitutional layer defining invariant behavior regardless of model, provider, or skill set.

### 2.2 Soul Contents
Soul defines:
- Mission and allegiance
- Autonomy posture
- Risk tolerance and escalation rules
- Approval requirements
- Tone invariants
- Memory ethics
- Scheduling boundaries

### 2.3 Storage and Versioning
```
soul/
  soul.yaml
  soul_versions/
```

### 2.4 Soul Amendment Workflow
1. Proposal generated
2. Diff and impact analysis
3. Soul linter validation
4. Owner approval
5. Version activation

---

## 3. Skills Framework

### 3.1 Skill Structure
```
skills/<skill_name>/
  skill.yaml
  execute.py
  tests/
```

### 3.2 Skill Contract
Each skill declares inputs, outputs, risk, permissions, required brain, scheduler eligibility, and receipts.

### 3.3 Skill Registry
Central registry tracks enabled skills, versions, permissions, and ownership.

---

## 4. Skill Factory (Self‑Creation)

Lancelot may propose new skills through a governed Skill Factory pipeline:
- Draft → Test → Propose → Approve → Register

No skill self‑activates.

---

## 5. Skill Monetization

Skills may be monetized via a marketplace:
- Official
- Verified
- Community (sandboxed)

Permissions, pricing, and receipts are visible in War Room.

---

## 6. Heartbeat System

### 6.1 Endpoints
- /health/live
- /health/ready

### 6.2 Monitor
Background monitor checks dependencies and emits receipts on state changes.

---

## 7. Scheduler (Chron)

### 7.1 Capabilities
- Cron jobs
- Interval jobs
- Event‑triggered jobs

### 7.2 Execution Pipeline
Scheduler → Orchestrator → Router → Sentry → Skill

### 7.3 Gating
Jobs only run when onboarding READY and dependencies healthy.

---

## 8. War Room Integration

War Room exposes:
- Soul version
- Skill registry
- Job status
- Health status
- Approvals and receipts

---

## 9. Memory Interaction

Soul and Skills are not stored in memory. Memory references versions and enabled skill IDs only.

---

## 10. Testing

- Unit tests for soul linting, skills, scheduler gating
- Integration tests for heartbeat and job execution

---

## 11. Definition of Done

- Soul versioned and immutable
- Skills modular and auditable
- Self‑skill creation gated
- Heartbeat visible
- Scheduler observable

---

## End of Specification
