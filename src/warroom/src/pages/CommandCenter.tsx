import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchReceipts } from '@/api/receipts'
import { fetchReceiptStats } from '@/api/receipts'
import type { ReceiptItem } from '@/api/receipts'
import { fetchPendingActionCards } from '@/api/actioncards'
import { fetchGovernanceApprovals } from '@/api/governance'
import type { ApprovalItem } from '@/api/governance'
import type { ActionCardData } from '@/types/api'
import { StatusDot } from '@/components'
import { ChatInterface } from './command/ChatInterface'
import { ControlsPanel } from './command/ControlsPanel'
import { ActiveWorkPanel } from './command/ActiveWorkPanel'
import { formatTimeOnly } from '@/utils/dateFormat'

// ── Helpers ─────────────────────────────────────────────────────

function receiptStatusState(status: string): 'healthy' | 'error' | 'degraded' | 'inactive' {
  if (status === 'success') return 'healthy'
  if (status === 'failure') return 'error'
  if (status === 'pending') return 'degraded'
  return 'inactive'
}

function formatActionName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function startOfLocalDayIso(): string {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  return start.toISOString()
}

function actionCardTime(card: ActionCardData): string {
  return formatTimeOnly(new Date(card.presentedAt * 1000).toISOString())
}

// ── Component ───────────────────────────────────────────────────

export function CommandCenter() {
  usePageTitle('Command Center')
  const recentFetcher = useCallback(() => fetchReceipts({ limit: 8 }), [])
  const todayStatsFetcher = useCallback(() => fetchReceiptStats(startOfLocalDayIso()), [])
  const { data: receiptsData } = usePolling({ fetcher: recentFetcher, interval: 15000 })
  const { data: statsData } = usePolling({ fetcher: todayStatsFetcher, interval: 30000 })
  const { data: approvalsData, error: approvalsError } = usePolling({
    fetcher: fetchGovernanceApprovals,
    interval: 10000,
  })
  const { data: actionCardsData, error: actionCardsError } = usePolling({
    fetcher: fetchPendingActionCards,
    interval: 10000,
  })

  const receipts: ReceiptItem[] = receiptsData?.receipts ?? []
  const approvals: ApprovalItem[] = approvalsData?.approvals ?? []
  const actionCards: ActionCardData[] = actionCardsData?.cards.filter(card => !card.resolved) ?? []
  const todayCount = statsData?.stats?.total_receipts ?? 0
  const failedTodayCount = statsData?.stats?.by_status?.['failure'] ?? 0
  const pendingActionCount = approvals.length + actionCards.length
  const pendingUnavailable = Boolean(approvalsError || actionCardsError)

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Command Center</h2>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left column: 2/3 width */}
        <div className="lg:col-span-2 space-y-6">
          {/* Active work ledger */}
          <ActiveWorkPanel />

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
                        {formatTimeOnly(r.timestamp)}
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
          {/* Fleet dashboard entry */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Multi-Agent Dashboard</h3>
                <p className="mt-1 text-xs text-text-muted">
                  Fleet health, approvals, trust proposals, and instance entry points
                </p>
              </div>
            </div>
            <Link
              to="/federation/fleet"
              className="mt-4 inline-flex w-full items-center justify-center rounded border border-accent-primary bg-accent-primary/10 px-3 py-2 text-sm font-medium text-accent-primary hover:bg-accent-primary/20"
            >
              Open Fleet Dashboard
            </Link>
          </section>

          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">Pending Actions</h3>
            {pendingUnavailable ? (
              <p className="text-sm text-state-degraded">Pending action data unavailable</p>
            ) : pendingActionCount === 0 ? (
              <p className="text-sm text-text-muted">No pending actions</p>
            ) : (
              <div className="space-y-2">
                {approvals.slice(0, 4).map((item) => (
                  <Link
                    key={`approval-${item.id}`}
                    to="/governance"
                    className="block rounded-md border border-state-warning/30 bg-state-warning/10 px-3 py-2 hover:bg-state-warning/15"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] font-mono uppercase tracking-wider text-state-warning">
                        Approval
                      </span>
                      <span className="text-[10px] font-mono text-text-muted">
                        {formatTimeOnly(item.created_at)}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-xs font-medium text-text-primary">
                      {item.name || item.capability || item.id}
                    </p>
                  </Link>
                ))}
                {actionCards.slice(0, Math.max(0, 4 - approvals.length)).map((card) => (
                  <Link
                    key={`action-card-${card.cardId}`}
                    to="/command"
                    className="block rounded-md border border-accent-primary/30 bg-accent-primary/10 px-3 py-2 hover:bg-accent-primary/15"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] font-mono uppercase tracking-wider text-accent-primary">
                        Action Card
                      </span>
                      <span className="text-[10px] font-mono text-text-muted">
                        {actionCardTime(card)}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-xs font-medium text-text-primary">
                      {card.title || card.cardId}
                    </p>
                  </Link>
                ))}
                {pendingActionCount > 4 && (
                  <p className="text-[11px] text-text-muted">
                    {pendingActionCount - 4} more pending item{pendingActionCount - 4 === 1 ? '' : 's'}.
                  </p>
                )}
              </div>
            )}
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
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Approvals</span>
                <p className="text-xl font-mono font-bold text-text-primary">{approvals.length}</p>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Action Cards</span>
                <p className="text-xl font-mono font-bold text-text-primary">{actionCards.length}</p>
              </div>
              <div>
                <span className="text-[10px] uppercase tracking-wider text-text-muted">Failed Today</span>
                <p className={`text-xl font-mono font-bold ${failedTodayCount > 0 ? 'text-state-error' : 'text-text-primary'}`}>
                  {failedTodayCount}
                </p>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
