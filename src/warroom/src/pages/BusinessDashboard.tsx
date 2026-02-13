import { useState } from 'react'
import { usePolling } from '@/hooks'
import { fetchBalClients, pauseClient, resumeClient, activateClient } from '@/api'
import type { BalClient } from '@/api/business'
import { MetricCard, StatusDot, EmptyState } from '@/components'
import type { SystemState } from '@/components/StatusDot'

// ── Helpers ──────────────────────────────────────────

const STATUS_STATE: Record<string, SystemState> = {
  active: 'healthy',
  onboarding: 'degraded',
  paused: 'inactive',
  churned: 'error',
}

const STATUS_LABEL: Record<string, string> = {
  active: 'Active',
  onboarding: 'Onboarding',
  paused: 'Paused',
  churned: 'Churned',
}

const TIER_COLORS: Record<string, string> = {
  starter: 'bg-blue-500/15 text-blue-400',
  growth: 'bg-purple-500/15 text-purple-400',
  scale: 'bg-amber-500/15 text-amber-400',
}

function tierBadge(tier: string) {
  const cls = TIER_COLORS[tier] ?? 'bg-surface-card-elevated text-text-muted'
  return (
    <span className={`text-[10px] uppercase font-semibold tracking-wider px-2 py-0.5 rounded ${cls}`}>
      {tier}
    </span>
  )
}

