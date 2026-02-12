import { useCallback } from 'react'
import { usePolling } from '@/hooks'
import { fetchReceipts } from '@/api/receipts'
import { fetchReceiptStats } from '@/api/receipts'
import type { ReceiptItem } from '@/api/receipts'
import { StatusDot } from '@/components'
import { ChatInterface } from './command/ChatInterface'
import { ControlsPanel } from './command/ControlsPanel'

// ── Helpers ─────────────────────────────────────────────────────

function receiptStatusState(status: string): 'healthy' | 'error' | 'degraded' | 'inactive' {
  if (status === 'success') return 'healthy'
  if (status === 'failure') return 'error'
  if (status === 'pending') return 'degraded'
  return 'inactive'
}

function formatReceiptTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour12: false })
}

function formatActionName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

// ── Component ───────────────────────────────────────────────────

export function CommandCenter() {
  const recentFetcher = useCallback(() => fetchReceipts({ limit: 8 }), [])
  const { data: receiptsData } = usePolling({ fetcher: recentFetcher, interval: 15000 })
  const { data: statsData } = usePolling({ fetcher: fetchReceiptStats, interval: 30000 })

  const receipts: ReceiptItem[] = receiptsData?.receipts ?? []
  const todayCount = statsData?.stats?.total_receipts ?? 0
  const pendingCount = statsData?.stats?.by_status?.['pending'] ?? 0

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Command Center</h2>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: 2/3 width */}
        <div className="lg:col-span-2 space-y-6">
          {/* Active Task Monitor — WR-23 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Active Task</h3>
            <p className="text-sm text-text-muted">No active task</p>
          </section>

          {/* Chat Interface */}
          <ChatInterface />

          {/* Recent Activity Feed */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Recent Activity</h3>
            {receipts.length === 0 ? (
              <p className="text-sm text-text-muted">No recent activity</p>
            ) : (
              <div className="space-y-1">
                {receipts.map((r) => (
                  <div
                    key={r.id}
                    className="flex items-center justify-between px-3 py-2 bg-surface-card-elevated rounded-md hover:bg-surface-input/50 transition-colors"
                  >
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      <StatusDot state={receiptStatusState(r.status)} />
                      <div className="min-w-0 flex-1">
                        <span className="text-xs font-medium text-text-primary truncate block">
                          {formatActionName(r.action_name)}
                        </span>
                        <span className="text-[10px] text-text-muted font-mono">
                          {r.action_type}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 flex-shrink-0 ml-2">
                      {r.duration_ms !== null && (
                        <span className="text-[10px] text-text-muted font-mono">
                          {r.duration_ms < 1000 ? `${Math.round(r.duration_ms)}ms` : `${(r.duration_ms / 1000).toFixed(1)}s`}
                        </span>
                      )}
                      <span className="text-[10px] text-text-muted font-mono">
                        {formatReceiptTime(r.timestamp)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Right column: 1/3 width */}
        <div className="space-y-6">
          {/* Pending Actions — WR-15 */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Pending Actions</h3>
            <p className="text-sm text-text-muted">No pending actions</p>
          </section>

          {/* Controls Panel */}
          <ControlsPanel />

          {/* Quick Stats */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Quick Stats</h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Actions Today</span>
                <p className="text-xl font-mono font-bold text-text-primary">{todayCount}</p>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Pending</span>
                <p className="text-xl font-mono font-bold text-text-primary">{pendingCount}</p>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
