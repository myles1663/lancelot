# Command Center Execution Runbook

## Overview
Command Center execution is the operator-facing path for governed chat, tool use, approval, and completion reporting.

The production standard is receipt-backed completion: Lancelot should not report a write, command, connector call, or workflow as complete unless the corresponding governed tool call succeeded and the result was included in the execution receipts.

## Completion Contract
An interaction may be reported as complete only when:

- all required governed tool calls have completed successfully
- no unresolved tool failure, exception, rejection, or approval pause remains
- writes targeted the intended workspace boundary
- validation commands requested by the task have run and returned acceptable results
- the final response is assembled from tool receipts, not from planner narration

If a model attempts to answer with a completion claim while unresolved tool failures remain, the agentic loop must block that claim and return a receipt-based incomplete status instead.

## Workspace Boundaries
Use explicit workspace roots for all writes:

| Workspace | Purpose |
|-----------|---------|
| `/home/lancelot/app` | Lancelot self-development changes only |
| `/home/lancelot/workspace` | user artifacts, local generated projects, exports |

`repo_writer` receipts include the resolved workspace, target path, relative path, and write scope. Use those fields to confirm a change landed in the intended boundary.

## Approval Cards
Approval cards should explain the operator intent in plain language before showing raw parameters.

Required context:

- what the user asked Lancelot to do
- what tool will run
- what concrete target will change or be contacted
- why this is governed
- whether the approval covers one exact operation or a grouped batch
- what the approval does not authorize

Grouped approvals are appropriate when multiple governed actions belong to the same user request and have already been enumerated as exact tool calls.

Approval cards should read like a responsible employee asking a manager for approval:

```text
I need approval to edit 2 repository files.

What I am trying to do: update the local dashboard workflow.

Approval scope:
- 2 exact repository file operations
- Only the files listed below
- Workspace root: `/home/lancelot/workspace`

This approval does not cover:
- Files not listed below
- Follow-up writes after this approval group
- Git commits, pushes, deployments, or external calls unless separately approved
```

Raw tool names, request IDs, and full parameters should remain available under technical details, not lead the card.

## Progress Surfaces
War Room should show two progress streams:

- `chat.progress` for governance phases such as preflight, classification, approval, execution, and finalization
- `toolflow.*` for actual tool loop events such as tool start, tool completion, approval block, quest completion, or quest failure

Approval pauses are not failures. They should surface as blocked/waiting states with the approval ID where available.

## Async Command Runs
War Room text commands should use `POST /chat/async` for governed execution. The HTTP request only accepts the work and returns a run record; the actual model/tool loop continues in the gateway background task pool.

Run state is persisted in `/home/lancelot/data/chat/async_runs.sqlite` and is available through:

- `GET /api/chat/runs`
- `GET /api/chat/runs/{run_id}`
- `POST /api/chat/runs/{run_id}/cancel`
- `POST /api/chat/runs/{run_id}/retry`

Active work state is mirrored into `/home/lancelot/data/work/work_ledger.sqlite`. This ledger is the compact resume surface for long-running or multiday work: objective, status, phase, blocker, next action, recent ledger events, and checkpoint summaries. It is intentionally separate from chat transcripts and TaskRun execution state.

The active-work API is available through:

- `GET /api/work/active`
- `GET /api/work/{quest_id}`
- `POST /api/work/{quest_id}/checkpoint`
- `POST /api/work/{quest_id}/resume`
- `POST /api/work/{quest_id}/archive`

The gateway emits these WebSocket events as the run advances:

| Event | Meaning |
|-------|---------|
| `chat.run_queued` | The command was accepted and persisted. |
| `chat.run_started` | The background task began executing the chat turn. |
| `chat.run_progress` | The persisted run was updated with the latest governance phase and timing data. |
| `chat.run_completed` | The run completed and contains the final assistant response. |
| `chat.run_blocked` | The run paused for approval or another operator action. |
| `chat.run_failed` | The run failed and contains a bounded error summary. |
| `chat.run_cancelled` | The operator cancelled the run before a terminal response was accepted. |

Command Center must render queued/running runs as active work instead of holding the browser request open. Terminal run events are the source of the final assistant message; the queue acknowledgement is not a completion response.

