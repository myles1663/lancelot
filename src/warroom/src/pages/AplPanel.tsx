import { usePolling, usePageTitle } from '@/hooks'
import { Activity, AlertTriangle, Bot, ClipboardCheck, ListChecks, Zap } from 'lucide-react'
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
import { TierBadge, ConfirmDialog } from '@/components'
import { useState } from 'react'

type AplTileTone = 'accent' | 'healthy' | 'warning' | 'muted'

const aplTileToneClass: Record<AplTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function AplTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: AplTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${aplTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function ruleStatusClass(status: string): string {
  if (status === 'active') return 'bg-state-healthy/15 text-state-healthy'
  if (status === 'paused') return 'bg-state-degraded/15 text-state-degraded'
  return 'bg-state-inactive/15 text-state-inactive'
}

export function AplPanel() {
  usePageTitle('Approval Learning')
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
  const pausedRules = rules.filter((r) => r.status === 'paused')
  const autoApproved = decisionsData?.auto_approved ?? 0
  const autoRate = decisionsData?.total
    ? `${Math.round((autoApproved / decisionsData.total) * 100)}%`
    : '--'

  const handlePause = async (id: string) => { await pauseAplRule(id); refetchRules() }
  const handleResume = async (id: string) => { await resumeAplRule(id); refetchRules() }
  const handleRevoke = async () => {
    if (revokeTarget) { await revokeAplRule(revokeTarget); setRevokeTarget(null); refetchRules() }
  }
  const handleActivate = async (id: string) => { await activateAplProposal(id); refetchProposals(); refetchRules() }
  const handleDecline = async (id: string) => { await declineAplProposal(id); refetchProposals() }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
              Approval Learning
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">Approval Pattern Learning</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Review learned automation rules, pending rule proposals, circuit breaker pressure, and recent approval
              decisions before expanding autonomous behavior.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono text-text-muted sm:flex sm:flex-wrap">
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              rules {rules.length}
            </span>
            <span className={`rounded border px-2 py-1 ${proposals.length > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input'}`}>
              proposals {proposals.length}
            </span>
            <span className={`rounded border px-2 py-1 ${breakers.length > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input'}`}>
              breakers {breakers.length}
            </span>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <AplTile
            label="Active Rules"
            value={activeRules.length}
            detail={`${pausedRules.length} paused rule${pausedRules.length === 1 ? '' : 's'}.`}
            tone={activeRules.length > 0 ? 'healthy' : 'muted'}
          />
          <AplTile
            label="Pending Proposals"
            value={proposals.length}
            detail="New learned patterns awaiting operator review."
            tone={proposals.length > 0 ? 'warning' : 'muted'}
          />
          <AplTile
            label="Total Decisions"
            value={decisionsData?.total ?? '--'}
            detail={`${autoApproved.toLocaleString()} auto-approved in this window.`}
            tone="accent"
          />
          <AplTile
            label="Auto Rate"
            value={autoRate}
            detail="Recent decision share handled by rules."
          />
          <AplTile
            label="Circuit Breakers"
            value={breakers.length}
            detail="Rules stopped by configured safety limits."
            tone={breakers.length > 0 ? 'warning' : 'healthy'}
          />
        </div>
      </div>

      {/* Circuit Breakers Warning */}
      {breakers.length > 0 && (
        <div className="rounded-lg border border-state-warning/30 bg-state-warning/10 p-4">
          <div className="mb-3 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-state-warning" aria-hidden="true" />
            <h4 className="text-xs font-semibold text-state-warning uppercase tracking-wider">
              Circuit Breakers Triggered ({breakers.length})
            </h4>
          </div>
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
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <Bot className="h-4 w-4 text-state-healthy" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Automation Rules
                </h3>
              </div>
              <p className="mt-1 text-xs text-text-muted">
                Learned rules that can approve matching low-risk actions without manual review.
              </p>
            </div>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              {activeRules.length} active
            </span>
          </div>
          <div className="space-y-3 max-h-96 overflow-y-auto">
            {rules.length === 0 ? (
              <p className="text-sm text-text-muted">No rules defined</p>
            ) : (
              rules.map((r) => (
                <div key={r.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium text-text-primary">{r.name}</span>
                        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${ruleStatusClass(r.status)}`}>
                          {r.status.toUpperCase()}
                        </span>
                        <span className="rounded border border-border-default bg-surface-input px-1.5 py-0.5 text-[10px] font-mono text-text-muted">
                          {r.pattern_type}
                        </span>
                      </div>
                      <p className="text-xs text-text-muted mt-1">{r.description}</p>
                    </div>
                    <div className="flex shrink-0 gap-1">
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
                  <div className="flex flex-wrap gap-4 mt-3 text-[10px] font-mono text-text-muted">
                    <span>Today: {r.auto_decisions_today}/{r.max_daily}</span>
                    <span>Total: {r.auto_decisions_total}/{r.max_total}</span>
                    <span className="truncate">{r.conditions_summary}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        <div className="space-y-6">
          {/* Proposals */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <ClipboardCheck className="h-4 w-4 text-state-warning" aria-hidden="true" />
                  <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                    Proposed Rules
                  </h3>
                </div>
                <p className="mt-1 text-xs text-text-muted">
                  Learned patterns waiting to become active automation rules.
                </p>
              </div>
              <span className={`rounded border px-2 py-1 text-xs font-mono ${proposals.length > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input text-text-muted'}`}>
                {proposals.length}
              </span>
            </div>
            {proposals.length === 0 ? (
              <p className="text-sm text-text-muted">No pending proposals</p>
            ) : (
              <div className="space-y-3">
                {proposals.map((p) => (
                  <div key={p.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-medium text-text-primary">{p.name}</p>
                          <span className="rounded border border-border-default bg-surface-input px-1.5 py-0.5 text-[10px] font-mono text-text-muted">
                            {p.pattern_type}
                          </span>
                        </div>
                        <p className="text-xs text-text-muted mt-1">{p.description}</p>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        <button onClick={() => handleActivate(p.id)} className="px-3 py-1 text-xs bg-state-healthy/15 text-state-healthy rounded hover:bg-state-healthy/25">
                          Activate
                        </button>
                        <button onClick={() => handleDecline(p.id)} className="px-3 py-1 text-xs bg-state-error/15 text-state-error rounded hover:bg-state-error/25">
                          Decline
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Recent Decisions */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <ListChecks className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                  <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                    Recent Decisions
                  </h3>
                </div>
                <p className="mt-1 text-xs text-text-muted">
                  Latest approvals and denials influenced by APL rules.
                </p>
              </div>
              <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
                {decisions.length}
              </span>
            </div>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {decisions.length === 0 ? (
                <p className="text-sm text-text-muted">No decisions yet</p>
              ) : (
                decisions.map((d) => (
                  <div key={d.id} className="grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-3 rounded border border-border-default bg-surface-card-elevated p-2 text-xs">
                    <TierBadge tier={d.risk_tier} compact />
                    <span className={`font-mono ${d.decision === 'approved' ? 'text-state-healthy' : 'text-state-error'}`}>
                      {d.decision.toUpperCase()}
                    </span>
                    <span className="text-text-primary truncate flex-1">{d.capability}</span>
                    {d.is_auto ? (
                      <span className="text-[10px] px-1.5 py-0.5 bg-accent-primary/15 text-accent-primary rounded font-mono">AUTO</span>
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 bg-surface-input text-text-muted rounded font-mono">MANUAL</span>
                    )}
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-state-healthy" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Rule Posture</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Active and paused rules show how much approval behavior has been delegated to learned automation.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Learning Flow</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Proposals should be reviewed before becoming rules; activation expands automation and decline keeps manual review.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-state-warning" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Safety Limits</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Circuit breakers are the operator signal that automation has hit a configured usage boundary.
          </p>
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
