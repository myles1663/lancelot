import { usePolling } from '@/hooks'
import { fetchTrustRecords, fetchTrustProposals, fetchTrustTimeline, approveTrustProposal, declineTrustProposal } from '@/api'
import { TierBadge, MetricCard } from '@/components'

export function TrustLedger() {
  const { data: recordsData } = usePolling({ fetcher: fetchTrustRecords, interval: 15000 })
  const { data: proposalsData, refetch: refetchProposals } = usePolling({ fetcher: fetchTrustProposals, interval: 10000 })
  const { data: timelineData } = usePolling({ fetcher: fetchTrustTimeline, interval: 30000 })

  const records = recordsData?.records ?? []
  const proposals = proposalsData?.proposals ?? []
  const events = timelineData?.events ?? []

  const graduated = records.filter((r) => r.is_graduated).length
  const avgSuccess = records.length > 0
    ? (records.reduce((a, r) => a + r.success_rate, 0) / records.length * 100).toFixed(0)
    : '--'

  const handleProposal = async (id: string, action: 'approve' | 'decline') => {
    if (action === 'approve') await approveTrustProposal(id)
    else await declineTrustProposal(id)
    refetchProposals()
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Trust Ledger</h2>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Total Records" value={records.length} />
        <MetricCard label="Graduated" value={graduated} />
        <MetricCard label="Pending Proposals" value={proposals.length} />
        <MetricCard label="Avg Success Rate" value={`${avgSuccess}%`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Trust Records Table */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Per-Capability Trust
          </h3>
          <div className="overflow-auto max-h-96">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted uppercase tracking-wider border-b border-border-default">
                  <th className="px-3 py-2 text-left">Capability</th>
                  <th className="px-3 py-2 text-left">Tier</th>
                  <th className="px-3 py-2 text-right">Success</th>
                  <th className="px-3 py-2 text-right">Streak</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => (
                  <tr key={`${r.capability}-${r.scope}`} className="border-b border-border-default">
                    <td className="px-3 py-2 text-text-primary font-mono truncate max-w-[200px]" title={r.capability}>
                      {r.capability}
                    </td>
                    <td className="px-3 py-2">
                      <TierBadge tier={r.current_tier} compact />
                      {r.is_graduated && (
                        <span className="ml-1 text-[9px] text-state-healthy">G</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-text-secondary">
                      {(r.success_rate * 100).toFixed(0)}%
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-text-secondary">
                      {r.consecutive_successes}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <div className="space-y-6">
          {/* Graduation Proposals */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Graduation Proposals ({proposals.length})
            </h3>
            {proposals.length === 0 ? (
              <p className="text-sm text-text-muted">No pending proposals</p>
            ) : (
              <div className="space-y-3">
                {proposals.map((p) => (
                  <div key={p.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                    <p className="text-sm text-text-primary font-mono">{p.capability}</p>
                    <div className="flex items-center gap-2 mt-1">
                      <TierBadge tier={p.current_tier} compact />
                      <span className="text-text-muted">&rarr;</span>
                      <TierBadge tier={p.proposed_tier} compact />
                      <span className="text-[10px] text-text-muted font-mono ml-auto">
                        {p.consecutive_successes} streak
                      </span>
                    </div>
                    <div className="flex gap-2 mt-2">
                      <button
                        onClick={() => handleProposal(p.id, 'approve')}
                        className="px-3 py-1 text-xs bg-state-healthy/15 text-state-healthy rounded hover:bg-state-healthy/25"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleProposal(p.id, 'decline')}
                        className="px-3 py-1 text-xs bg-state-error/15 text-state-error rounded hover:bg-state-error/25"
                      >
                        Decline
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Timeline */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Graduation Timeline
            </h3>
            {events.length === 0 ? (
              <p className="text-sm text-text-muted">No graduation events yet</p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {events.slice(0, 20).map((e, i) => (
                  <div key={i} className="flex items-center gap-3 text-xs">
                    <span className="text-text-muted font-mono w-28 shrink-0">
                      {new Date(e.timestamp).toLocaleDateString()}
                    </span>
                    <TierBadge tier={e.from_tier} compact />
                    <span className="text-text-muted">&rarr;</span>
                    <TierBadge tier={e.to_tier} compact />
                    <span className="text-text-secondary truncate">{e.capability}</span>
                    <span className="text-[10px] text-text-muted ml-auto">{e.trigger}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
