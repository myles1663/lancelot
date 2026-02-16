import { usePolling } from '@/hooks'
import { fetchGovernanceStats, fetchGovernanceDecisions, fetchGovernanceApprovals, approveItem, denyItem } from '@/api'
import { MetricCard, TierBadge } from '@/components'
import type { GovernanceDecision, ApprovalItem } from '@/api/governance'

export function GovernanceDashboard() {
  const { data: statsData } = usePolling({ fetcher: fetchGovernanceStats, interval: 10000 })
  const { data: decisionsData, refetch: refetchDecisions } = usePolling({
    fetcher: () => fetchGovernanceDecisions(20),
    interval: 10000,
  })
  const { data: approvalsData, refetch: refetchApprovals } = usePolling({
    fetcher: fetchGovernanceApprovals,
    interval: 10000,
  })

  const trustStats = (statsData?.stats?.trust ?? {}) as Record<string, number>
  const aplStats = (statsData?.stats?.apl ?? {}) as Record<string, number>
  const decisions = decisionsData?.decisions ?? []
  const approvals = approvalsData?.approvals ?? []

  const handleAction = async (id: string, action: 'approve' | 'deny') => {
    if (action === 'approve') await approveItem(id)
    else await denyItem(id)
    refetchApprovals()
    refetchDecisions()
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Governance Dashboard</h2>

      {/* Metrics row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <MetricCard label="Trust Records" value={trustStats.total_records ?? '--'} />
        <MetricCard label="Graduated" value={trustStats.graduated_records ?? '--'} />
        <MetricCard label="Pending Proposals" value={trustStats.pending_proposals ?? '--'} />
        <MetricCard label="Active Rules" value={aplStats.active_rules ?? '--'} />
        <MetricCard
          label="Automation Rate"
          value={aplStats.automation_rate != null ? `${(Number(aplStats.automation_rate) * 100).toFixed(0)}%` : '--'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Approval Queue */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Approval Queue ({approvals.length})
          </h3>
          {approvals.length === 0 ? (
            <p className="text-sm text-text-muted">No pending approvals</p>
          ) : (
            <div className="space-y-3">
              {approvals.map((item: ApprovalItem) => (
                <div key={item.id} className={`flex items-center justify-between p-3 bg-surface-card-elevated rounded-md border ${item.type === 'sentry' ? 'border-state-warning/50' : 'border-border-default'}`}>
                  <div>
                    <div className="flex items-center gap-2">
                      {item.type === 'sentry' ? (
                        <span className="text-xs font-mono px-1.5 py-0.5 bg-state-warning/15 text-state-warning rounded">T3 ACTION</span>
                      ) : (
                        <span className="text-xs font-mono text-text-muted">{item.type}</span>
                      )}
                      {item.current_tier != null && <TierBadge tier={item.current_tier} compact />}
                      {item.proposed_tier != null && (
                        <>
                          <span className="text-text-muted text-xs">&rarr;</span>
                          <TierBadge tier={item.proposed_tier} compact />
                        </>
                      )}
                    </div>
                    <p className="text-sm text-text-primary mt-1">{item.name || item.capability || item.id}</p>
                    {item.type === 'sentry' && item.params && Object.keys(item.params).length > 0 && (
                      <p className="text-xs text-text-muted mt-0.5 font-mono truncate max-w-xs">{JSON.stringify(item.params)}</p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleAction(item.id, 'approve')}
                      className="px-3 py-1 text-xs bg-state-healthy/15 text-state-healthy rounded hover:bg-state-healthy/25 transition-colors"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => handleAction(item.id, 'deny')}
                      className="px-3 py-1 text-xs bg-state-error/15 text-state-error rounded hover:bg-state-error/25 transition-colors"
                    >
                      Deny
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Decision Log */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Recent Decisions
          </h3>
          {decisions.length === 0 ? (
            <p className="text-sm text-text-muted">No decisions yet</p>
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {decisions.map((d: GovernanceDecision) => (
                <div key={d.id} className="flex items-center gap-3 p-2 rounded bg-surface-card-elevated text-sm">
                  <TierBadge tier={d.risk_tier} compact />
                  <span className={`font-mono text-xs ${d.decision === 'approved' ? 'text-state-healthy' : 'text-state-error'}`}>
                    {d.decision.toUpperCase()}
                  </span>
                  <span className="text-text-primary truncate flex-1">{d.capability}</span>
                  {d.is_auto && (
                    <span className="text-[10px] px-1.5 py-0.5 bg-accent-primary/15 text-accent-primary rounded font-mono">
                      AUTO
                    </span>
                  )}
                  <span className="text-[10px] text-text-muted font-mono">
                    {new Date(d.recorded_at).toLocaleTimeString()}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
