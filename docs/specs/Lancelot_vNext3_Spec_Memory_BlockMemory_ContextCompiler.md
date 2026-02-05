# Lancelot vNext3 Specification
## Block Memory + Context Compiler + Governed Self-Edits

**Status:** In Progress
**Target Release:** vNext3
**Depends On:**
- Lancelot vNext2 Spec – Soul, Skills, Heartbeat, Scheduler
- Lancelot Engineering SOP – Add-Ons & Extensions

---

## 0. Executive Summary

This specification defines **Lancelot vNext3**, introducing a governed memory architecture:

1. **Block Memory** — Tiered memory with pinned core blocks (persona, human, mission, operating_rules, workspace_state)
2. **Context Compiler** — Deterministic prompt assembly with receipts and token budgets
3. **Governed Self-Edits** — Atomic commits, write gates, quarantine, promotion, and rollback
4. **Memory Observability** — Full audit trail, War Room integration, scheduler jobs

These capabilities are introduced as a **new upgrade slice**, fully compliant with the Engineering SOP.

---

## 1. Goals and Success Criteria

### 1.1 Goals
- Core Memory Blocks pinned in-context (deterministic "always visible" memory)
- Self-editing memory tools with atomic commits, diff receipts, and rollback
- Tiered memory hierarchy with lifecycle rules (core / working / episodic / archival)
- Context Compiler that assembles the runtime prompt from blocks + targeted retrieval
- Memory poisoning defense (quarantine, provenance, TTL/decay, write gates)
- First-class observability (why this memory was included; what changed; how to revert)

