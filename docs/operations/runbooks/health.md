# Health Runbook

## Overview
The Heartbeat subsystem provides liveness and readiness probes.

## Feature Flag
`FEATURE_HEALTH_MONITOR=true|false` — hot-toggleable. When disabled, the background monitor thread stops and `/health/ready` returns a 503 via request-gating middleware. `/health/live` always returns 200 regardless of flag state.

## Endpoints
- `GET /health/live` — always 200 if process running
- `GET /health/ready` — returns snapshot with degraded_reasons

## Architecture
- Background thread runs health checks every 30 seconds (configurable via `interval_s`)
- Sleep uses 1-second increments with a stop event for responsive shutdown (~1s instead of up to 30s)
- Emits receipts on state transitions (healthy → degraded, degraded → recovered)

## Common Operations

### Check System Health
```
GET /health/ready
```
Fields: ready, onboarding_state, local_llm_ready, scheduler_running, degraded_reasons.

### Troubleshooting
- **ready=false**: Check degraded_reasons array for specific failures
- **LLM not responding**: Verify local model server is running
- **Scheduler not running**: Check FEATURE_SCHEDULER flag and scheduler service
- **Monitor not stopping**: The monitor uses 1-second sleep increments — `stop_monitor()` should complete within ~1 second. If it blocks, check for hung health check functions.
