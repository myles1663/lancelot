
# Lancelot vNext Upgrade Specification (Integrated Edition)
**Single-Choice Flagship Brain · Mandatory Local Utility Brain · Unbrickable Onboarding · War Room Control Plane**

---

## Generated
- Date: 2026-02-03
- Status: Authoritative implementation specification
- Scope: Replaces all prior vNext spec drafts

---

## 0. Executive Summary

This specification defines the next major evolution of **Project Lancelot**.

Lancelot becomes a **fully provisioned, single-install system** where:

- The user selects **one flagship model provider** (Gemini / OpenAI / Anthropic)
- A **local utility language model is mandatory and installed during onboarding**
- All model routing, tiering, and cost optimization is automatic and hidden
- Onboarding is **unbrickable** and always recoverable
- The War Room functions as a **true operational control plane**, not a viewer

There is no “partial install” state.  
When onboarding completes, **the entire system is ready**.

---

## 1. Core Design Principles

### 1.1 One Choice, Many Brains (Hidden)
Users make exactly one AI choice: the **Flagship Brain**.

All other models—local or remote—are automatically orchestrated.

### 1.2 Mandatory Local Utility Brain
A local open-source language model is required and installed as part of onboarding.
It handles all low-risk, high-volume cognitive tasks to:

- reduce token costs
- improve latency
- protect privacy
- stabilize behavior

The system **does not enter READY without a verified local model**.

### 1.3 No Bricking, Ever
There are no dead-end states.
All failures are recoverable through:
- resume
- back
- repair
- restart
- reset

### 1.4 War Room = Control Plane
The War Room is the authoritative surface for:
- system state
- approvals
- model routing
- cost pressure
- onboarding recovery

---

## 2. Model Architecture Overview

### 2.1 Internal Cognitive Roles

| Role | Purpose | Visibility |
|----|----|----|
| Utility Brain (Local) | classify, extract, summarize, redact | Hidden |
| Orchestrator Brain (Fast) | routing, tool calls, retries | Hidden |
| Primary Brain (Deep) | planning, high-risk decisions | Hidden |
| Flagship Provider | conversational identity | User-visible (provider only) |

Users never choose models by tier.

---

## 3. Local Utility Model Package (First-Class Subsystem)

### 3.1 What Is Included

The local model package consists of:

- **Runtime**: open-source inference engine (e.g., llama.cpp, MIT license)
- **Pinned model metadata** (no bundled weights):
  - model name
  - quantization
  - checksum
  - source URLs
- **Prompt suite**:
  - classify_intent
  - extract_json
  - summarize_internal
  - redact
  - rag_rewrite
- **Installer**:
  - downloads model from upstream publisher
  - verifies checksum
- **Smoke test**:
  - validates inference correctness
- **Health signals**:
  - surfaced in system status and War Room

Model weights are **not redistributed**.  
They are downloaded with explicit user consent during onboarding.

### 3.2 Licensing Posture

- Runtime: permissive OSS (MIT)
- Prompts/config: Lancelot IP
- Model weights:
  - downloaded by user from original source
  - checksum verified
  - never bundled in repo

This design is:
- open-source safe
- enterprise safe
- future commercial safe

---

## 4. Onboarding v2 (Provisioning Pipeline)

Onboarding is not a questionnaire.
It is **system provisioning**.

### 4.1 Onboarding States

```
WELCOME
FLAGSHIP_SELECTION
CREDENTIALS_CAPTURE
CREDENTIALS_VERIFY
LOCAL_UTILITY_SETUP   ← mandatory
COMMS_SELECTION
COMMS_CONFIGURE
COMMS_VERIFY
FINAL_CHECKS
READY
COOLDOWN (temporary failure state)
```

### 4.2 Mandatory Local Utility Setup

The `LOCAL_UTILITY_SETUP` state:

1. Explains purpose in plain language
2. Requests consent to download an open-source model
3. Performs:
   - runtime check (Docker / local-llm)
   - model presence check
   - download from upstream
   - checksum verification
   - service start
   - smoke test inference
4. Shows real-time progress
5. Blocks advancement until success

Failure handling:
- retry
- repair diagnostics
- cooldown
- War Room-assisted recovery

There is **no skip path**.

---

## 5. Onboarding Recovery & Persistence

### 5.1 Persistent Snapshot

All onboarding state is persisted to disk:

- current state
- flagship provider choice
- credential status
- local model install status
- verification codes (hashed)
- cooldown timers
- last error

App restarts always resume correctly.

### 5.2 Recovery Commands (Global)

Available at any step:
- BACK
- STATUS
- REPAIR
- RESTART STEP
- RESET ONBOARDING
- RESEND CODE

### 5.3 Cooldown (Replaces LOCKDOWN)

Permanent lockouts are prohibited.
Cooldown is time-based and reversible.

---

## 6. Model Routing System

### 6.1 Provider Profiles

Each flagship provider maps to a **profile**:

- deep lane
- fast lane
- cache lane (optional)

Profiles are runtime-loaded and versioned.

### 6.2 Routing Order

1. Local redaction (always)
2. Local utility tasks
3. Flagship fast lane
4. Flagship deep lane (risk / complexity / planning)

All routing decisions generate receipts.

---

## 7. War Room v2 (Control Plane)

### 7.1 Required Panels

#### 🧭 System State
- orchestrator readiness
- onboarding state
- local utility status
- flagship provider status

#### 🛂 Pending Approvals
- MCP / Sentry queue
- allow once / always / deny

#### 🧠 Model Router
- recent routing decisions
- chosen brain
- rationale

#### 📊 Cost & Token Pressure
- per-lane usage
- savings from local utility

#### 🔁 Setup & Recovery
- onboarding resume
- repair actions
- reset

War Room can complete onboarding if the UI flow is interrupted.

---

## 8. Security & Reliability Guarantees

- No silent failures
- Local redaction before any external call
- Safe error surfacing (no stack traces)
- Degraded mode clearly visible
- Deterministic recovery paths

---

## 9. Implementation Phases

### Phase 1 — Unbrickable Onboarding
### Phase 2 — War Room Control Plane
### Phase 3 — Local Model Package
### Phase 4 — Model Router & Provider Lanes
### Phase 5 — Cost Telemetry & Hardening

Each phase is incremental, test-driven, and production-safe.

---

## 10. Definition of Done

- One flagship choice
- Mandatory local utility brain installed during onboarding
- No onboarding dead ends
- War Room can intervene and repair
- Model routing is automatic and auditable
- Licensing posture is clean

---

## End of Specification
