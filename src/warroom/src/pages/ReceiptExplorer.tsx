import { useState, useCallback } from 'react'
import { usePolling } from '@/hooks'
import { fetchReceipts, fetchReceiptStats } from '@/api'
import { TierBadge, MetricCard } from '@/components'
import type { ReceiptItem } from '@/api/receipts'

const TIER_LABELS = ['T0', 'T1', 'T2', 'T3']

export function ReceiptExplorer() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const fetcher = useCallback(
    () =>
      fetchReceipts({
        limit: 100,
        q: search || undefined,
        status: statusFilter || undefined,
      }),
    [search, statusFilter],
  )

  const { data: receiptsData } = usePolling({ fetcher, interval: 15000 })
  const { data: statsData } = usePolling({ fetcher: fetchReceiptStats, interval: 30000 })

  const receipts = (receiptsData?.receipts ?? []).filter(
    (r) => !tierFilter || String(r.tier) === tierFilter,
  )
  const stats = statsData?.stats

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Receipt Explorer</h2>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <MetricCard label="Total Receipts" value={stats.total_receipts} />
          <MetricCard label="Avg Duration" value={`${Math.round(stats.duration_ms?.average ?? 0)}ms`} />
          <MetricCard label="Total Tokens" value={stats.tokens?.total?.toLocaleString() ?? '--'} />
          <MetricCard
            label="Success Rate"
            value={
              stats.by_status
                ? `${Math.round(((stats.by_status.completed ?? 0) / Math.max(stats.total_receipts, 1)) * 100)}%`
                : '--'
            }
          />
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <input
          type="text"
          placeholder="Search receipts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active w-64"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
        >
          <option value="">All Statuses</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="pending">Pending</option>
        </select>
        <select
          value={tierFilter}
          onChange={(e) => setTierFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
        >
          <option value="">All Tiers</option>
          {TIER_LABELS.map((t, i) => (
            <option key={i} value={String(i)}>{t}</option>
          ))}
        </select>
      </div>

      {/* Receipt Table */}
      <div className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border-default text-text-muted text-xs uppercase tracking-wider">
              <th className="px-4 py-3 text-left">Time</th>
              <th className="px-4 py-3 text-left">Tier</th>
              <th className="px-4 py-3 text-left">Action</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-right">Duration</th>
              <th className="px-4 py-3 text-right">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {receipts.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-text-muted">
                  No receipts found
                </td>
              </tr>
            ) : (
              receipts.map((r: ReceiptItem) => (
                <ReceiptRow
                  key={r.id}
                  receipt={r}
                  expanded={expandedId === r.id}
                  onToggle={() => setExpandedId(expandedId === r.id ? null : r.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ReceiptRow({
  receipt: r,
  expanded,
  onToggle,
}: {
  receipt: ReceiptItem
  expanded: boolean
  onToggle: () => void
}) {
  const statusColor =
    r.status === 'completed' ? 'text-state-healthy' : r.status === 'failed' ? 'text-state-error' : 'text-state-degraded'

  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-border-default hover:bg-surface-card-elevated cursor-pointer transition-colors"
      >
        <td className="px-4 py-3 font-mono text-xs text-text-muted">
          {new Date(r.timestamp).toLocaleString()}
        </td>
        <td className="px-4 py-3">
          <TierBadge tier={r.tier} compact />
        </td>
        <td className="px-4 py-3 text-text-primary">{r.action_name}</td>
        <td className={`px-4 py-3 font-mono text-xs ${statusColor}`}>
          {r.status.toUpperCase()}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text-secondary">
          {r.duration_ms != null ? `${r.duration_ms}ms` : '--'}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text-secondary">
          {r.token_count ?? '--'}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-border-default">
          <td colSpan={6} className="px-4 py-3 bg-surface-card-elevated">
            <div className="grid grid-cols-2 gap-4 text-xs">
              <div>
                <span className="text-text-muted block mb-1">Inputs</span>
                <pre className="font-mono text-text-secondary bg-surface-input rounded p-2 overflow-auto max-h-40">
                  {JSON.stringify(r.inputs, null, 2)}
                </pre>
              </div>
              <div>
                <span className="text-text-muted block mb-1">Outputs</span>
                <pre className="font-mono text-text-secondary bg-surface-input rounded p-2 overflow-auto max-h-40">
                  {JSON.stringify(r.outputs, null, 2)}
                </pre>
              </div>
            </div>
            {r.error_message && (
              <div className="mt-2 text-xs text-state-error bg-state-error/10 rounded p-2">
                {r.error_message}
              </div>
            )}
            <div className="mt-2 text-[10px] text-text-muted font-mono">
              ID: {r.id} {r.quest_id && `| Quest: ${r.quest_id}`} {r.parent_id && `| Parent: ${r.parent_id}`}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