### 1.2 Non-Goals (Explicit)
- Building a full vector DB service (keep local-first; SQLite ok; optional embeddings later)
- Fully autonomous Soul rewriting (agent can propose only; owner approves)
- Perfect semantic recall (we'll ship correctness + safety > recall)

### 1.3 Success Criteria (Measurable)
- Deterministic context assembly: same inputs → same compiled context
- Core block size bounds enforced (token budget hard limits)
- Every memory write produces a receipt with diff + provenance + rollback pointer
- Poisoning tests pass: prompt injection attempts do not end up in Core Memory
- Rollbacks work: reverting N commits restores prior core blocks exactly

---

## 2. Memory Tiers (Four-Tier Model)

### Tier A — Core Memory (Pinned Blocks)
Always included in context.

| Block | Description |
|-------|-------------|
| persona | Derived from Soul compilation + stable behavior constraints |
| human | User preferences/profile; from USER.md + verified facts |
| mission | Long-running objectives / current quest focus |
| operating_rules | Execution style constraints, guardrails, tool discipline |
| workspace_state | Project-specific pinned state (optional) |

**Key property:** small, curated, verified, hard token limits.

### Tier B — Working Memory (Task Scratchpad)
Short-lived and task/quest scoped (hours-days).
- Plan scratch
- Intermediate extracted facts
- Active TODOs
- Transient decisions

Auto-expiring by TTL.

### Tier C — Episodic Memory (Conversation Timeline)
Immutable append-only log + periodic summaries.
- Messages (or references to chat_log)
- Per-session summaries
- Important events extracted into "Episodic Events"

### Tier D — Archival Memory (Long-term Store)
Searchable knowledge base of facts/events/docs/receipts.
- Structured entries with provenance, confidence, TTL/decay
- Optional embeddings later (start with lexical + metadata filters)

---

## 3. Context Compiler

### 3.1 Inputs
- objective: str
- quest_id: str | None
- recent_messages: N (from chat_log/episodic)
- soul_active_version
- mode: normal | crusader

### 3.2 Output Artifact
```python
class CompiledContext(BaseModel):
    context_id: str
    created_at: datetime
    objective: str
    included_blocks: list[CoreBlockType]
    included_memory_item_ids: list[str]
    excluded_candidates: list[dict]
    token_estimate: int
    rendered_prompt: str
```

### 3.3 Assembly Algorithm (Deterministic)
1. Compile persona block from Soul
2. Load Core Blocks in fixed order: persona → human → operating_rules → mission → workspace_state
3. Add Working Memory (namespace quest:<id> then global, apply TTL filter)
4. Retrieval step (query archival + episodic summaries)
5. Budget enforcement (hard cap per block, drop lowest confidence first)
6. Security filters (remove tool-injection patterns, redact secrets)
7. Emit CompiledContext + receipt

---

## 4. Memory Safety & Poisoning Defenses

### 4.1 Write Gates (Mandatory)
Before commit:
- Block allowlist: which blocks can be written by agent
- Evidence required: any core:human update requires provenance from user message
- Quarantine-by-default for new core edits
- Confidence floor: below floor → archival only (never core)
- Secret scrubbing: detect API keys/tokens; never store in memory

### 4.2 Promotion Workflow
memory_promote(item_id) requires:
- Verifier pass (LLM or deterministic heuristic)
- Owner approval depending on Soul rules (memory_ethics)

---

## 5. Storage Plan

### 5.1 New Directory
```
lancelot_data/memory/
  core_blocks.json
  working_memory.sqlite
  episodic.sqlite
  archival.sqlite
  commits/
```

### 5.2 Preserved Files
- lancelot_data/chat_log.json (read; later optional migrate)
- lancelot_data/receipts/ (write)
- lancelot_data/USER.md (source for human block)

---

## 6. Agent Tool Functions

### 6.1 Staged Edits
- memory_begin_edits() → staged_commit_id
- memory_insert(staged_commit_id, target, content, reason, confidence, provenance)
- memory_replace(staged_commit_id, target, selector, new_content, reason, confidence, provenance)
- memory_delete(staged_commit_id, target, selector, reason, confidence, provenance)
- memory_finish_edits(staged_commit_id) → committed_commit_id

### 6.2 Retrieval
- memory_search(query, tiers, namespace, tags, limit) → items
- memory_get_core_blocks() → blocks

### 6.3 Safety/Admin
- memory_quarantine(target, reason)
- memory_rollback(commit_id, reason)

### 6.4 Governance Restrictions
- rethink only allowed for working/archival, never for persona unless owner-approved
- direct edits to Soul are never allowed; only soul_propose_amendment()

---

## 7. API Endpoints

```
GET  /memory/core
GET  /memory/search
POST /memory/commit/begin
POST /memory/commit/{id}/finish
POST /memory/rollback/{commit_id}
GET  /memory/quarantine
POST /memory/promote/{id}
```

---

## 8. War Room Integration

Memory panel with:
- Core Blocks View (content, token budget, last updated, status)
- Commit History (list with diff preview, rollback button)
- Quarantine Queue (pending items, approve/deny)
- Context Compiler Trace (last N contexts, why included/excluded, token breakdown)

---

## 9. Scheduler Jobs

| Job | Schedule | Description |
|-----|----------|-------------|
| memory_compact_working | hourly | Remove expired items, summarize long threads |
| memory_decay_archival | daily 3am | Reduce confidence over time unless reinforced |
| memory_summarize_episodic | daily | Write session summary + event extraction |
| memory_integrity_audit | daily | Ensure commits have receipts, blocks within budget |

Each job emits receipts: memory_job_run, memory_job_failed, memory_job_skipped

---

## 10. Feature Flag

```env
FEATURE_MEMORY_VNEXT=true|false
```

System must boot and function with the feature disabled.

---

## 11. Testing Requirements

### Unit Tests
- Core block budget enforcement
- Deterministic context ordering
- Commit diff generation
- Rollback correctness
- Quarantine rules

### Security Tests
- Prompt injection attempts do not write core memory
- Tool injection strings do not get included in compiled prompt
- Secrets never persist in any tier

### Integration Tests
- Compile context → run planner → receipt emitted
- Memory edit → commit → receipt → rollback restores prior blocks
- Scheduler jobs run and emit receipts

---

## 12. Definition of Done

- Memory subsystem feature-flagged and operational
- Context Compiler deterministic and receipted
- All writes gated, quarantined, promotable, rollbackable
- War Room Memory panel functional
- Scheduler jobs operational
- All tests passing

---

## End of Specification