WebSocket events are the primary live update path, but they are not the only source of truth. Command Center should also reconcile recent persisted runs from `GET /api/chat/runs` at a low rate. Reconciliation restores active queued/running runs after browser refresh or transient event loss. Terminal runs fetched through reconciliation should be rendered as final assistant messages only when the UI had already tracked that run as active, otherwise old completed runs can be replayed into chat history.

For long-running work, Command Center should also reconcile `GET /api/work/active`. The work ledger is the operator-facing source of "what is still open?" after browser refresh, gateway restart, context compaction, or a multi-day pause. It should not be used as a full transcript or proof source; receipts remain the evidence layer.

Governed model/tool turns first persist a `waiting_worker_slot` progress event and remain queued until the async worker slot is acquired. After the slot is acquired, the gateway persists an `execution` progress event with `wait_reason=execution_start` before entering the model/tool path. This keeps Command Center from presenting a queued turn as active execution while it is waiting behind earlier governed work, and it gives the operator a visible transition when execution actually starts.

Exact runtime status commands (`status`, `/status`, `system status`, `runtime status`, `health`, `health check`) are deterministic fast-path commands. They return bounded health state directly after authentication and do not enter the classifier, frontier model, tool loop, or governed async worker-slot queue.

For live operator smoke testing, use `GET /api/operator/smoke` from an authenticated War Room/admin session. This endpoint runs the read-only operational report inside the live gateway process and returns the report plus `source=live_gateway`. Do not import `gateway._try_handle_operational_report_command` from a standalone Python process to judge production health; standalone imports do not run the ASGI startup lifecycle, so scheduler and local-model state can look unavailable even when the container is healthy.

Run records include bounded observability fields:

- `progress_events`: last 100 phase/message updates with elapsed milliseconds from queue time
- `phase_timings_ms`: accumulated elapsed time by phase
- `total_elapsed_ms`: total queue-to-current or queue-to-terminal elapsed time
- `last_progress_message`: operator-facing status text for the active run card

Progress events may also include bounded degraded-state metadata:

- `severity`: `info`, `warning`, or `error`
- `degraded`: true when the current run entered a reduced-assurance path
- `degraded_reason`: short operator-facing reason, such as local scrub fallback or policy block
- `wait_reason`: short machine-readable wait class for operator display. Current values include `worker_slot`, `execution_start`, `provider_call`, `approval`, `tool_execution`, and `finalization`.

Command Center must render these degraded progress events while the run is active. The operator should not need to open logs or wait for final receipts to learn that a privacy scrub path fell back or blocked frontier egress.

Command Center must also render wait reasons as plain-language status. A user should be able to distinguish "waiting for a governed worker slot" from "starting governed execution", "waiting on the provider", "waiting for Commander approval", "executing a governed tool", and "finalizing response/proof" without reading logs.

If an active queued/running run has not received a new progress event for a long quiet phase, Command Center should keep the run visible and flag it as slow progress rather than implying completion or failure. The operator-facing card should include the age of the latest progress event and the last observed phase, for example: `No new progress for 1m 12s. Last phase: Provider Call.` This signal is separate from degraded privacy/runtime warnings.

On gateway startup, queued/running records older than `LANCELOT_CHAT_RUN_STALE_AFTER_S` are marked failed with a restart-stale reason. This prevents stale "running" rows from surviving a process restart and making War Room look hung.

Run cancellation is cooperative. Cancelling a queued/running/blocked run marks the persisted run `cancelled`, unblocks the Command Center UI, emits `chat.run_cancelled`, and prevents late worker completion from rewriting the operator-visible state. If a synchronous provider call or tool call has already entered a blocking section, the underlying worker may still return later; this control is not a hard process kill.

Retry is available for `failed`, `cancelled`, and operator-approved `blocked` runs. The retry endpoint creates a new run with `retry_of_run_id` and `retry_count` set, then replays the retained original message through the same governed async path. It does not broaden the original scope; approved blocked runs succeed only if the underlying governed requests are approved or match a narrow Sentry approval intent. Today that intent reuse is limited to same-file shared-workspace text artifacts where the resumed `repo_writer` call has the same action, target, and equivalent text content. Repository/application writes, patches, deletes, commands, connector calls, and changed targets still require exact approval.

