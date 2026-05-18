import { useState } from 'react'
import { Activity, AlertTriangle, CalendarClock, Clock, ListChecks, PlayCircle } from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchHealthReady } from '@/api'
import { fetchSchedulerJobs, enableSchedulerJob, disableSchedulerJob, triggerSchedulerJob, updateSchedulerJobTimezone } from '@/api/scheduler'
import type { SchedulerJob, JobTriggerResponse } from '@/api/scheduler'
import { formatTimestamp } from '@/utils/dateFormat'

// Common IANA timezones for the selector
const TIMEZONE_OPTIONS = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Anchorage',
  'Pacific/Honolulu',
  'UTC',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Asia/Kolkata',
  'Australia/Sydney',
]
import { StatusDot, ConfirmDialog, EmptyState } from '@/components'

// ── Helpers ─────────────────────────────────────────────────────

function formatTrigger(type: string, value: string): string {
  if (type === 'interval') {
    const secs = parseInt(value, 10)
    if (isNaN(secs)) return value
    if (secs >= 3600) return `every ${Math.round(secs / 3600)}h`
    if (secs >= 60) return `every ${Math.round(secs / 60)}m`
    return `every ${secs}s`
  }
  return value
}

function formatLastRun(iso: string | null): string {
  return formatTimestamp(iso)
}

function jobStatusState(status: string | null): 'healthy' | 'error' | 'inactive' {
  if (!status) return 'inactive'
  if (status === 'failed') return 'error'
  return 'healthy'
}

