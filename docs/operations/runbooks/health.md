# Health Runbook

## Overview
The Heartbeat subsystem provides liveness and readiness probes.

## Feature Flag
`FEATURE_HEALTH_MONITOR=true|false` — disable background health monitoring.

## Endpoints
- `GET /health/live` — always 200 if process running
- `GET /health/ready` — returns snapshot with degraded_reasons

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
