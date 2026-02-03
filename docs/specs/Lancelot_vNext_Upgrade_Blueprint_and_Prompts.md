
# Lancelot vNext Upgrade — Implementation Blueprint & Prompt Pack
**Mandatory Local Utility Model · Single Flagship Choice · Unbrickable Onboarding · War Room Control Plane**

---

## Generated
- Date: 2026-02-03
- Status: Implementation Blueprint + Code-Generation Prompt Pack
- Audience: Core Lancelot development (GPT-CLI / Claude Code workflows)

---

## How to Use This Document

This document is designed to be used **sequentially**.

1. Read Sections 1–3 to understand the architecture and build order.
2. Execute the prompts in Section 4 **in order**.
3. After each prompt:
   - run tests
   - commit changes
   - proceed to the next prompt

Each prompt:
- is commit-sized
- is test-driven
- wires directly into live code paths
- introduces no orphaned code

Integration tests use **real services** and are skipped automatically if required environment variables are missing.

---

## 1. Target End State (What “Done” Looks Like)

### User Experience
- One unified onboarding provisions the **entire system**
- User selects **one flagship provider**
- Local utility model is **mandatory and installed during onboarding**
- No dead-end states or manual file deletion
- War Room can recover, repair, and intervene at any time

### Cognitive Architecture
| Brain | Purpose |
|-----|--------|
| Local Utility | classify, extract, summarize, redact |
| Flagship Fast | orchestration, tools, retries |
| Flagship Deep | planning, high-risk decisions |

### Control Plane
War Room exposes:
- system readiness
- onboarding recovery
- pending approvals
- router decisions
- cost/token pressure

---

## 2. Build Order (Why This Sequence)

1. Unbrickable onboarding
2. War Room recovery authority
3. Local model package (productized)
4. Mandatory local install during onboarding
5. Routing (local first)
6. Provider lanes + escalation
7. Cost telemetry + hardening

At every step, the system remains runnable.

---

## 3. Implementation Chunks

### Chunk A — Unbrickable Onboarding
Snapshot persistence, recovery commands, cooldown.

### Chunk B — Control-Plane Endpoints
System + onboarding APIs.

### Chunk C — War Room Recovery Panel
UI wired to backend recovery.

### Chunk D — Local Model Package
Lockfile, prompts, fetch, smoke test.

### Chunk E — Mandatory Local Install
LOCAL_UTILITY_SETUP onboarding state.

### Chunk F — local-llm Runtime + Client
Docker service + Python client.

### Chunk G — Runtime Configs & Provider Profiles
models.yaml / router.yaml activated.

### Chunk H — ModelRouter v1
Local utility routing + receipts.

### Chunk I — Provider Lanes
Fast vs deep escalation.

### Chunk J — Usage & Hardening
Cost telemetry + reliability fixes.

---

## 4. Code-Generation Prompts (Run in Order)

---

### Prompt 0 — Test Harness Baseline
```text
Ensure pytest and integration markers exist. Add minimal smoke test and test runner.
```
---

### Prompt 1 — Onboarding Snapshot Persistence
```text
Implement OnboardingSnapshot persistence with disk-backed JSON storage and tests.
```
---

### Prompt 2 — STATUS Command
```text
Add global STATUS command showing onboarding and system provisioning state.
```
---

### Prompt 3 — BACK and RESTART STEP
```text
Implement BACK and RESTART STEP recovery commands with safe state transitions.
```
---

### Prompt 4 — Verification Code Persistence & RESEND CODE
```text
Persist verification codes (hashed) and add rate-limited RESEND CODE.
```
---

### Prompt 5 — Replace LOCKDOWN with COOLDOWN
```text
Remove permanent LOCKDOWN; implement time-based COOLDOWN with recovery.
```
---

### Prompt 6 — Control-Plane API Endpoints
```text
Add /system/status and onboarding control endpoints with safe error handling.
```
---

### Prompt 7 — War Room Setup & Recovery Panel
```text
Add Setup & Recovery panel in War Room wired to onboarding endpoints.
```
---

### Prompt 8 — Local Model Package Scaffold
```text
Create local_models/ structure, models.lock.yaml, prompts, and schema validation.
```
---

### Prompt 9 — fetch_model.py
```text
Implement model download + checksum verification from lockfile.
```
---

### Prompt 10 — smoke_test.py
```text
Implement local model smoke test with real inference (env-gated).
```
---

### Prompt 11 — local-llm Docker Service
```text
Add local-llm service to docker-compose with healthcheck and volume mount.
```
---

### Prompt 12 — Mandatory LOCAL_UTILITY_SETUP Onboarding State
```text
Add mandatory local utility install + verification during onboarding.
```
---

### Prompt 13 — LocalModelClient & Utility Prompts
```text
Implement LocalModelClient and utility task runners with integration tests.
```
---

### Prompt 14 — Runtime Configs & Provider Profiles
```text
Activate runtime models.yaml/router.yaml and ProviderProfile registry.
```
---

### Prompt 15 — ModelRouter v1 (Local Utility)
```text
Route utility tasks to local model, log router receipts, expose War Room panel.
```
---

### Prompt 16 — Provider Lanes & Escalation
```text
Implement fast/deep flagship lanes with risk-based escalation and real API tests.
```
---

### Prompt 17 — Usage & Cost Telemetry
```text
Track per-brain usage, expose /usage/summary, add War Room cost panel.
```
---

### Prompt 18 — Hardening & Reliability Pass
```text
Fix health checks, error leakage, and add regression tests.
```
---

## 5. Definition of Done

- Single flagship choice
- Mandatory local utility brain installed during onboarding
- No onboarding dead ends
- War Room can fully recover and intervene
- Routing decisions auditable
- Licensing posture clean
- Tests pass continuously

---

## End of Blueprint
