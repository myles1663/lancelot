import { useMemo, type ReactNode } from 'react'
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleSlash,
  Clock3,
  Cpu,
  DatabaseZap,
  HeartPulse,
  Loader2,
  Radio,
  RefreshCw,
  Timer,
} from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchHealth, fetchHealthReady } from '@/api'
import { StatusDot, Skeleton } from '@/components'
import type { SystemState } from '@/components'
import type { HealthCheckResponse, HealthReadyResponse, LocalModelRoleStatus } from '@/types/api'
import { formatRelativeTime, formatTimestamp, formatUptime } from '@/utils/dateFormat'
import { getErrorMessage } from '@/utils/errors'

type HealthTone = 'healthy' | 'warning' | 'error' | 'muted' | 'accent'

interface ReadinessItem {
  label: string
  value: string
  state: SystemState
  detail: string
}

const TONE_CLASS: Record<HealthTone, string> = {
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-degraded/30 bg-state-degraded/10 text-state-degraded',
  error: 'border-state-error/30 bg-state-error/10 text-state-error',
  muted: 'border-border-default bg-surface-card text-text-muted',
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
}

function componentState(status: string | undefined): SystemState {
  const normalized = (status || '').toLowerCase()
  if (['ok', 'healthy', 'ready', 'running', 'online'].includes(normalized)) return 'healthy'
  if (['degraded', 'warning', 'loaded'].includes(normalized)) return 'degraded'
  if (['error', 'failed', 'offline', 'unhealthy'].includes(normalized)) return 'error'
  return 'inactive'
}

