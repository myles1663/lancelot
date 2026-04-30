# Hive Agent Mesh Runbook

Operational procedures for the Hive Agent Mesh sub-agent system.

**Feature flag:** `FEATURE_HIVE` (default: `false`)

---

## Checking Mesh Status

```bash
curl http://localhost:8000/api/hive/status
```

Expected response:
```json
{
  "status": "idle",
  "enabled": true,
  "active_agents": 0,
  "max_agents": 10,
  "quest_id": null,
  "goal": null
}
```

Status values: `idle`, `decomposing`, `executing`, `replanning`, `failed`.

## Viewing the Roster

```bash
# All agents (active + archived)
curl http://localhost:8000/api/hive/roster

# Active only
curl http://localhost:8000/api/hive/agents

# Archived (collapsed) only
curl http://localhost:8000/api/hive/agents/history
```

## Submitting a Task

```bash
curl -X POST http://localhost:8000/api/hive/tasks \
  -H "Content-Type: application/json" \
  -d '{"goal": "Research competitor pricing and summarize findings", "context": {}}'
```

Returns a `HiveTaskResult` with `quest_id`, `success`, and per-agent results.

---

## Operator Interventions

All interventions require a reason string (validated non-empty).

### Pause an Agent

```bash
curl -X POST http://localhost:8000/api/hive/agents/{agent_id}/pause \
  -H "Content-Type: application/json" \
  -d '{"reason": "Need to review output before continuing"}'
```

### Resume a Paused Agent

```bash
curl -X POST http://localhost:8000/api/hive/agents/{agent_id}/resume
```

### Kill an Agent

```bash
curl -X POST http://localhost:8000/api/hive/agents/{agent_id}/kill \
  -H "Content-Type: application/json" \
  -d '{"reason": "Agent is taking wrong approach"}'
```

### Modify (Kill + Replan)

```bash
curl -X POST http://localhost:8000/api/hive/agents/{agent_id}/modify \
  -H "Content-Type: application/json" \
  -d '{"reason": "Wrong approach", "feedback": "Focus on public pricing pages only"}'
```

### Emergency Kill All

```bash
curl -X POST http://localhost:8000/api/hive/kill-all \
  -H "Content-Type: application/json" \
  -d '{"reason": "Emergency stop - unexpected behavior detected"}'
```

---

## Monitoring

### Active Agents

Check the War Room HiveAgentMesh page for real-time monitoring with 3-second polling. Or via API:

```bash
curl http://localhost:8000/api/hive/agents
```

### Receipt Trails

View all receipts for a task:

```bash
curl http://localhost:8000/api/hive/tasks/{quest_id}
```

Hierarchical view:

```bash
curl http://localhost:8000/api/hive/tasks/{quest_id}/tree
```

### Intervention History

```bash
curl http://localhost:8000/api/hive/interventions
curl http://localhost:8000/api/hive/interventions/{quest_id}
```

---

## Troubleshooting

### Agent stuck in EXECUTING state

1. Check action count — may be approaching `max_actions_per_agent` limit
2. Check if waiting for governance approval (paused internally)
3. Try pausing and resuming: `POST /api/hive/agents/{id}/pause` then `/resume`
4. If unresponsive, kill: `POST /api/hive/agents/{id}/kill`

### Governance denial

**Symptom:** Agent collapsed with reason `governance_denied`

1. Check the governance receipt for the denied capability
2. The scoped Soul may be too restrictive — check `allowed_categories` in the task spec
3. If the action legitimately needs higher autonomy, resubmit with adjusted context

### Decomposition failure

**Symptom:** Task submission returns error

1. Check that the flagship_deep LLM lane is available (Gemini Pro / GPT-4o / Claude)
2. Check the goal description — overly vague goals may fail decomposition
3. Check `max_subtasks_per_decomposition` in `config/hive.yaml`

### No identical retry error

**Symptom:** Replan fails with "identical plan" error

This means the LLM generated the same plan as before. The Architect enforces plan diversity after failure. Provide more specific feedback via the MODIFY intervention, or submit a new task with different context.

---

## Configuration Tuning

### `config/hive.yaml` Key Settings

| Setting | Default | Effect |
|---------|---------|--------|
| `max_concurrent_agents` | 10 | Max active agents (paused count too) |
| `default_task_timeout` | 300 | Seconds before timeout collapse |
| `max_actions_per_agent` | 50 | Actions before max_actions collapse |
| `max_subtasks_per_decomposition` | 20 | Cap on LLM decomposition |
| `default_control_method` | supervised | Agent autonomy level |
| `collapse_on_governance_violation` | true | Collapse vs. pause on denial |
| `never_retry_identical_plan` | true | Enforce plan diversity |

### Reducing Resource Usage

- Lower `max_concurrent_agents` to reduce thread pool size
- Lower `max_actions_per_agent` to limit per-agent work
- Set `default_control_method` to `manual_confirm` for maximum control
