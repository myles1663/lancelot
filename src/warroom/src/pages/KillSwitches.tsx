import { usePolling } from '@/hooks'
import { fetchSystemStatus } from '@/api'
import { StatusDot } from '@/components'

export function KillSwitches() {
  const { data } = usePolling({ fetcher: fetchSystemStatus, interval: 10000 })

  // Feature flags are read from /system/status — we display what's available
  // Future WR-24 will add a dedicated /api/flags endpoint
  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Kill Switches</h2>

      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          System State
        </h3>
        {!data ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Onboarding</span>
              <StatusDot state={data.onboarding.is_ready ? 'healthy' : 'degraded'} label={data.onboarding.state} />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">System Ready</span>
              <StatusDot state={data.onboarding.is_ready ? 'healthy' : 'inactive'} label={data.onboarding.is_ready ? 'Ready' : 'Not Ready'} />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-primary">Cooldown</span>
              <StatusDot
                state={data.cooldown.active ? 'degraded' : 'healthy'}
                label={data.cooldown.active ? `Active (${Math.round(data.cooldown.remaining_seconds)}s)` : 'Inactive'}
              />
            </div>
          </div>
        )}
      </section>

      <section className="bg-surface-card border border-border-default rounded-lg p-4">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
          Feature Flags
        </h3>
        <p className="text-sm text-text-muted">
          Feature flag management will be available after WR-24 backend implementation.
          Current flags are configured via environment variables in .env.
        </p>
        <div className="mt-3 space-y-2">
          {['FEATURE_MEMORY_VNEXT', 'FEATURE_SOUL', 'FEATURE_SKILLS', 'FEATURE_SCHEDULER', 'FEATURE_HEALTH_MONITOR', 'FEATURE_LOCAL_AGENTIC'].map((flag) => (
            <div key={flag} className="flex items-center justify-between p-2 bg-surface-card-elevated rounded text-xs">
              <span className="font-mono text-text-primary">{flag}</span>
              <span className="text-text-muted">env-configured</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