function jobStatusLabel(status: string | null): string {
  if (!status) return 'No runs'
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function formatJobError(error: string | null): string {
  if (!error) return ''
  return error.length > 220 ? `${error.slice(0, 217)}...` : error
}

type SchedulerTileTone = 'accent' | 'healthy' | 'warning' | 'muted'

const schedulerTileToneClass: Record<SchedulerTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function SchedulerTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: SchedulerTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${schedulerTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

// ── Component ───────────────────────────────────────────────────

export function SchedulerPanel() {
  usePageTitle('Scheduler')
  // Health polling (scheduler running status)
  const { data: healthData } = usePolling({ fetcher: fetchHealthReady, interval: 10000 })

  // Jobs polling
  const { data: jobsData, refetch } = usePolling({ fetcher: fetchSchedulerJobs, interval: 10000 })

  // UI state
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [pendingTrigger, setPendingTrigger] = useState<string | null>(null)
  const [triggerResult, setTriggerResult] = useState<JobTriggerResponse | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)
  const [triggering, setTriggering] = useState<string | null>(null)
  const [updatingTz, setUpdatingTz] = useState<string | null>(null)

  // Derived
  const schedulerRunning = healthData?.scheduler_running ?? false
  const lastTick = healthData?.last_scheduler_tick_at
  const jobs = jobsData?.jobs ?? []
  const total = jobsData?.total ?? 0
  const enabledCount = jobsData?.enabled_count ?? 0
  const disabledCount = Math.max(0, total - enabledCount)
  const approvalJobs = jobs.filter(job => job.requires_approvals.length > 0)
  const failedJobs = jobs.filter(job => job.last_run_status === 'failed')
  const cronJobs = jobs.filter(job => job.trigger_type === 'cron')

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleToggle = async (e: React.MouseEvent, job: SchedulerJob) => {
    e.stopPropagation()
    setToggling(job.id)
    try {
      if (job.enabled) {
        await disableSchedulerJob(job.id)
      } else {
        await enableSchedulerJob(job.id)
      }
      refetch()
    } finally {
      setToggling(null)
    }
  }

  const handleTrigger = (jobId: string) => {
    setPendingTrigger(jobId)
  }

  const doTrigger = async (jobId: string) => {
    setPendingTrigger(null)
    setTriggering(jobId)
    try {
      const result = await triggerSchedulerJob(jobId)
      setTriggerResult(result)
      refetch()
      setTimeout(() => setTriggerResult(null), 5000)
    } finally {
      setTriggering(null)
    }
  }

  const handleTimezoneChange = async (jobId: string, tz: string) => {
    setUpdatingTz(jobId)
    try {
      await updateSchedulerJobTimezone(jobId, tz)
      refetch()
    } finally {
      setUpdatingTz(null)
    }
  }

  const pendingJob = jobs.find(j => j.id === pendingTrigger)

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2">
              <CalendarClock className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
                Scheduled Operations
              </div>
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">Scheduler</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Monitor recurring jobs, manual triggers, approval-gated schedules, and recent execution outcomes.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono text-text-muted sm:flex sm:flex-wrap">
            <span className={`rounded border px-2 py-1 ${schedulerRunning ? 'border-state-healthy/40 bg-state-healthy/10 text-state-healthy' : 'border-border-default bg-surface-input'}`}>
              {schedulerRunning ? 'running' : 'stopped'}
            </span>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              jobs {total}
            </span>
            <span className={`rounded border px-2 py-1 ${failedJobs.length > 0 ? 'border-state-error/40 bg-state-error/10 text-state-error' : 'border-border-default bg-surface-input'}`}>
              failed {failedJobs.length}
            </span>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <SchedulerTile
            label="Scheduler"
            value={schedulerRunning ? 'Running' : 'Stopped'}
            detail={lastTick ? `Last tick ${formatTimestamp(lastTick)}` : 'No scheduler tick recorded.'}
            tone={schedulerRunning ? 'healthy' : 'warning'}
          />
          <SchedulerTile
            label="Total Jobs"
            value={total}
            detail={`${enabledCount} enabled, ${disabledCount} disabled.`}
            tone="accent"
          />
          <SchedulerTile
            label="Cron Jobs"
            value={cronJobs.length}
            detail="Wall-clock scheduled jobs."
          />
          <SchedulerTile
            label="Approval Gated"
            value={approvalJobs.length}
            detail="Jobs requiring approval scopes."
            tone={approvalJobs.length > 0 ? 'warning' : 'muted'}
          />
          <SchedulerTile
            label="Failed Last Run"
            value={failedJobs.length}
            detail="Jobs whose most recent run failed."
            tone={failedJobs.length > 0 ? 'warning' : 'healthy'}
          />
        </div>
      </div>

      {/* Trigger Result Banner */}
      {triggerResult && (
        <div className={`mb-4 p-3 rounded-lg border flex items-center justify-between ${
          triggerResult.success
            ? 'bg-state-healthy/10 border-state-healthy/30'
            : 'bg-state-error/10 border-state-error/30'
        }`}>
          <span className={`text-sm ${triggerResult.success ? 'text-state-healthy' : 'text-state-error'}`}>
            {triggerResult.success
              ? `Job "${triggerResult.id}" triggered successfully (${triggerResult.duration_ms}ms)`
              : triggerResult.skip_reason
                ? `Job "${triggerResult.id}" skipped: ${triggerResult.skip_reason}`
                : `Job "${triggerResult.id}" failed: ${triggerResult.error}`
            }
          </span>
          <button
            onClick={() => setTriggerResult(null)}
            className="text-xs text-text-muted hover:text-text-primary ml-4"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Scheduled Jobs */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <ListChecks className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                Scheduled Jobs
              </h3>
            </div>
            <p className="mt-1 text-xs text-text-muted">
              Expand a job to inspect schedule details, readiness requirements, approval scopes, and run history.
            </p>
          </div>
          <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
            {jobs.length}
          </span>
        </div>

        {jobs.length === 0 ? (
          <EmptyState
            title="No Scheduled Jobs"
            description="No jobs registered. Add jobs to config/scheduler.yaml and restart the scheduler."
            icon="&#9200;"
          />
        ) : (
          <div className="space-y-1">
            {jobs.map(job => {
              const isExpanded = expanded.has(job.id)
              const isToggling = toggling === job.id
              const isTriggering = triggering === job.id

              return (
                <div
                  key={job.id}
                  className="bg-surface-card-elevated rounded-md border border-border-default overflow-hidden"
                >
                  {/* Job row */}
                  <div
                    className="flex flex-col gap-3 p-3 cursor-pointer hover:bg-surface-input/50 transition-colors xl:flex-row xl:items-center xl:justify-between"
                    onClick={() => toggleExpand(job.id)}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className={`text-[10px] transition-transform ${isExpanded ? 'rotate-90' : ''}`}>
                        &#9654;
                      </span>
                      <span className="text-sm font-medium text-text-primary truncate">
                        {job.name}
                      </span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent-primary/15 text-accent-primary whitespace-nowrap font-mono">
                        {job.trigger_type === 'interval' ? 'interval' : 'cron'}
                      </span>
                      <span className="text-[10px] text-text-muted font-mono whitespace-nowrap">
                        {formatTrigger(job.trigger_type, job.trigger_value)}
                      </span>
                      {job.trigger_type === 'cron' && job.timezone && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-input text-text-muted whitespace-nowrap font-mono">
                          {job.timezone.replace('America/', '').replace('_', ' ')}
                        </span>
                      )}
                      {job.requires_approvals.length > 0 && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-state-degraded/15 text-state-degraded whitespace-nowrap">
                          approval
                        </span>
                      )}
                      {job.last_run_status === 'failed' && (
                        <span className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded bg-state-error/15 text-state-error whitespace-nowrap">
                          <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                          failed
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2 flex-shrink-0 xl:ml-3">
                      <StatusDot
                        state={job.enabled ? 'healthy' : 'inactive'}
                        label={job.enabled ? 'Enabled' : 'Disabled'}
                      />
                      {/* Run Now button */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleTrigger(job.id)
                        }}
                        disabled={isTriggering || !job.enabled}
                        className={`px-2.5 py-1 text-[11px] font-medium rounded transition-colors ${
                          job.enabled
                            ? 'bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25'
                            : 'bg-surface-input text-text-muted cursor-not-allowed'
                        }`}
                      >
                        {isTriggering ? 'Running...' : 'Run Now'}
                      </button>

                      {/* Toggle switch */}
                      <button
                        onClick={(e) => handleToggle(e, job)}
                        disabled={isToggling}
                        className={`relative w-11 h-6 rounded-full transition-colors duration-200 ${
                          job.enabled ? 'bg-state-healthy' : 'bg-surface-input border border-border-default'
                        } ${isToggling ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                      >
                        <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform duration-200 ${
                          job.enabled ? 'translate-x-5' : 'translate-x-0'
                        }`} />
                      </button>
                    </div>
                  </div>

                  {/* Expanded details */}
                  {isExpanded && (
                    <div className="px-3 pb-3 pt-0 border-t border-border-default/50 space-y-3">
                      {/* Description */}
                      {job.description && (
                        <p className="text-xs text-text-secondary leading-relaxed mt-2">
                          {job.description}
                        </p>
                      )}

                      {/* Details grid */}
                      <div className="grid grid-cols-2 gap-x-6 gap-y-2 mt-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider">Skill:</span>
                          <span className="text-xs font-mono text-text-primary">
                            {job.skill || '(none)'}
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider">Timeout:</span>
                          <span className="text-xs font-mono text-text-primary">{job.timeout_s}s</span>
                        </div>
                        {job.trigger_type === 'cron' && (
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] text-text-muted uppercase tracking-wider">Timezone:</span>
                            <select
                              value={job.timezone || 'UTC'}
                              onChange={(e) => handleTimezoneChange(job.id, e.target.value)}
                              disabled={updatingTz === job.id}
                              className="text-xs font-mono bg-surface-input border border-border-default rounded px-1.5 py-0.5 text-text-primary cursor-pointer disabled:opacity-50"
                            >
                              {TIMEZONE_OPTIONS.map(tz => (
                                <option key={tz} value={tz}>{tz}</option>
                              ))}
                              {/* Show current value if not in predefined list */}
                              {job.timezone && !TIMEZONE_OPTIONS.includes(job.timezone) && (
                                <option value={job.timezone}>{job.timezone}</option>
                              )}
                            </select>
                          </div>
                        )}
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider">Requires Ready:</span>
                          <span className="text-xs text-text-primary">{job.requires_ready ? 'Yes' : 'No'}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider">Approvals:</span>
                          <span className="text-xs text-text-primary">
                            {job.requires_approvals.length > 0
                              ? job.requires_approvals.join(', ')
                              : 'None'}
                          </span>
                        </div>
                      </div>

                      {/* Run history */}
                      <div className="flex items-center gap-6 pt-2 border-t border-border-default/30">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider">Last Run:</span>
                          <span className="text-xs font-mono text-text-primary">
                            {formatLastRun(job.last_run_at)}
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider">Status:</span>
                          <StatusDot
                            state={jobStatusState(job.last_run_status)}
                            label={jobStatusLabel(job.last_run_status)}
                          />
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-text-muted uppercase tracking-wider">Total Runs:</span>
                          <span className="text-xs font-mono text-text-primary">{job.run_count}</span>
                        </div>
                      </div>

                      {job.last_run_status === 'failed' && job.last_run_error && (
                        <div className="rounded border border-state-error/30 bg-state-error/10 px-3 py-2">
                          <div className="text-[10px] text-state-error uppercase tracking-wider mb-1">Last Failure</div>
                          <div className="text-xs font-mono text-state-error break-words">
                            {formatJobError(job.last_run_error)}
                          </div>
                        </div>
                      )}

                      {/* Registered at */}
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-text-muted uppercase tracking-wider">Registered:</span>
                        <span className="text-xs font-mono text-text-muted">
                          {formatTimestamp(job.registered_at)}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-state-healthy" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Schedule Posture</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Enabled jobs run on their configured trigger while disabled jobs remain visible for operator inspection.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <PlayCircle className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Manual Trigger</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Run Now uses the same job path immediately, after explicit confirmation, without changing the schedule.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-state-warning" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Failure Signal</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Failed last runs stay visible in the job row and expanded details so operators can triage quickly.
          </p>
        </div>
      </div>

      {/* Trigger confirmation dialog */}
      <ConfirmDialog
        open={pendingTrigger !== null}
        title="Trigger Job Manually"
        description={`Run "${pendingJob?.name ?? pendingTrigger}" immediately? This bypasses the normal schedule and executes the job now.`}
        confirmLabel="Run Now"
        onConfirm={() => pendingTrigger && doTrigger(pendingTrigger)}
        onCancel={() => setPendingTrigger(null)}
      />
    </div>
  )
}
