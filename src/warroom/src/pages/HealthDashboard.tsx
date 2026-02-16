import { usePolling } from '@/hooks'
import { fetchHealth, fetchHealthReady } from '@/api'
import { MetricCard, StatusDot, EmptyState } from '@/components'
import type { SystemState } from '@/components'
import type { HealthCheckResponse, HealthReadyResponse } from '@/types/api'

// ── Helpers ─────────────────────────────────────────────────────

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return 'Never'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function componentState(status: string): SystemState {
  if (status === 'ok') return 'healthy'
  if (status === 'degraded') return 'degraded'
  return 'inactive'
}

function componentLabel(status: string): string {
  if (status === 'ok') return 'OK'
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function overallStatus(
  health: HealthCheckResponse | null,
  ready: HealthReadyResponse | null,
): { label: string; color: string } {
  if (!health || !ready) return { label: '--', color: '' }
  if (ready.degraded_reasons.length > 0)
    return { label: 'DEGRADED', color: 'text-state-degraded' }
  const allOk = Object.values(health.components).every((v) => v === 'ok')
  if (allOk && ready.ready) return { label: 'HEALTHY', color: 'text-state-healthy' }
  return { label: 'DEGRADED', color: 'text-state-degraded' }
}

// ── Component ───────────────────────────────────────────────────

export function HealthDashboard() {
  const { data: health } = usePolling<HealthCheckResponse>({
    fetcher: fetchHealth,
    interval: 5000,
  })
  const { data: ready } = usePolling<HealthReadyResponse>({
    fetcher: fetchHealthReady,
    interval: 5000,
  })

  const status = overallStatus(health, ready)
  const componentEntries = Object.entries(health?.components ?? {})

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Health Dashboard</h2>

      {/* ── Top Metrics ─────────────────────────────────────── */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <MetricCard label="Overall Status" value={status.label} color={status.color} />
        <MetricCard
          label="Uptime"
          value={health ? formatUptime(health.uptime_seconds) : '--'}
        />
        <MetricCard
          label="Last Health Tick"
          value={ready?.last_health_tick_at ? formatTimestamp(ready.last_health_tick_at) : '--'}
        />
        <MetricCard label="Components" value={componentEntries.length} />
      </div>

      {/* ── Component Status ────────────────────────────────── */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Component Status
        </h3>
        {componentEntries.length === 0 ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <div className="space-y-2">
            {componentEntries.map(([name, status]) => (
              <div
                key={name}
                className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md"
              >
                <span className="text-sm text-text-primary capitalize">{name}</span>
                <StatusDot state={componentState(status)} label={componentLabel(status)} />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Readiness ───────────────────────────────────────── */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Readiness
        </h3>
        {!ready ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Ready State</span>
              <StatusDot
                state={ready.ready ? 'healthy' : 'degraded'}
                label={ready.ready ? 'Ready' : 'Not Ready'}
              />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Onboarding</span>
              <span className="text-xs font-mono text-text-secondary">
                {ready.onboarding_state}
              </span>
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Local LLM</span>
              <StatusDot
                state={ready.local_llm_ready ? 'healthy' : 'inactive'}
                label={ready.local_llm_ready ? 'Ready' : 'Not Ready'}
              />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Scheduler</span>
              <StatusDot
                state={ready.scheduler_running ? 'healthy' : 'inactive'}
                label={ready.scheduler_running ? 'Running' : 'Stopped'}
              />
            </div>
          </div>
        )}
      </section>

      {/* ── Degraded Reasons ────────────────────────────────── */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Degraded Reasons
        </h3>
        {!ready ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : ready.degraded_reasons.length === 0 ? (
          <EmptyState
            title="All Clear"
            description="No degraded reasons detected. All systems operating normally."
          />
        ) : (
          <div className="space-y-2">
            {ready.degraded_reasons.map((reason, i) => (
              <div
                key={i}
                className="flex items-start gap-3 p-3 bg-state-degraded/10 border border-state-degraded/20 rounded-md"
              >
                <span className="w-2 h-2 rounded-full bg-state-degraded mt-1 flex-shrink-0" />
                <span className="text-sm text-text-primary">{reason}</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── System Info ─────────────────────────────────────── */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          System Info
        </h3>
        <div className="space-y-2">
          <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
            <span className="text-sm text-text-primary">Version</span>
            <span className="text-xs font-mono text-text-secondary">
              {health?.version ?? '--'}
            </span>
          </div>
          <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
            <span className="text-sm text-text-primary">Crusader Mode</span>
            <StatusDot
              state={health?.crusader_mode ? 'degraded' : 'healthy'}
              label={health?.crusader_mode ? 'Active' : 'Inactive'}
              pulse={health?.crusader_mode}
            />
          </div>
          <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
            <span className="text-sm text-text-primary">Last Scheduler Tick</span>
            <span className="text-xs font-mono text-text-secondary">
              {ready ? formatTimestamp(ready.last_scheduler_tick_at) : '--'}
            </span>
          </div>
          <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
            <span className="text-sm text-text-primary">Snapshot Timestamp</span>
            <span className="text-xs font-mono text-text-secondary">
              {ready ? formatTimestamp(ready.timestamp) : '--'}
            </span>
          </div>
        </div>
      </section>
    </div>
  )
}
