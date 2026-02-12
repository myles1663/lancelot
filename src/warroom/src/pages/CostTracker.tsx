import { usePolling } from '@/hooks'
import { fetchUsageSummary, fetchUsageLanes, fetchUsageModels, fetchUsageMonthly } from '@/api'
import { MetricCard } from '@/components'

export function CostTracker() {
  const { data: summary } = usePolling({ fetcher: fetchUsageSummary, interval: 15000 })
  const { data: lanes } = usePolling({ fetcher: fetchUsageLanes, interval: 30000 })
  const { data: models } = usePolling({ fetcher: fetchUsageModels, interval: 30000 })
  const { data: monthly } = usePolling({ fetcher: () => fetchUsageMonthly(), interval: 60000 })

  const usage = summary?.usage ?? {}
  const laneData = lanes?.lanes ?? {}
  const modelData = models?.models ?? {}
  const monthlyData = monthly?.monthly ?? {}

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Cost Tracker</h2>

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Total Calls" value={String(usage.total_calls ?? '--')} />
        <MetricCard label="Total Tokens" value={usage.total_tokens ? Number(usage.total_tokens).toLocaleString() : '--'} />
        <MetricCard label="Est. Cost" value={usage.estimated_cost ? `$${Number(usage.estimated_cost).toFixed(2)}` : '--'} />
        <MetricCard label="Avg Latency" value={usage.avg_latency_ms ? `${Math.round(Number(usage.avg_latency_ms))}ms` : '--'} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Per-Lane Breakdown */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Usage by Lane
          </h3>
          {Object.keys(laneData).length === 0 ? (
            <p className="text-sm text-text-muted">No lane data available</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(laneData).map(([lane, data]) => {
                const d = data as Record<string, unknown>
                return (
                  <div key={lane} className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                    <span className="text-sm text-text-primary font-mono">{lane}</span>
                    <div className="text-right text-xs text-text-secondary font-mono">
                      <span>{String(d.calls ?? 0)} calls</span>
                      <span className="ml-3">{d.tokens ? Number(d.tokens).toLocaleString() : '0'} tokens</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* Per-Model Breakdown */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Usage by Model
          </h3>
          {Object.keys(modelData).length === 0 ? (
            <p className="text-sm text-text-muted">No model data available</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(modelData).map(([model, data]) => {
                const d = data as Record<string, unknown>
                return (
                  <div key={model} className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                    <span className="text-sm text-text-primary font-mono truncate max-w-[200px]">{model}</span>
                    <div className="text-right text-xs text-text-secondary font-mono">
                      <span>{String(d.calls ?? 0)} calls</span>
                      {d.estimated_cost != null && <span className="ml-3">${Number(d.estimated_cost).toFixed(2)}</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* Monthly Data */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4 lg:col-span-2">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Monthly Summary
          </h3>
          <div className="flex gap-3 mb-3 flex-wrap">
            {monthly?.available_months?.map((m) => (
              <span key={m} className="text-xs font-mono px-2 py-1 bg-surface-input rounded text-text-secondary">
                {m}
              </span>
            ))}
          </div>
          {Object.keys(monthlyData).length === 0 ? (
            <p className="text-sm text-text-muted">No monthly data yet</p>
          ) : (
            <pre className="text-xs font-mono text-text-secondary bg-surface-input rounded p-3 overflow-auto max-h-64">
              {JSON.stringify(monthlyData, null, 2)}
            </pre>
          )}
        </section>
      </div>
    </div>
  )
}
