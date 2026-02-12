import { usePolling } from '@/hooks'
import { fetchHealth } from '@/api'
import { StatusDot, MetricCard } from '@/components'

export function ToolFabric() {
  const { data: health } = usePolling({ fetcher: fetchHealth, interval: 10000 })

  const components = health?.components ?? {}

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Tool Fabric</h2>

      {/* Component Health */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        {Object.entries(components).map(([name, status]) => (
          <MetricCard key={name} label={name} value={status} />
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Provider Health */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Provider Health
          </h3>
          <div className="space-y-3">
            {Object.entries(components).map(([name, status]) => (
              <div key={name} className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
                <span className="text-sm text-text-primary font-mono">{name}</span>
                <StatusDot
                  state={status === 'ok' ? 'healthy' : status === 'degraded' ? 'degraded' : 'error'}
                  label={status}
                />
              </div>
            ))}
          </div>
        </section>

        {/* System Info */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            System Info
          </h3>
          <div className="space-y-3">
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-secondary">Version</span>
              <span className="text-sm font-mono text-text-primary">{health?.version ?? '--'}</span>
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-secondary">Uptime</span>
              <span className="text-sm font-mono text-text-primary">
                {health?.uptime_seconds ? `${Math.round(health.uptime_seconds / 60)}m` : '--'}
              </span>
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-secondary">Crusader Mode</span>
              <StatusDot
                state={health?.crusader_mode ? 'degraded' : 'healthy'}
                label={health?.crusader_mode ? 'Active' : 'Off'}
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
