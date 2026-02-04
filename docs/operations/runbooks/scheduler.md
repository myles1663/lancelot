# Scheduler Runbook

## Overview
The Scheduler (Chron) subsystem manages periodic and cron-based jobs.

## Feature Flag
`FEATURE_SCHEDULER=true|false` — disable to boot without scheduler.

## Configuration
- Config file: `config/scheduler.yaml`
- Example: `config/scheduler.example.yaml`
- Persistence: `data/scheduler.sqlite`

## Common Operations

### List Jobs
Check War Room Scheduler panel or query the service directly.

### Manual Trigger
Use `run_now(job_id)` or War Room "Run Now" button.

### Disable a Job
Use `disable_job(job_id)` or War Room toggle.

### Troubleshooting
- **Job skipped (not READY)**: System must be in READY state
- **Job skipped (approvals)**: Owner approval required for gated jobs
- **Job failed**: Check skill execution logs
- **SQLite locked**: Only one scheduler instance should run per data directory
