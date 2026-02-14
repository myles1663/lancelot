import { useState } from 'react'
import { usePolling } from '@/hooks'
import {
  fetchUsageSummary, fetchUsageLanes, fetchUsageModels, fetchUsageMonthly,
  fetchProviderStack, refreshModelDiscovery,
} from '@/api'
import type { ProviderStackResponse, DiscoveredModel } from '@/api'
import { MetricCard } from '@/components'

/** Format context window size for display */
function formatCtx(tokens: number): string {
  if (!tokens) return '--'
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`
  return String(tokens)
}

/** Capitalize provider name for display */
function providerLabel(stack: ProviderStackResponse | null | undefined): string {
  if (!stack || stack.status === 'unavailable') return 'Not Connected'
  return stack.provider_display_name || stack.provider?.charAt(0).toUpperCase() + stack.provider?.slice(1) || 'Unknown'
}

/** Lane display order and labels */
const LANE_ORDER = ['fast', 'deep', 'cache'] as const
const LANE_LABELS: Record<string, string> = {
  fast: 'Fast',
  deep: 'Deep',
  cache: 'Cache',
}

export function CostTracker() {
  const { data: summary } = usePolling({ fetcher: fetchUsageSummary, interval: 15000 })
  const { data: lanes } = usePolling({ fetcher: fetchUsageLanes, interval: 30000 })
  const { data: models } = usePolling({ fetcher: fetchUsageModels, interval: 30000 })
  const { data: monthly } = usePolling({ fetcher: () => fetchUsageMonthly(), interval: 60000 })
  const { data: stack, refetch: refetchStack } = usePolling({ fetcher: fetchProviderStack, interval: 60000 })

  const [refreshing, setRefreshing] = useState(false)

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await refreshModelDiscovery()
      await refetchStack()
    } catch {
      // silently fail — UI will show stale data
    } finally {
      setRefreshing(false)
    }
  }

  const usage = summary?.usage ?? {} as Record<string, unknown>
  const laneData = lanes?.lanes ?? {}
  const modelData = models?.models ?? {}
  const monthlyData = monthly?.monthly ?? {} as Record<string, unknown>

  // Backend field names: total_requests, total_tokens_est, total_cost_est, avg_elapsed_ms
  const totalRequests = (usage as Record<string, unknown>).total_requests
  const totalTokens = (usage as Record<string, unknown>).total_tokens_est
  const estCost = (usage as Record<string, unknown>).total_cost_est
  const avgLatency = (usage as Record<string, unknown>).avg_elapsed_ms

  // Monthly data fields
  const md = monthlyData as Record<string, unknown>
  const byModel = (md.by_model ?? {}) as Record<string, Record<string, unknown>>
  const byDay = (md.by_day ?? {}) as Record<string, Record<string, unknown>>

  // Use monthly by_model / summary by_model as fallback for per-model breakdown
  const summaryByModel = (usage as Record<string, unknown>).by_model as Record<string, Record<string, unknown>> | undefined
  const effectiveModelData = Object.keys(modelData).length > 0
    ? modelData
    : (Object.keys(byModel).length > 0 ? byModel : (summaryByModel ?? {}))

  // Use summary by_lane as fallback for per-lane breakdown
  const summaryByLane = (usage as Record<string, unknown>).by_lane as Record<string, Record<string, unknown>> | undefined
  const effectiveLaneData = Object.keys(laneData).length > 0 ? laneData : (summaryByLane ?? {})

  // Model stack data
  const stackLanes = stack?.lanes ?? {}
  const discoveredModels = stack?.discovered_models ?? []
  const isConnected = stack?.status === 'connected'

  // Format last refresh time
  const lastRefresh = stack?.last_refresh
    ? new Date(stack.last_refresh).toLocaleTimeString()
    : null

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Cost Tracker</h2>

      {/* ═══ Model Stack Section ═══ */}
      <section className="bg-surface-card border border-border-default rounded-lg p-4 mb-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
            Model Stack
          </h3>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="text-xs px-3 py-1 rounded border border-border-default text-text-secondary
                       hover:bg-surface-card-elevated hover:text-text-primary transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>

        {/* Provider status row */}
        <div className="flex items-center gap-3 mb-4">
          <span className="text-sm text-text-primary font-medium">
            Provider: {providerLabel(stack)}
          </span>
          <span className={`inline-flex items-center gap-1.5 text-xs ${isConnected ? 'text-green-400' : 'text-text-muted'}`}>
            <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-text-muted'}`} />
            {isConnected ? 'Connected' : 'Unavailable'}
          </span>
          {lastRefresh && (
            <span className="text-xs text-text-muted ml-auto">
              Last discovery: {lastRefresh}
            </span>
          )}
        </div>

        {/* Lane assignments table */}
        {Object.keys(stackLanes).length > 0 && (
          <div className="overflow-x-auto mb-4">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-text-muted text-left">
                  <th className="py-1 pr-4">Lane</th>
                  <th className="py-1 pr-4">Model</th>
                  <th className="py-1 pr-4 text-right">Context</th>
                  <th className="py-1 pr-4 text-right">$/1K out</th>
                  <th className="py-1 text-center">Tools</th>
                </tr>
              </thead>
              <tbody className="text-text-primary">
                {LANE_ORDER.filter(lane => stackLanes[lane]).map(lane => {
                  const l = stackLanes[lane]!
                  return (
                    <tr key={lane} className="border-t border-border-default/50">
                      <td className="py-2 pr-4 font-medium">{LANE_LABELS[lane] || lane}</td>
                      <td className="py-2 pr-4">{l.display_name || l.model}</td>
                      <td className="py-2 pr-4 text-right">{formatCtx(l.context_window)}</td>
                      <td className="py-2 pr-4 text-right">
                        {l.cost_output_per_1k ? `$${l.cost_output_per_1k.toFixed(4)}` : '--'}
                      </td>
                      <td className="py-2 text-center">
                        {l.supports_tools
                          ? <span className="text-green-400">Yes</span>
                          : <span className="text-text-muted">No</span>
                        }
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Discovered models (collapsible) */}
        {discoveredModels.length > 0 && (
          <DiscoveredModelsList models={discoveredModels} />
        )}
      </section>

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Total Calls" value={totalRequests != null ? String(totalRequests) : '--'} />
        <MetricCard label="Total Tokens" value={totalTokens ? Number(totalTokens).toLocaleString() : '--'} />
        <MetricCard label="Est. Cost" value={estCost ? `$${Number(estCost).toFixed(4)}` : '--'} />
        <MetricCard label="Avg Latency" value={avgLatency ? `${Math.round(Number(avgLatency))}ms` : '--'} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Per-Lane Breakdown */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Usage by Lane
          </h3>
          {Object.keys(effectiveLaneData).length === 0 ? (
            <p className="text-sm text-text-muted">No lane data available</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(effectiveLaneData).map(([lane, data]) => {
                const d = data as Record<string, unknown>
                return (
                  <div key={lane} className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                    <span className="text-sm text-text-primary font-mono">{lane}</span>
                    <div className="text-right text-xs text-text-secondary font-mono">
                      <span>{String(d.calls ?? d.requests ?? 0)} calls</span>
                      <span className="ml-3">{Number(d.tokens ?? d.tokens_est ?? 0).toLocaleString()} tokens</span>
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
          {Object.keys(effectiveModelData).length === 0 ? (
            <p className="text-sm text-text-muted">No model data available</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(effectiveModelData).map(([model, data]) => {
                const d = data as Record<string, unknown>
                return (
                  <div key={model} className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                    <span className="text-sm text-text-primary font-mono truncate max-w-[200px]">{model}</span>
                    <div className="text-right text-xs text-text-secondary font-mono">
                      <span>{String(d.calls ?? d.requests ?? 0)} calls</span>
                      <span className="ml-3">{Number(d.tokens ?? d.tokens_est ?? 0).toLocaleString()} tokens</span>
                      {(d.estimated_cost ?? d.cost) != null && (
                        <span className="ml-3">${Number(d.estimated_cost ?? d.cost).toFixed(4)}</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* Monthly Summary — Rendered as proper tables */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4 lg:col-span-2">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Monthly Summary
          </h3>
          <div className="flex gap-3 mb-4 flex-wrap items-center">
            {monthly?.available_months?.map((m: string) => (
              <span key={m} className="text-xs font-mono px-2 py-1 bg-accent-primary/15 text-accent-primary rounded">
                {m}
              </span>
            ))}
          </div>

          {!md.month ? (
            <p className="text-sm text-text-muted">No monthly data yet</p>
          ) : (
            <div className="space-y-4">
              {/* Monthly totals */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Month</span>
                  <p className="text-sm font-mono text-text-primary mt-1">{String(md.month)}</p>
                </div>
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Requests</span>
                  <p className="text-sm font-mono text-text-primary mt-1">{String(md.total_requests ?? 0)}</p>
                </div>
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Tokens</span>
                  <p className="text-sm font-mono text-text-primary mt-1">{Number(md.total_tokens ?? 0).toLocaleString()}</p>
                </div>
                <div className="p-3 bg-surface-card-elevated rounded-md">
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">Cost</span>
                  <p className="text-sm font-mono text-text-primary mt-1">${Number(md.total_cost ?? 0).toFixed(4)}</p>
                </div>
              </div>

              {/* By Model table */}
              {Object.keys(byModel).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-secondary mb-2">By Model</h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs font-mono">
                      <thead>
                        <tr className="text-text-muted text-left">
                          <th className="py-1 pr-4">Model</th>
                          <th className="py-1 pr-4 text-right">Requests</th>
                          <th className="py-1 pr-4 text-right">Tokens</th>
                          <th className="py-1 text-right">Cost</th>
                        </tr>
                      </thead>
                      <tbody className="text-text-primary">
                        {Object.entries(byModel).map(([model, d]) => (
                          <tr key={model} className="border-t border-border-default/50">
                            <td className="py-2 pr-4">{model}</td>
                            <td className="py-2 pr-4 text-right">{String(d.requests ?? 0)}</td>
                            <td className="py-2 pr-4 text-right">{Number(d.tokens ?? 0).toLocaleString()}</td>
                            <td className="py-2 text-right">${Number(d.cost ?? 0).toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* By Day table */}
              {Object.keys(byDay).length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-text-secondary mb-2">By Day</h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs font-mono">
                      <thead>
                        <tr className="text-text-muted text-left">
                          <th className="py-1 pr-4">Date</th>
                          <th className="py-1 pr-4 text-right">Requests</th>
                          <th className="py-1 pr-4 text-right">Tokens</th>
                          <th className="py-1 text-right">Cost</th>
                        </tr>
                      </thead>
                      <tbody className="text-text-primary">
                        {Object.entries(byDay).map(([day, d]) => (
                          <tr key={day} className="border-t border-border-default/50">
                            <td className="py-2 pr-4">{day}</td>
                            <td className="py-2 pr-4 text-right">{String(d.requests ?? 0)}</td>
                            <td className="py-2 pr-4 text-right">{Number(d.tokens ?? 0).toLocaleString()}</td>
                            <td className="py-2 text-right">${Number(d.cost ?? 0).toFixed(4)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

/** Collapsible list of discovered models */
function DiscoveredModelsList({ models }: { models: DiscoveredModel[] }) {
  const [expanded, setExpanded] = useState(false)
  const TIER_COLORS: Record<string, string> = {
    fast: 'text-blue-400',
    standard: 'text-text-secondary',
    deep: 'text-purple-400',
  }

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-text-secondary hover:text-text-primary transition-colors"
      >
        {expanded ? 'Hide' : 'Show'} Available Models ({models.length} discovered)
      </button>
      {expanded && (
        <div className="mt-2 space-y-1">
          {models.map(m => (
            <div key={m.id} className="flex items-center gap-3 px-2 py-1.5 text-xs font-mono rounded bg-surface-card-elevated">
              <span className="text-text-primary truncate max-w-[220px]">{m.display_name || m.id}</span>
              <span className={`${TIER_COLORS[m.capability_tier] || 'text-text-muted'}`}>
                {m.capability_tier}
              </span>
              {m.supports_tools && (
                <span className="text-green-400/70">tools</span>
              )}
              <span className="text-text-muted ml-auto">{formatCtx(m.context_window)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