Work resume is available for ledger items whose retained chat run is `failed`, `cancelled`, or `blocked`. `POST /api/work/{quest_id}/resume` creates a new async chat run from the retained original message and records the retry in the ledger. It must not manufacture a new instruction from the current UI text.

When a retry reaches a terminal state, the original blocked ledger item is marked superseded so the operator does not see stale blocked work after a successful retry. The retry lineage remains available through chat-run receipts and ledger events.

Operators can manually clear stale work with `POST /api/work/{quest_id}/archive`. Archive is a terminal ledger state: it hides the item from active work, preserves events and checkpoints, and cancels the retained chat run if it is still queued, running, or blocked.

When archived work has unresolved ActionCards with the same `quest_id`, the gateway archives those cards as `resolved_action=archived` without approving or denying the underlying request. Operators can also archive a stale standalone card with `POST /api/actioncards/{card_id}/archive`. This path is for cleanup only; it must not be used to approve or deny governed work.

Checkpoints can be created at operator pause boundaries with `POST /api/work/{quest_id}/checkpoint`. A checkpoint captures a bounded summary of completed work, pending work, open decisions, files touched, approvals, and receipt IDs. The context compiler injects the latest active-work block into future turns so Lancelot can resume from durable state instead of relying on a long chat transcript.

The gateway also checkpoints open work automatically during safe lifecycle boundaries:

- gateway shutdown checkpoints all open work with reason `gateway_shutdown`
- gateway startup checkpoints stale open work with reason `gateway_startup_stale_work`
- `GET /api/work/active` checkpoints quiet active work after `LANCELOT_WORK_QUIET_CHECKPOINT_AFTER_S` seconds with reason `quiet_phase`

Repeated checkpoint reasons are deduped inside the configured quiet window so polling the Command Center does not spam the ledger.

File uploads still use `/chat/upload` synchronously because multipart payloads carry in-memory file state that should not be replayed from the async run store.

## Approval Resume Loop

Approving an ActionCard authorizes the bounded operation described in that card; it does not silently resume the command loop.

After an approval is confirmed by the backend, War Room should show a resume prompt next to the resolved ActionCard. Pressing **Continue** must requeue the exact blocked async run referenced by that ActionCard's `quest_id`, preserving the original message, governance path, receipts, and completion contract. It must not send a generic `continue` chat message, because that can resume the wrong thread when multiple approved cards exist.

Approval cards must describe scope in operator language. For shared-workspace text artifacts, the card may say the approval is bounded rather than exact because Sentry can tolerate harmless resume differences such as trailing punctuation on the same file. For repository/application writes and command execution, the card must continue to describe exact scope.

The UI must not mark an approval as resolved until the backend resolver accepts the decision. If resolution fails, the card should remain actionable and show the resolver error instead of presenting a resume path.

`actioncard_resolved` events should include both legacy and explicit fields:

- `button_id` / `channel`
- `resolved_action` / `resolved_channel`
- `quest_id`
- `result_status`

## Short Follow-Ups During Approval Waits
Short acknowledgements such as `ok`, `sounds good`, and `ok sounds good` must not be sent back through a model when the prior assistant response was an approval wait. Lancelot should answer deterministically that it is paused for Commander approval, point the operator to the ActionCard, and tell them to send `continue` after approval.

This prevents the operator from seeing an inert echo response while the system is actually waiting on a governance decision.

These deterministic follow-ups are evaluated before classifier/model routing so simple control replies do not pay the latency of a full reasoning turn.

## Troubleshooting

### Command Center only says "Processing"
Check:

1. War Room WebSocket is connected to `/ws/warroom`
2. authenticated session cookie is present
3. backend logs show `chat.progress` events
4. `FEATURE_TOOL_FLOW_STREAMING` if tool-level progress is expected
5. model latency if progress reached "Preparing governed model request" and then stalls

### Tool fails with a command that works in another shell
Check the execution target. `command_runner` may execute in the Linux container, the Windows host bridge, or Tool Fabric depending on runtime flags. Windows shell builtins such as `type` and `dir` are rejected in POSIX runtimes with a suggested equivalent command.

### Lancelot reports incomplete work
Open the latest receipts for the quest. The completion contract intentionally prefers an incomplete status over a false success claim when any governed action is unresolved.