function componentLabel(status: string | undefined): string {
  if (!status) return 'Unknown'
  if (status === 'ok') return 'OK'
  return status.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function overallStatus(
  health: HealthCheckResponse | null,
  ready: HealthReadyResponse | null,
): { label: string; state: SystemState; tone: HealthTone; detail: string } {
  if (!health || !ready) {
    return { label: 'LOADING', state: 'inactive', tone: 'muted', detail: 'Waiting for health and readiness snapshots.' }
  }
  if (!ready.ready) {
    return { label: 'NOT READY', state: 'degraded', tone: 'warning', detail: 'Readiness gate is not currently satisfied.' }
  }
  if (ready.degraded_reasons.length > 0) {
    return { label: 'DEGRADED', state: 'degraded', tone: 'warning', detail: `${ready.degraded_reasons.length} degraded reason${ready.degraded_reasons.length === 1 ? '' : 's'} reported.` }
  }
  const componentValues = Object.values(health.components || {})
  const failingComponents = componentValues.filter((status) => componentState(status) !== 'healthy').length
  if (failingComponents > 0) {
    return { label: 'ATTENTION', state: 'degraded', tone: 'warning', detail: `${failingComponents} component${failingComponents === 1 ? '' : 's'} outside OK state.` }
  }
  return { label: 'HEALTHY', state: 'healthy', tone: 'healthy', detail: 'Readiness and component checks are currently clean.' }
}

function roleState(role: LocalModelRoleStatus): SystemState {
  if (role.ready) return 'healthy'
  if (role.loaded || role.enabled || role.configured) return 'degraded'
  return 'inactive'
}

function readable(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function onboardingState(value: string | undefined): SystemState {
  const normalized = (value || '').toLowerCase()
  if (['complete', 'completed', 'ready'].includes(normalized)) return 'healthy'
  if (['error', 'failed'].includes(normalized)) return 'error'
  if (!normalized || normalized === 'unknown') return 'inactive'
  return 'degraded'
}

function percent(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--'
  return `${value.toFixed(value >= 10 ? 1 : 2)}%`
}

function compactTimestamp(value: string | null | undefined): string {
  if (!value) return 'Never'
  return formatRelativeTime(value)
}

function SummaryTile({
  label,
  value,
  detail,
  icon,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  icon: ReactNode
  tone?: HealthTone
}) {
  return (
    <div className={`min-w-0 rounded-lg border p-4 ${TONE_CLASS[tone]}`}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider opacity-80">{label}</span>
        <span className="shrink-0">{icon}</span>
      </div>
      <div className="mt-3 truncate text-2xl font-semibold leading-tight text-text-primary" title={String(value)}>
        {value}
      </div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function FieldRow({
  label,
  value,
  state,
}: {
  label: string
  value: string | number
  state?: SystemState
}) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 rounded border border-border-default bg-surface-card-elevated px-3 py-2">
      <span className="min-w-0 truncate text-sm text-text-primary">{label}</span>
      {state ? (
        <StatusDot state={state} label={String(value)} className="shrink-0" />
      ) : (
        <span className="min-w-0 truncate text-right font-mono text-xs text-text-secondary" title={String(value)}>
          {value}
        </span>
      )}
    </div>
  )
}

function ReadinessCard({ item }: { item: ReadinessItem }) {
  const tone = item.state === 'healthy' ? 'healthy' : item.state === 'error' ? 'error' : item.state === 'degraded' ? 'warning' : 'muted'
  return (
    <div className={`rounded-lg border p-3 ${TONE_CLASS[tone]}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-text-primary">{item.label}</div>
          <div className="mt-1 text-xs leading-5 text-text-muted">{item.detail}</div>
        </div>
        <StatusDot state={item.state} label={item.value} className="shrink-0" />
      </div>
    </div>
  )
}

function LoadingRows({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, index) => (
        <Skeleton key={index} variant="row" />
      ))}
    </div>
  )
}

export function HealthDashboard() {
  usePageTitle('Health')

  const {
    data: health,
    error: healthError,
    loading: healthLoading,
    refetch: refetchHealth,
  } = usePolling<HealthCheckResponse>({
    fetcher: fetchHealth,
    interval: 5000,
  })
  const {
    data: ready,
    error: readyError,
    loading: readyLoading,
    refetch: refetchReady,
  } = usePolling<HealthReadyResponse>({
    fetcher: fetchHealthReady,
    interval: 5000,
  })

  const status = overallStatus(health, ready)
  const componentEntries = Object.entries(health?.components ?? {})
  const componentCounts = useMemo(() => {
    return componentEntries.reduce(
      (counts, [, value]) => {
        counts[componentState(value)] += 1
        return counts
      },
      { healthy: 0, degraded: 0, error: 0, inactive: 0 } as Record<SystemState, number>,
    )
  }, [componentEntries])

  const readinessItems = useMemo<ReadinessItem[]>(() => {
    if (!ready) return []
    return [
      {
        label: 'Ready State',
        value: ready.ready ? 'Ready' : 'Not Ready',
        state: ready.ready ? 'healthy' : 'degraded',
        detail: 'Primary readiness gate used by operators and automation.',
      },
      {
        label: 'Onboarding',
        value: readable(ready.onboarding_state || 'unknown'),
        state: onboardingState(ready.onboarding_state),
        detail: 'Instance setup posture.',
      },
      {
        label: 'Local LLM',
        value: ready.local_llm_ready ? 'Ready' : ready.local_llm_loaded ? 'Loaded' : 'Unavailable',
        state: ready.local_llm_ready ? 'healthy' : ready.local_llm_loaded ? 'degraded' : 'inactive',
        detail: ready.local_llm_status ? readable(ready.local_llm_status) : 'Runtime readiness status.',
      },
      {
        label: 'Scheduler',
        value: ready.scheduler_running ? 'Running' : 'Stopped',
        state: ready.scheduler_running ? 'healthy' : 'inactive',
        detail: `Last tick: ${compactTimestamp(ready.last_scheduler_tick_at)}`,
      },
      {
        label: 'Health Tick',
        value: compactTimestamp(ready.last_health_tick_at),
        state: ready.last_health_tick_at ? 'healthy' : 'inactive',
        detail: 'Most recent internal health monitor tick.',
      },
      {
        label: 'Snapshot',
        value: compactTimestamp(ready.timestamp),
        state: ready.timestamp ? 'healthy' : 'inactive',
        detail: 'Freshness of this readiness response.',
      },
    ]
  }, [ready])

  const localModelRoles = Object.entries(health?.local_llm?.roles ?? {})
  const hasErrors = Boolean(healthError || readyError)
  const degradedReasons = ready?.degraded_reasons ?? []
  const attentionCount = degradedReasons.length + componentCounts.degraded + componentCounts.error

  const refresh = () => {
    refetchHealth()
    refetchReady()
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <section className={`rounded-lg border p-4 ${TONE_CLASS[status.tone]}`}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-accent-primary">
              <HeartPulse className="h-4 w-4" aria-hidden="true" />
              System Health
            </div>
            <h1 className="mt-2 text-2xl font-semibold text-text-primary">Health Dashboard</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">
              Monitor readiness, degraded reasons, component state, scheduler activity, and local model runtime health from one operator view.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-3 xl:min-w-[30rem]">
            <div className="rounded border border-border-default bg-surface-card-elevated p-3">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Posture</div>
              <div className="mt-1 flex items-center gap-2">
                <StatusDot state={status.state} label={status.label} />
              </div>
            </div>
            <div className={`rounded border p-3 ${attentionCount > 0 ? TONE_CLASS.warning : TONE_CLASS.healthy}`}>
              <div className="text-[10px] uppercase tracking-wider opacity-80">Attention</div>
              <div className="mt-1 text-lg font-semibold text-text-primary">{attentionCount}</div>
            </div>
            <button
              type="button"
              onClick={refresh}
              className="inline-flex items-center justify-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs font-medium text-text-secondary hover:border-border-active hover:text-text-primary"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Refresh
            </button>
          </div>
        </div>
        <div className="mt-4 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs leading-5 text-text-secondary">
          {status.detail}
        </div>
      </section>

      {hasErrors && (
        <section className="rounded-lg border border-state-error/40 bg-state-error/10 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-state-error" aria-hidden="true" />
            <div className="space-y-1 text-sm text-state-error">
              {healthError && <div>{getErrorMessage(healthError, 'Unable to load gateway health')}</div>}
              {readyError && <div>{getErrorMessage(readyError, 'Unable to load readiness state')}</div>}
            </div>
          </div>
        </section>
      )}

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryTile
          label="Uptime"
          value={health ? formatUptime(health.uptime_seconds) : healthLoading ? '...' : '--'}
          detail="Gateway process runtime."
          icon={<Timer className="h-4 w-4" />}
          tone="accent"
        />
        <SummaryTile
          label="Error Rate"
          value={health ? percent(health.error_rate) : healthLoading ? '...' : '--'}
          detail={`${health?.error_count ?? 0} errors across ${health?.total_requests ?? 0} requests.`}
          icon={<Activity className="h-4 w-4" />}
          tone={(health?.error_rate ?? 0) > 0 ? 'warning' : 'healthy'}
        />
        <SummaryTile
          label="Components"
          value={componentEntries.length || (healthLoading ? '...' : 0)}
          detail={`${componentCounts.healthy} healthy, ${componentCounts.degraded + componentCounts.error} attention.`}
          icon={<Cpu className="h-4 w-4" />}
          tone={componentCounts.degraded + componentCounts.error > 0 ? 'warning' : componentEntries.length ? 'healthy' : 'muted'}
        />
        <SummaryTile
          label="Last Tick"
          value={ready?.last_health_tick_at ? formatRelativeTime(ready.last_health_tick_at) : readyLoading ? '...' : 'Never'}
          detail="Latest health monitor update."
          icon={<Clock3 className="h-4 w-4" />}
          tone={ready?.last_health_tick_at ? 'healthy' : 'muted'}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-text-primary">Operator Focus</h2>
              <p className="mt-1 text-xs leading-5 text-text-secondary">Start here when the health monitor is not clean.</p>
            </div>
            {attentionCount === 0 ? (
              <span className="inline-flex items-center gap-2 rounded border border-state-healthy/30 bg-state-healthy/10 px-3 py-1.5 text-xs font-medium text-state-healthy">
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                Clear
              </span>
            ) : (
              <span className="inline-flex items-center gap-2 rounded border border-state-degraded/30 bg-state-degraded/10 px-3 py-1.5 text-xs font-medium text-state-degraded">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                Review
              </span>
            )}
          </div>

          {!ready ? (
            <LoadingRows count={4} />
          ) : degradedReasons.length === 0 && !ready.local_llm_last_error && componentCounts.degraded + componentCounts.error === 0 ? (
            <div className="rounded-lg border border-state-healthy/30 bg-state-healthy/10 p-4">
              <div className="flex items-start gap-3">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-state-healthy" aria-hidden="true" />
                <div>
                  <div className="text-sm font-medium text-text-primary">No degraded reasons detected</div>
                  <div className="mt-1 text-xs leading-5 text-text-muted">Readiness and loaded component state are currently clean.</div>
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              {ready.local_llm_last_error && (
                <div className="rounded border border-state-error/30 bg-state-error/10 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wider text-state-error">Local LLM Error</div>
                  <div className="mt-1 text-sm leading-5 text-text-primary">{ready.local_llm_last_error}</div>
                </div>
              )}
              {degradedReasons.map((reason, index) => (
                <div key={`${reason}-${index}`} className="rounded border border-state-degraded/30 bg-state-degraded/10 p-3">
                  <div className="flex items-start gap-3">
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-state-degraded" />
                    <span className="text-sm leading-5 text-text-primary">{reason}</span>
                  </div>
                </div>
              ))}
              {componentCounts.degraded + componentCounts.error > 0 && (
                <div className="rounded border border-border-default bg-surface-card-elevated p-3 text-sm text-text-secondary">
                  {componentCounts.degraded + componentCounts.error} component checks need review below.
                </div>
              )}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-4">
            <h2 className="text-sm font-semibold text-text-primary">Readiness Checks</h2>
            <p className="mt-1 text-xs leading-5 text-text-secondary">The operational checks that decide whether this instance should accept work.</p>
          </div>
          {!ready ? (
            <LoadingRows count={5} />
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {readinessItems.map((item) => (
                <ReadinessCard key={item.label} item={item} />
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Component Status</h2>
            <p className="mt-1 text-xs leading-5 text-text-secondary">Gateway component checks reported by `/health`.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="rounded border border-state-healthy/30 bg-state-healthy/10 px-2 py-1 text-xs text-state-healthy">{componentCounts.healthy} healthy</span>
            <span className="rounded border border-state-degraded/30 bg-state-degraded/10 px-2 py-1 text-xs text-state-degraded">{componentCounts.degraded} degraded</span>
            <span className="rounded border border-state-error/30 bg-state-error/10 px-2 py-1 text-xs text-state-error">{componentCounts.error} error</span>
            <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1 text-xs text-text-muted">{componentCounts.inactive} inactive</span>
          </div>
        </div>

        {componentEntries.length === 0 ? (
          <LoadingRows count={4} />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {componentEntries.map(([name, value]) => (
              <div key={name} className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-text-primary" title={readable(name)}>{readable(name)}</div>
                    <div className="mt-1 font-mono text-[11px] text-text-muted">{value}</div>
                  </div>
                  <StatusDot state={componentState(value)} label={componentLabel(value)} className="shrink-0" />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-4 flex items-center gap-2">
            <Bot className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-text-primary">Local Model Runtime</h2>
          </div>
          <div className="space-y-2">
            <FieldRow label="Ready" value={ready?.local_llm_ready ? 'Ready' : 'Not Ready'} state={ready?.local_llm_ready ? 'healthy' : ready?.local_llm_loaded ? 'degraded' : 'inactive'} />
            <FieldRow label="Loaded" value={ready?.local_llm_loaded ? 'Loaded' : 'Not Loaded'} state={ready?.local_llm_loaded ? 'healthy' : 'inactive'} />
            <FieldRow label="Status" value={ready?.local_llm_status ? readable(ready.local_llm_status) : '--'} />
            <FieldRow label="Last Verified" value={formatTimestamp(ready?.local_llm_last_verified_at)} />
            <FieldRow label="Last Checked" value={formatTimestamp(ready?.local_llm_last_checked_at)} />
            <FieldRow label="Failure Count" value={ready?.local_llm_consecutive_failures ?? '--'} />
            <FieldRow label="Last Smoke" value={ready?.local_llm_last_smoke_elapsed_ms != null ? `${ready.local_llm_last_smoke_elapsed_ms} ms` : '--'} />
          </div>
        </div>

        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-4 flex items-center gap-2">
            <DatabaseZap className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-text-primary">System Info</h2>
          </div>
          <div className="space-y-2">
            <FieldRow label="Version" value={health?.version ?? '--'} />
            <FieldRow label="Gateway Status" value={health?.status ? readable(health.status) : '--'} state={componentState(health?.status)} />
            <FieldRow label="Crusader Mode" value={health?.crusader_mode ? 'Active' : 'Inactive'} state={health?.crusader_mode ? 'degraded' : 'healthy'} />
            <FieldRow label="Scheduler" value={ready?.scheduler_running ? 'Running' : 'Stopped'} state={ready?.scheduler_running ? 'healthy' : 'inactive'} />
            <FieldRow label="Last Scheduler Tick" value={formatTimestamp(ready?.last_scheduler_tick_at)} />
            <FieldRow label="Snapshot Timestamp" value={formatTimestamp(ready?.timestamp)} />
          </div>
        </div>
      </section>

      {localModelRoles.length > 0 && (
        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-4 flex items-center gap-2">
            <Radio className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-text-primary">Model Role Health</h2>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {localModelRoles.map(([role, data]) => (
              <div key={role} className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-text-primary">{readable(role)}</div>
                    <div className="mt-1 truncate font-mono text-xs text-text-muted" title={data.model || ''}>{data.model || 'No model configured'}</div>
                  </div>
                  <StatusDot state={roleState(data)} label={data.status ? readable(data.status) : roleState(data)} className="shrink-0" />
                </div>
                {data.last_error && (
                  <div className="mt-3 rounded border border-state-error/30 bg-state-error/10 p-2 text-xs leading-5 text-state-error">
                    {data.last_error}
                  </div>
                )}
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded border border-border-default bg-surface-input p-2">
                    <div className="text-[10px] uppercase tracking-wider text-text-muted">Priority</div>
                    <div className="mt-1 text-text-primary">{data.priority ?? '--'}</div>
                  </div>
                  <div className="rounded border border-border-default bg-surface-input p-2">
                    <div className="text-[10px] uppercase tracking-wider text-text-muted">Failures</div>
                    <div className="mt-1 text-text-primary">{data.consecutive_failures ?? 0}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {(healthLoading || readyLoading) && !health && !ready && (
        <div className="fixed bottom-5 right-5 hidden items-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs text-text-secondary shadow-lg lg:flex">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          Loading health data
        </div>
      )}

      {!ready?.ready && ready && (
        <section className="rounded-lg border border-state-degraded/30 bg-state-degraded/10 p-4">
          <div className="flex items-start gap-3">
            <CircleSlash className="mt-0.5 h-4 w-4 shrink-0 text-state-degraded" aria-hidden="true" />
            <div>
              <div className="text-sm font-semibold text-text-primary">Instance is not fully ready</div>
              <div className="mt-1 text-xs leading-5 text-text-secondary">
                Treat command execution and automation as degraded until the readiness gate returns Ready.
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
