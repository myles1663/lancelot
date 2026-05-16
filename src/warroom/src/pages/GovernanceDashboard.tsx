import { usePolling, usePageTitle } from '@/hooks'
import { Activity, ClipboardCheck, Scale, ShieldCheck, Zap } from 'lucide-react'
import { fetchGovernanceStats, fetchGovernanceDecisions, fetchGovernanceApprovals, approveItem, denyItem } from '@/api'
import { TierBadge, EmptyState } from '@/components'
import type { GovernanceDecision, ApprovalItem } from '@/api/governance'
import { formatTimeOnly } from '@/utils/dateFormat'

type GovernanceTileTone = 'accent' | 'healthy' | 'warning' | 'muted'

const governanceTileToneClass: Record<GovernanceTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function statValue(value: number | undefined): string {
  return value == null ? '--' : value.toLocaleString()
}

function GovernanceTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: GovernanceTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${governanceTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

export function GovernanceDashboard() {
  usePageTitle('Governance')
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
  const sentryApprovals = approvals.filter((item) => item.type === 'sentry').length
  const autoDecisions = decisions.filter((decision) => decision.is_auto).length
  const deniedDecisions = decisions.filter((decision) => decision.decision !== 'approved').length
  const automationRate =
    aplStats.automation_rate != null ? `${(Number(aplStats.automation_rate) * 100).toFixed(0)}%` : '--'

  const handleAction = async (id: string, action: 'approve' | 'deny') => {
    if (action === 'approve') await approveItem(id)
    else await denyItem(id)
    refetchApprovals()
    refetchDecisions()
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
              Governance Control
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">Governance Dashboard</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Monitor trust progression, approval pressure, APL automation, and recent governance decisions from the
              operator review surface.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono text-text-muted sm:flex sm:flex-wrap">
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              approvals {approvals.length}
            </span>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              decisions {decisions.length}
            </span>
            <span className={`rounded border px-2 py-1 ${sentryApprovals > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input'}`}>
              T3 {sentryApprovals}
            </span>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <GovernanceTile
            label="Trust Records"
            value={statValue(trustStats.total_records)}
            detail="Tracked trust ledger records."
            tone="accent"
          />
          <GovernanceTile
            label="Graduated"
            value={statValue(trustStats.graduated_records)}
            detail="Records promoted through trust stages."
            tone="healthy"
          />
          <GovernanceTile
            label="Pending Proposals"
            value={statValue(trustStats.pending_proposals)}
            detail="Trust changes still awaiting resolution."
            tone={(trustStats.pending_proposals ?? 0) > 0 ? 'warning' : 'muted'}
          />
          <GovernanceTile
            label="Active Rules"
            value={statValue(aplStats.active_rules)}
            detail="APL policies currently enforced."
          />
          <GovernanceTile
            label="Automation Rate"
            value={automationRate}
            detail={`${autoDecisions} automated decisions in the recent window.`}
            tone="healthy"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Approval Queue */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <ClipboardCheck className="h-4 w-4 text-state-warning" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Approval Queue
                </h3>
              </div>
              <p className="mt-1 text-xs text-text-muted">
                Operator decisions that block or release governed actions.
              </p>
            </div>
            <span className={`rounded border px-2 py-1 text-xs font-mono ${approvals.length > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input text-text-muted'}`}>
              {approvals.length}
            </span>
          </div>
          {approvals.length === 0 ? (
            <EmptyState title="No Pending Approvals" description="All governance items have been reviewed." />
          ) : (
            <div className="space-y-3">
              {approvals.map((item: ApprovalItem) => (
                <div key={item.id} className={`flex flex-col gap-3 p-3 bg-surface-card-elevated rounded-md border sm:flex-row sm:items-center sm:justify-between ${item.type === 'sentry' ? 'border-state-warning/50' : 'border-border-default'}`}>
                  <div className="min-w-0">
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
                    <p className="text-sm text-text-primary mt-1 truncate">{item.name || item.capability || item.id}</p>
                    {item.type === 'sentry' && item.params && Object.keys(item.params).length > 0 && (
                      <p className="text-xs text-text-muted mt-0.5 font-mono truncate max-w-xs">{JSON.stringify(item.params)}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-2">
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
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <Scale className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Recent Decisions
                </h3>
              </div>
              <p className="mt-1 text-xs text-text-muted">
                Latest approvals, denials, automation decisions, and risk tiers.
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
                {decisions.length}
              </span>
              {deniedDecisions > 0 ? (
                <span className="rounded border border-state-error/40 bg-state-error/10 px-2 py-1 text-xs font-mono text-state-error">
                  denied {deniedDecisions}
                </span>
              ) : null}
            </div>
          </div>
          {decisions.length === 0 ? (
            <EmptyState title="No Decisions" description="Governance decisions will appear here as actions are evaluated." />
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {decisions.map((d: GovernanceDecision) => (
                <div key={d.id} className="grid grid-cols-[auto_1fr] gap-3 rounded border border-border-default bg-surface-card-elevated p-3 text-sm sm:grid-cols-[auto_auto_minmax(0,1fr)_auto_auto] sm:items-center">
                  <div>
                    <TierBadge tier={d.risk_tier} compact />
                  </div>
                  <span className={`font-mono text-xs ${d.decision === 'approved' ? 'text-state-healthy' : 'text-state-error'}`}>
                    {d.decision.toUpperCase()}
                  </span>
                  <span className="col-span-2 min-w-0 truncate text-text-primary sm:col-span-1">{d.capability}</span>
                  {d.is_auto ? (
                    <span className="w-fit text-[10px] px-1.5 py-0.5 bg-accent-primary/15 text-accent-primary rounded font-mono">
                      AUTO
                    </span>
                  ) : (
                    <span className="w-fit text-[10px] px-1.5 py-0.5 bg-surface-input text-text-muted rounded font-mono">
                      MANUAL
                    </span>
                  )}
                  <span className="text-[10px] text-text-muted font-mono sm:text-right">
                    {formatTimeOnly(d.recorded_at)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-state-healthy" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Trust Posture</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Graduated records and pending proposals show whether trust changes are moving or backing up.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">APL Automation</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Automation rate should be read alongside active rules and denied decisions, not by itself.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-state-warning" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Review Load</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Pending approvals and T3 actions are the operator workload signal for this page.
          </p>
        </div>
      </div>
    </div>
  )
}
