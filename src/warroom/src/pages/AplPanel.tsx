import { usePolling } from '@/hooks'
import {
  fetchAplRules,
  fetchAplProposals,
  fetchAplDecisions,
  fetchCircuitBreakers,
  pauseAplRule,
  resumeAplRule,
  revokeAplRule,
  activateAplProposal,
  declineAplProposal,
} from '@/api'
import { MetricCard, TierBadge, ConfirmDialog } from '@/components'
import { useState } from 'react'

export function AplPanel() {
  const { data: rulesData, refetch: refetchRules } = usePolling({ fetcher: fetchAplRules, interval: 15000 })
  const { data: proposalsData, refetch: refetchProposals } = usePolling({ fetcher: fetchAplProposals, interval: 10000 })
  const { data: decisionsData } = usePolling({ fetcher: () => fetchAplDecisions(30), interval: 15000 })
  const { data: breakersData } = usePolling({ fetcher: fetchCircuitBreakers, interval: 30000 })

  const [revokeTarget, setRevokeTarget] = useState<string | null>(null)

  const rules = rulesData?.rules ?? []
  const proposals = proposalsData?.proposals ?? []
  const decisions = decisionsData?.decisions ?? []
  const breakers = breakersData?.circuit_breakers ?? []

  const activeRules = rules.filter((r) => r.status === 'active')
  const autoRate = decisionsData?.total
    ? `${Math.round(((decisionsData.auto_approved ?? 0) / decisionsData.total) * 100)}%`
    : '--'

  const handlePause = async (id: string) => { await pauseAplRule(id); refetchRules() }
  const handleResume = async (id: string) => { await resumeAplRule(id); refetchRules() }
  const handleRevoke = async () => {
    if (revokeTarget) { await revokeAplRule(revokeTarget); setRevokeTarget(null); refetchRules() }
  }
  const handleActivate = async (id: string) => { await activateAplProposal(id); refetchProposals(); refetchRules() }
  const handleDecline = async (id: string) => { await declineAplProposal(id); refetchProposals() }

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Approval Pattern Learning</h2>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label="Active Rules" value={activeRules.length} />
        <MetricCard label="Pending Proposals" value={proposals.length} />
        <MetricCard label="Total Decisions" value={decisionsData?.total ?? '--'} />
        <MetricCard label="Auto Rate" value={autoRate} />
      </div>

      {/* Circuit Breakers Warning */}
      {breakers.length > 0 && (
        <div className="mb-6 p-3 bg-state-degraded/10 border border-state-degraded/30 rounded-lg">
          <h4 className="text-xs font-semibold text-state-degraded uppercase tracking-wider mb-2">
            Circuit Breakers Triggered ({breakers.length})
          </h4>
          {breakers.map((b) => (
            <div key={b.id} className="text-sm text-text-primary">
              {b.name} — {b.daily_usage}/{b.max_daily} daily limit
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Rules Table */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Active Rules ({activeRules.length})
          </h3>
          <div className="space-y-3 max-h-96 overflow-y-auto">
            {rules.length === 0 ? (
              <p className="text-sm text-text-muted">No rules defined</p>
            ) : (
              rules.map((r) => (
                <div key={r.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-sm font-medium text-text-primary">{r.name}</span>
                      <span className={`ml-2 text-[10px] font-mono px-1.5 py-0.5 rounded ${
                        r.status === 'active' ? 'bg-state-healthy/15 text-state-healthy'
                        : r.status === 'paused' ? 'bg-state-degraded/15 text-state-degraded'
                        : 'bg-state-inactive/15 text-state-inactive'
                      }`}>
                        {r.status.toUpperCase()}
                      </span>
                    </div>
                    <div className="flex gap-1">
                      {r.status === 'active' && (
                        <button onClick={() => handlePause(r.id)} className="px-2 py-1 text-[10px] text-state-degraded hover:bg-state-degraded/10 rounded">
                          Pause
                        </button>
                      )}
                      {r.status === 'paused' && (
                        <button onClick={() => handleResume(r.id)} className="px-2 py-1 text-[10px] text-state-healthy hover:bg-state-healthy/10 rounded">
                          Resume
                        </button>
                      )}
                      {(r.status === 'active' || r.status === 'paused') && (
                        <button onClick={() => setRevokeTarget(r.id)} className="px-2 py-1 text-[10px] text-state-error hover:bg-state-error/10 rounded">
                          Revoke
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-text-muted mt-1">{r.description}</p>
                  <div className="flex gap-4 mt-2 text-[10px] font-mono text-text-muted">
                    <span>Today: {r.auto_decisions_today}/{r.max_daily}</span>
                    <span>Total: {r.auto_decisions_total}/{r.max_total}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        <div className="space-y-6">
          {/* Proposals */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Proposed Rules ({proposals.length})
            </h3>
            {proposals.length === 0 ? (
              <p className="text-sm text-text-muted">No pending proposals</p>
            ) : (
              <div className="space-y-3">
                {proposals.map((p) => (
                  <div key={p.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                    <p className="text-sm font-medium text-text-primary">{p.name}</p>
                    <p className="text-xs text-text-muted mt-1">{p.description}</p>
                    <div className="flex gap-2 mt-2">
                      <button onClick={() => handleActivate(p.id)} className="px-3 py-1 text-xs bg-state-healthy/15 text-state-healthy rounded hover:bg-state-healthy/25">
                        Activate
                      </button>
                      <button onClick={() => handleDecline(p.id)} className="px-3 py-1 text-xs bg-state-error/15 text-state-error rounded hover:bg-state-error/25">
                        Decline
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Recent Decisions */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
              Recent Decisions
            </h3>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {decisions.length === 0 ? (
                <p className="text-sm text-text-muted">No decisions yet</p>
              ) : (
                decisions.map((d) => (
                  <div key={d.id} className="flex items-center gap-3 p-2 rounded bg-surface-card-elevated text-xs">
                    <TierBadge tier={d.risk_tier} compact />
                    <span className={`font-mono ${d.decision === 'approved' ? 'text-state-healthy' : 'text-state-error'}`}>
                      {d.decision.toUpperCase()}
                    </span>
                    <span className="text-text-primary truncate flex-1">{d.capability}</span>
                    {d.is_auto && (
                      <span className="text-[10px] px-1 bg-accent-primary/15 text-accent-primary rounded font-mono">AUTO</span>
                    )}
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </div>

      <ConfirmDialog
        open={!!revokeTarget}
        title="Revoke Rule"
        description="This will permanently deactivate this automation rule. Future matching actions will require manual approval."
        variant="destructive"
        confirmLabel="Revoke"
        onConfirm={handleRevoke}
        onCancel={() => setRevokeTarget(null)}
      />
    </div>
  )
}