function formatDate(iso: string | null) {
  if (!iso) return '--'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

// ── Component ────────────────────────────────────────

export function BusinessDashboard() {
  const { data, loading, error, refetch } = usePolling({ fetcher: fetchBalClients, interval: 15000 })
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('all')

  const clients = data?.clients ?? []
  const total = clients.length

  // Counts
  const active = clients.filter(c => c.status === 'active').length
  const onboarding = clients.filter(c => c.status === 'onboarding').length
  const paused = clients.filter(c => c.status === 'paused').length
  const churned = clients.filter(c => c.status === 'churned').length

  // Tier breakdown
  const tierCounts: Record<string, number> = {}
  clients.filter(c => c.status !== 'churned').forEach(c => {
    tierCounts[c.plan_tier] = (tierCounts[c.plan_tier] || 0) + 1
  })

  // Filtered list
  const filtered = statusFilter === 'all'
    ? clients
    : clients.filter(c => c.status === statusFilter)

  // Actions
  async function handleAction(clientId: string, action: 'pause' | 'resume' | 'activate') {
    setActionLoading(clientId)
    try {
      if (action === 'pause') await pauseClient(clientId)
      else if (action === 'resume') await resumeClient(clientId)
      else if (action === 'activate') await activateClient(clientId)
      refetch()
    } catch {
      // swallow — API error will show on next poll
    } finally {
      setActionLoading(null)
    }
  }

  // BAL disabled / error state
  if (!loading && error) {
    const is503 = error.message?.includes('503') || error.message?.includes('not enabled')
    if (is503) {
      return (
        <div>
          <h2 className="text-lg font-semibold text-text-primary mb-6">Business Dashboard</h2>
          <EmptyState
            title="BAL Not Enabled"
            description="Enable FEATURE_BAL in Kill Switches to activate the Business Automation Layer."
            icon="🔒"
          />
        </div>
      )
    }
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Business Dashboard</h2>

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Total Clients" value={total} />
        <MetricCard label="Active" value={active} />
        <MetricCard label="Onboarding" value={onboarding} />
        <MetricCard label="Paused" value={paused} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Client List — spans 2 columns */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4 lg:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
              Clients
            </h3>
            <div className="flex gap-1">
              {['all', 'active', 'onboarding', 'paused', 'churned'].map(s => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`text-[10px] uppercase tracking-wider px-2 py-1 rounded transition-colors ${
                    statusFilter === s
                      ? 'bg-accent-primary/20 text-accent-primary'
                      : 'text-text-muted hover:text-text-secondary'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {filtered.length === 0 ? (
            <p className="text-sm text-text-muted py-4 text-center">
              {total === 0 ? 'No clients yet. Create one via the API.' : 'No clients match this filter.'}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="text-text-muted text-left uppercase tracking-wider">
                    <th className="py-2 pr-4">Name</th>
                    <th className="py-2 pr-4">Email</th>
                    <th className="py-2 pr-4">Status</th>
                    <th className="py-2 pr-4">Tier</th>
                    <th className="py-2 pr-4 text-right">Delivered</th>
                    <th className="py-2 pr-4">Since</th>
                    <th className="py-2">Actions</th>
                  </tr>
                </thead>
                <tbody className="text-text-primary">
                  {filtered.map((c: BalClient) => (
                    <tr key={c.id} className="border-t border-border-default/50">
                      <td className="py-2.5 pr-4 text-text-primary">{c.name}</td>
                      <td className="py-2.5 pr-4 text-text-secondary truncate max-w-[160px]">{c.email}</td>
                      <td className="py-2.5 pr-4">
                        <StatusDot
                          state={STATUS_STATE[c.status] ?? 'inactive'}
                          label={STATUS_LABEL[c.status] ?? c.status}
                        />
                      </td>
                      <td className="py-2.5 pr-4">{tierBadge(c.plan_tier)}</td>
                      <td className="py-2.5 pr-4 text-right text-text-secondary">
                        {c.content_history.total_pieces_delivered}
                      </td>
                      <td className="py-2.5 pr-4 text-text-secondary">{formatDate(c.created_at)}</td>
                      <td className="py-2.5">
                        <ClientActions
                          client={c}
                          loading={actionLoading === c.id}
                          onAction={handleAction}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Right sidebar */}
        <div className="space-y-6">
          {/* Plan Tier Breakdown */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Plan Tiers
            </h3>
            {Object.keys(tierCounts).length === 0 ? (
              <p className="text-sm text-text-muted">No active clients</p>
            ) : (
              <div className="space-y-2">
                {Object.entries(tierCounts).map(([tier, count]) => (
                  <div key={tier} className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                    {tierBadge(tier)}
                    <span className="text-sm font-mono text-text-primary">{count}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Status Overview */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Status Overview
            </h3>
            <div className="space-y-2">
              <div className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                <StatusDot state="healthy" label="Active" />
                <span className="text-sm font-mono text-text-primary">{active}</span>
              </div>
              <div className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                <StatusDot state="degraded" label="Onboarding" />
                <span className="text-sm font-mono text-text-primary">{onboarding}</span>
              </div>
              <div className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                <StatusDot state="inactive" label="Paused" />
                <span className="text-sm font-mono text-text-primary">{paused}</span>
              </div>
              <div className="flex items-center justify-between p-2 bg-surface-card-elevated rounded">
                <StatusDot state="error" label="Churned" />
                <span className="text-sm font-mono text-text-primary">{churned}</span>
              </div>
            </div>
          </section>

          {/* Quick Stats */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Content Delivery
            </h3>
            <div className="space-y-3">
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Total Delivered</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {clients.reduce((sum, c) => sum + c.content_history.total_pieces_delivered, 0)}
                </p>
              </div>
              <div className="p-3 bg-surface-card-elevated rounded-md">
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Avg Satisfaction</span>
                <p className="text-sm font-mono text-text-primary mt-1">
                  {clients.length > 0
                    ? (clients.reduce((sum, c) => sum + c.content_history.average_satisfaction, 0) / clients.length * 100).toFixed(0) + '%'
                    : '--'}
                </p>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}

// ── Client Actions ───────────────────────────────────

function ClientActions({
  client,
  loading,
  onAction,
}: {
  client: BalClient
  loading: boolean
  onAction: (id: string, action: 'pause' | 'resume' | 'activate') => void
}) {
  if (loading) {
    return <span className="text-[10px] text-text-muted animate-pulse">...</span>
  }

  const buttons: { label: string; action: 'pause' | 'resume' | 'activate'; cls: string }[] = []

  if (client.status === 'onboarding') {
    buttons.push({ label: 'Activate', action: 'activate', cls: 'text-state-healthy' })
  }
  if (client.status === 'active') {
    buttons.push({ label: 'Pause', action: 'pause', cls: 'text-state-degraded' })
  }
  if (client.status === 'paused') {
    buttons.push({ label: 'Resume', action: 'resume', cls: 'text-state-healthy' })
  }

  if (buttons.length === 0) return <span className="text-[10px] text-text-muted">--</span>

  return (
    <div className="flex gap-2">
      {buttons.map(b => (
        <button
          key={b.action}
          onClick={() => onAction(client.id, b.action)}
          className={`text-[10px] uppercase tracking-wider font-semibold ${b.cls} hover:opacity-80 transition-opacity`}
        >
          {b.label}
        </button>
      ))}
    </div>
  )
}
