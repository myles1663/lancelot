# Scheduler Runbook

## Overview
The Scheduler (Chron) subsystem manages periodic and cron-based jobs.

## Feature Flag
`FEATURE_SCHEDULER=true|false` — hot-toggleable. Requires `FEATURE_SKILLS` to be enabled for job execution. The flags API enforces this dependency.

## Configuration
- Config file: `config/scheduler.yaml`
- Example: `config/scheduler.example.yaml`
- Persistence: `data/scheduler.sqlite`

## Architecture
- Tick loop runs every 60 seconds, evaluating cron/interval triggers for all enabled jobs
- Per-job locking prevents concurrent execution of the same job (e.g., tick loop + manual `run_now`)
- Sleep uses 1-second increments with a stop event for responsive shutdown
- Cron evaluation is timezone-aware (jobs specify their timezone, evaluated in local time)
- Double-fire prevention: skips if the job already ran in the same minute

## Common Operations

### List Jobs
Check War Room Scheduler panel or query the service directly.

### Manual Trigger
Use `run_now(job_id)` or War Room "Run Now" button. If the job is already executing (from the tick loop), the manual trigger will be skipped with a "concurrent execution blocked" receipt.

### Disable a Job
Use `disable_job(job_id)` or War Room toggle.

### Troubleshooting
- **Job skipped (not READY)**: System must be in READY state
- **Job skipped (approvals)**: Owner approval required for gated jobs
- **Job skipped (concurrent)**: Another execution of the same job is in progress — check receipts
- **Job failed**: Check skill execution logs
- **SQLite locked**: Only one scheduler instance should run per data directory
