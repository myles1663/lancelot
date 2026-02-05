# Lancelot vNext3 Blueprint
## Block Memory + Context Compiler + Governed Self-Edits

**Status:** In Progress
**Spec:** Lancelot_vNext3_Spec_Memory_BlockMemory_ContextCompiler.md
**Branch:** feat/memory-vnext

---

## 1. Build Order

### Phase 0 — Foundations (No Behavior Change)
1. Create `src/core/memory/` package
2. Define Pydantic v2 schemas
3. Local-first persistence (`lancelot_data/memory/`)
4. Bootstrap read-only core blocks

### Phase 1 — Context Compiler (Read-Only)
5. Deterministic compilation order
6. Context receipts
7. Runtime prompt integration (feature-flagged)

### Phase 2 — Retrieval
8. SQLite FTS5 index
9. Budgeted retrieval in compiler
10. Inclusion / exclusion traces

### Phase 3 — Governed Self-Edits
11. Staged commits
12. Diff receipts + rollback pointers
13. Rollback engine

### Phase 4 — Safety & Governance
14. Write gates
15. Quarantine-by-default
16. Promotion workflow

### Phase 5 — Observability & Ops
17. API surface
18. War Room UI
19. Scheduler jobs

---

## 2. Chunking Strategy (Round 1)

| Chunk | Description |
|-------|-------------|
| A | Schemas + persistence |
| B | Context Compiler v1 |
| C | Search index + retrieval |
| D | Governed commits |
| E | Rollback + quarantine + promotion |
| F | API + War Room UI |
| G | Scheduler jobs |

---

## 3. Chunking Strategy (Round 2 — Right-Sized Steps)

### A — Schemas & Persistence
- A1. Schemas + enums
- A2. Core block JSON store
- A3. SQLite stores (working/episodic/archival)
- A4. Commit tables

### B — Context Compiler
- B1. Ordering + budgets
- B2. USER.md bootstrap
- B3. Receipts + traces
- B4. Runtime wiring

### C — Retrieval
- C1. FTS indexing
- C2. Search API
- C3. Compiler retrieval section
- C4. Integration tests

### D — Governed Commits
- D1. Staged commits
- D2. Atomic apply + diffs
- D3. Receipts
- D4. Tool wiring

### E — Safety
- E1. Write gates
- E2. Quarantine
- E3. Promotion
- E4. Rollback

### F — API & UI
- F1. FastAPI routes
- F2. Core/commit UI
- F3. Quarantine UI
- F4. Trace viewer

### G — Scheduler Jobs
- G1. Working compaction
- G2. Episodic summarization
- G3. Archival decay
- G4. Integrity audit

---

## 4. Code-Generation Prompt Sequence

### Prompt 0 — Memory Package & Core Blocks
Create `src/core/memory/` with:
- `__init__.py` with feature flag check
- `schemas.py` with Pydantic v2 models (CoreBlockType, CoreBlock, Provenance, MemoryItem, MemoryCommit, MemoryEdit)
- `store.py` with CoreBlockStore (JSON persistence)
- `config.py` with token budgets and defaults
- Tests in `tests/test_memory_schemas.py`

### Prompt 1 — SQLite Stores + FTS Schema
Create SQLite stores:
- `sqlite_store.py` with MemoryItemStore
- FTS5 virtual table for full-text search
- Working/episodic/archival tier management
- Tests in `tests/test_memory_sqlite.py`

### Prompt 2 — Context Compiler v1
Create context compiler:
- `compiler.py` with ContextCompiler class
- Deterministic block ordering
- Token budget enforcement
- Receipt emission
- Tests in `tests/test_context_compiler.py`

### Prompt 3 — Memory Search + Retrieval
Add retrieval capabilities:
- `index.py` with MemoryIndex class
- FTS5 search integration
- Compiler retrieval section
- Tests in `tests/test_memory_search.py`

### Prompt 4 — Governed Staged Commits
Implement commit system:
- `commits.py` with CommitManager
- Staged edit workflow
- Atomic apply with diffs
- Receipt generation
- Tests in `tests/test_memory_commits.py`

### Prompt 5 — Write Gates + Quarantine
Add safety layer:
- `gates.py` with WriteGateValidator
- Secret detection and scrubbing
- Quarantine-by-default logic
- Tests in `tests/test_memory_gates.py`

### Prompt 6 — Promotion + Rollback
Complete governance:
- `promotion.py` with PromotionWorkflow
- `rollback.py` with RollbackEngine
- Exact state restoration
- Tests in `tests/test_memory_rollback.py`

### Prompt 7 — API Endpoints
Create FastAPI routes:
- `api.py` with all memory endpoints
- Integration with gateway
- Tests in `tests/test_memory_api.py`

### Prompt 8 — War Room UI + Scheduler Jobs
Add observability:
- `src/ui/panels/memory_panel.py`
- Scheduler job implementations
- Integration tests

---

## 5. Test Strategy

### Unit Tests
- Each module has corresponding test file
- Pytest with fixtures
- No network calls in unit tests
- Injected timers for time-sensitive tests

### Integration Tests
- Real SQLite databases
- Real file system operations
- Decorated with `@pytest.mark.integration`

### Security Tests
- Prompt injection defense validation
- Secret scrubbing verification
- Quarantine enforcement tests

---

## 6. Rollout Notes

### Pre-Release
- Feature flag defaults to false
- All existing behavior preserved
- Memory subsystem initializes in background

### Release
- Enable via FEATURE_MEMORY_VNEXT=true
- Monitor receipts for issues
- War Room provides visibility

### Rollback
- Disable feature flag
- System reverts to previous behavior
- No data migration required for disable

---

## 7. Completion Criteria

This upgrade is complete when:
- [ ] Context assembly is deterministic and inspectable
- [ ] All memory writes are gated, receipted, and reversible
- [ ] No orphaned code paths exist
- [ ] War Room exposes memory state and history
- [ ] Scheduler jobs maintain hygiene automatically
- [ ] All tests pass
- [ ] Documentation complete

---

## End of Blueprint
