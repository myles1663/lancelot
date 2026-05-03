# Context Continuity

Lancelot uses several durable state layers so long-running sessions can resume without treating the raw chat transcript as the source of truth.

## Runtime State Layers

| Layer | Storage | Purpose |
|-------|---------|---------|
| Recent chat history | `lancelot_data/chat/` | Keep the newest user/assistant turns verbatim for local continuity. |
| Chat compaction summaries | `lancelot_data/chat/chat_summaries.json` | Preserve older turn intent in a bounded schema. |
| Work ledger | `/home/lancelot/data/work/work_ledger.sqlite` | Track active work, blockers, next actions, checkpoints, approvals, files touched, and receipt evidence. |
| Session briefs | `session_briefs` table in the work ledger | Preserve cross-session handoff summaries when active work is complete or terminal. |
| Structured memory | SQLite tier stores under the memory data directory | Store working, episodic, and archival memory for governed retrieval. |

## Chat Compaction

When chat history exceeds the configured window, Lancelot keeps recent turns verbatim and compacts older turns into schema version 2 summaries.

Each compacted summary can include:

- `decisions_made`
- `user_preferences`
- `unresolved_questions`
- `durable_facts`
- `rejected_or_blocked_actions`
- `next_steps`
- `receipt_references`
- preview lists for user intents and assistant outcomes

The renderer includes those fields in the context block so future turns see durable decisions and next steps without replaying the full transcript.

## Work Ledger And Session Briefs

The work ledger is the operator-facing continuity layer for Command Center work. It records active and terminal work items, progress events, checkpoints, and receipt-backed evidence.

Checkpoints capture:

- completed work
- pending work
- open decisions
- files touched
- approvals
- receipt IDs

Session briefs aggregate the latest work items, checkpoints, and ledger events into a handoff summary with:

- objective
- completed work
- pending work
- blockers
- files touched
- approvals
- receipts
- next action
- known risks

The context renderer uses active work first. If no active work is available for the current session or operator, it can include the latest session brief instead.

## Task Experience Memory

Meaningful completed tasks are recorded in episodic memory even when the deep reasoning pass did not run. This is intentionally decoupled from `FEATURE_DEEP_REASONING_LOOP`.

Task experience metadata includes:

- operator and channel
- quest and session
- tools used, succeeded, failed, or blocked
- files touched
- receipt IDs
- outcome and elapsed time
- retries
- approvals
- workflow/template ID when present

These records are tagged for retrieval, including channel, operator, quest, and workflow tags.

## Retrieval Ranking

Memory retrieval uses tokenized SQLite FTS/BM25-style queries. Cross-tier retrieval dedupes repeated episodic and archival content before ranking.

Ranking boosts:

- current quest match
- same operator match
- same workflow/template match
- tag overlap with the query
- receipt-backed evidence
- work-ledger/session-brief/task-experience records

The intended priority is current quest first, then same operator, then same workflow, then global memory.

## Context Efficiency Telemetry

Compiled contexts record efficiency metadata:

- total budget and used tokens
- remaining tokens and budget-used ratio
- static versus dynamic token split
- included, excluded, and considered memory hit counts
- exclusion reasons
- working and retrieval candidate counts
- retrieval miss rate
- cache eligibility inputs
- repeated task/template hit rate when runbook or task-experience evidence is included
- compaction savings status

This data is returned by the authenticated `POST /memory/compile` endpoint,
stored on compiled context objects, and written into `memory_compile` receipt
data. The War Room exposes the same telemetry in the Context Efficiency view
under Operations so operators can inspect long-running task context before or
during enterprise workflows.

## Live Proof Paths

Useful proof commands and endpoints:

| Proof | Surface |
|-------|---------|
| Runtime health | `GET /health`, `GET /ready` |
| Compile context | `POST /memory/compile` |
| Memory stats | `GET /memory/stats` |
| Recent memory | `GET /memory/recent` |
| Search memory | `POST /memory/search` |
| Active work | `GET /api/work/active` |
| Manual checkpoint | `POST /api/work/{quest_id}/checkpoint` |
| Scheduler cleanup | `POST /api/scheduler/jobs/memory_cleanup/trigger` |

All protected endpoints require an authenticated War Room session or the configured gateway bearer token.
