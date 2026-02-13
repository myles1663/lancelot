// ============================================================
// Scheduler API
// Endpoints for listing, enabling/disabling, and triggering scheduled jobs
// ============================================================

import { apiGet, apiPost, apiPatch } from './client'

// ── Response Types ──────────────────────────────────────────

export interface SchedulerJob {
  id: string
  name: string
  skill: string
  enabled: boolean
  trigger_type: 'interval' | 'cron'
  trigger_value: string
  timezone: string
  requires_ready: boolean
  requires_approvals: string[]
  timeout_s: number
  description: string
  last_run_at: string | null
  last_run_status: string | null
  run_count: number
  registered_at: string
}

export interface JobListResponse {
  jobs: SchedulerJob[]
  total: number
  enabled_count: number
}

export interface JobToggleResponse {
  id: string
  enabled: boolean
}

export interface JobTimezoneResponse {
  id: string
  timezone: string
}

export interface JobTriggerResponse {
  id: string
  executed: boolean
  success: boolean
  skip_reason: string | null
  error: string | null
  duration_ms: number
}

// ── API Functions ───────────────────────────────────────────

export const fetchSchedulerJobs = () =>
  apiGet<JobListResponse>('/api/scheduler/jobs')

export const enableSchedulerJob = (jobId: string) =>
  apiPost<JobToggleResponse>(`/api/scheduler/jobs/${jobId}/enable`)

export const disableSchedulerJob = (jobId: string) =>
  apiPost<JobToggleResponse>(`/api/scheduler/jobs/${jobId}/disable`)

export const triggerSchedulerJob = (jobId: string) =>
  apiPost<JobTriggerResponse>(`/api/scheduler/jobs/${jobId}/trigger`)

export const updateSchedulerJobTimezone = (jobId: string, timezone: string) =>
  apiPatch<JobTimezoneResponse>(`/api/scheduler/jobs/${jobId}/timezone`, { timezone })
