import { usePolling } from '@/hooks'
import { fetchHealth, fetchToolsHealth, fetchToolsConfig } from '@/api'
import { StatusDot, MetricCard } from '@/components'

export function ToolFabric() {
  const { data: health } = usePolling({ fetcher: fetchHealth, interval: 10000 })
  const { data: toolsHealth } = usePolling({ fetcher: fetchToolsHealth, interval: 10000 })
  const { data: toolsConfig } = usePolling({ fetcher: fetchToolsConfig, interval: 30000 })

  const systemComponents = health?.components ?? {}
  const providers = toolsHealth?.providers ?? {}
  const summary = toolsHealth?.summary ?? { total_providers: 0, healthy: 0, degraded: 0, offline: 0 }
  const fabricEnabled = toolsHealth?.enabled ?? false

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Tool Fabric</h2>

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <MetricCard label="Fabric" value={fabricEnabled ? 'Enabled' : 'Disabled'} />
        <MetricCard label="Providers" value={summary.total_providers} />
        <MetricCard label="Healthy" value={summary.healthy} />
        <MetricCard label="Degraded" value={summary.degraded} />
        <MetricCard label="Offline" value={summary.offline} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* System Components */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            System Components
          </h3>
          {Object.keys(systemComponents).length === 0 ? (
            <p className="text-sm text-text-muted">Loading...</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(systemComponents).map(([name, status]) => (
                <div key={name} className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-sm text-text-primary font-mono">{name}</span>
                  <StatusDot
                    state={status === 'ok' ? 'healthy' : status === 'disabled' ? 'inactive' : status === 'degraded' ? 'degraded' : 'error'}
                    label={String(status)}
                  />
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Tool Providers */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Tool Providers
          </h3>
          {Object.keys(providers).length === 0 ? (
            <p className="text-sm text-text-muted">
              {fabricEnabled ? 'No providers registered' : 'Tool Fabric not active — no providers registered'}
            </p>
          ) : (
            <div className="space-y-3">
              {Object.entries(providers).map(([pid, prov]) => (
                <div key={pid} className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
                  <div>
                    <span className="text-sm text-text-primary font-mono">{pid}</span>
                    {prov.error && <p className="text-[10px] text-state-error mt-0.5">{prov.error}</p>}
                  </div>
                  <StatusDot
                    state={prov.state === 'healthy' ? 'healthy' : prov.state === 'degraded' ? 'degraded' : 'error'}
                    label={prov.state}
                  />
                </div>
              ))}
            </div>
          )}
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

        {/* Fabric Configuration */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Fabric Configuration
          </h3>
          <div className="space-y-3">
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-secondary">Tool Fabric</span>
              <StatusDot
                state={toolsConfig?.enabled ? 'healthy' : 'inactive'}
                label={toolsConfig?.enabled ? 'Enabled' : 'Disabled'}
              />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-secondary">Safe Mode</span>
              <StatusDot
                state={toolsConfig?.safe_mode ? 'degraded' : 'healthy'}
                label={toolsConfig?.safe_mode ? 'Active' : 'Off'}
              />
            </div>
            <div className="flex items-center justify-between p-3 bg-surface-card-elevated rounded-md">
              <span className="text-sm text-text-secondary">Receipts</span>
              <StatusDot
                state={toolsConfig?.receipts ? 'healthy' : 'inactive'}
                label={toolsConfig?.receipts ? 'Enabled' : 'Disabled'}
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
