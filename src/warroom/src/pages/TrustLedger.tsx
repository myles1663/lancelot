import { useState } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import { Activity, ClipboardCheck, History, ShieldCheck, TrendingUp } from 'lucide-react'
import { fetchTrustRecords, fetchTrustProposals, fetchTrustTimeline, approveTrustProposal, declineTrustProposal } from '@/api'
import { TierBadge, EmptyState, Pagination } from '@/components'
import { formatDateOnly } from '@/utils/dateFormat'

const RECORDS_PER_PAGE = 20
const EVENTS_PER_PAGE = 20

type TrustTileTone = 'accent' | 'healthy' | 'warning' | 'muted'

const trustTileToneClass: Record<TrustTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function TrustTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: TrustTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${trustTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

export function TrustLedger() {
  usePageTitle('Trust Ledger')
  const { data: recordsData } = usePolling({ fetcher: fetchTrustRecords, interval: 15000 })
  const { data: proposalsData, refetch: refetchProposals } = usePolling({ fetcher: fetchTrustProposals, interval: 10000 })
  const { data: timelineData } = usePolling({ fetcher: fetchTrustTimeline, interval: 30000 })

  const [recordsPage, setRecordsPage] = useState(1)
  const [timelinePage, setTimelinePage] = useState(1)

  const records = recordsData?.records ?? []
  const proposals = proposalsData?.proposals ?? []
  const events = timelineData?.events ?? []

  const graduated = records.filter((r) => r.is_graduated).length
  const avgSuccess = records.length > 0
    ? (records.reduce((a, r) => a + r.success_rate, 0) / records.length * 100).toFixed(0)
    : '--'
  const tierOneRecords = records.filter((r) => r.current_tier === 1).length
  const tierTwoPlusRecords = records.filter((r) => r.current_tier >= 2).length

  // Client-side pagination
  const paginatedRecords = records.slice(
    (recordsPage - 1) * RECORDS_PER_PAGE,
    recordsPage * RECORDS_PER_PAGE,
  )
  const paginatedEvents = events.slice(
    (timelinePage - 1) * EVENTS_PER_PAGE,
    timelinePage * EVENTS_PER_PAGE,
  )

  const handleProposal = async (id: string, action: 'approve' | 'decline') => {
    if (action === 'approve') await approveTrustProposal(id)
    else await declineTrustProposal(id)
    refetchProposals()
  }

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-border-default bg-surface-card p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-accent-primary">
              Trust Audit
            </div>
            <h2 className="mt-2 text-2xl font-semibold text-text-primary">Trust Ledger</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Inspect per-capability autonomy posture, pending graduation proposals, and the recorded history of trust
              tier movement.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono text-text-muted sm:flex sm:flex-wrap">
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              records {records.length}
            </span>
            <span className={`rounded border px-2 py-1 ${proposals.length > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input'}`}>
              proposals {proposals.length}
            </span>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1">
              events {events.length}
            </span>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <TrustTile
            label="Total Records"
            value={records.length.toLocaleString()}
            detail="Capability and scope trust records."
            tone="accent"
          />
          <TrustTile
            label="Graduated"
            value={graduated.toLocaleString()}
            detail="Capabilities with graduated trust."
            tone="healthy"
          />
          <TrustTile
            label="Pending Proposals"
            value={proposals.length.toLocaleString()}
            detail="Graduation changes awaiting review."
            tone={proposals.length > 0 ? 'warning' : 'muted'}
          />
          <TrustTile
            label="Avg Success"
            value={`${avgSuccess}%`}
            detail="Mean success rate across records."
          />
          <TrustTile
            label="Higher Autonomy"
            value={tierTwoPlusRecords.toLocaleString()}
            detail={`${tierOneRecords.toLocaleString()} records remain at tier 1.`}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Trust Records Table */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                  Per-Capability Trust
                </h3>
              </div>
              <p className="mt-1 text-xs text-text-muted">
                Current autonomy tier, success rate, and consecutive success streak by capability.
              </p>
            </div>
            <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
              {records.length}
            </span>
          </div>
          <div className="overflow-auto">
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
                {paginatedRecords.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-8 text-center text-sm text-text-muted">
                      No trust records loaded.
                    </td>
                  </tr>
                ) : (
                  paginatedRecords.map((r) => (
                    <tr key={`${r.capability}-${r.scope}`} className="border-b border-border-default hover:bg-surface-card-elevated/50">
                      <td className="px-3 py-2 text-text-primary font-mono truncate max-w-[220px]" title={r.capability}>
                        {r.capability}
                        <div className="mt-0.5 text-[10px] text-text-muted truncate" title={r.scope}>
                          {r.scope}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <TierBadge tier={r.current_tier} compact />
                          {r.is_graduated ? (
                            <span className="rounded border border-state-healthy/40 bg-state-healthy/10 px-1.5 py-0.5 text-[9px] font-mono text-state-healthy">
                              GRAD
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-text-secondary">
                        {(r.success_rate * 100).toFixed(0)}%
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-text-secondary">
                        {r.consecutive_successes}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <Pagination
            currentPage={recordsPage}
            totalPages={Math.ceil(records.length / RECORDS_PER_PAGE)}
            onPageChange={setRecordsPage}
            totalItems={records.length}
          />
        </section>

        <div className="space-y-6">
          {/* Graduation Proposals */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <ClipboardCheck className="h-4 w-4 text-state-warning" aria-hidden="true" />
                  <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                    Graduation Proposals
                  </h3>
                </div>
                <p className="mt-1 text-xs text-text-muted">
                  Proposed trust tier changes requiring operator review.
                </p>
              </div>
              <span className={`rounded border px-2 py-1 text-xs font-mono ${proposals.length > 0 ? 'border-state-warning/40 bg-state-warning/10 text-state-warning' : 'border-border-default bg-surface-input text-text-muted'}`}>
                {proposals.length}
              </span>
            </div>
            {proposals.length === 0 ? (
              <EmptyState title="No Pending Proposals" description="No graduation proposals awaiting review." />
            ) : (
              <div className="space-y-3">
                {proposals.map((p) => (
                  <div key={p.id} className="p-3 bg-surface-card-elevated rounded-md border border-border-default">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <p className="text-sm text-text-primary font-mono truncate">{p.capability}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <TierBadge tier={p.current_tier} compact />
                          <span className="text-text-muted">&rarr;</span>
                          <TierBadge tier={p.proposed_tier} compact />
                          <span className="text-[10px] text-text-muted font-mono">
                            {p.consecutive_successes} streak
                          </span>
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-2">
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
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Timeline */}
          <section className="bg-surface-card border border-border-default rounded-lg p-4">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <History className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                  <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
                    Graduation Timeline
                  </h3>
                </div>
                <p className="mt-1 text-xs text-text-muted">
                  Historical tier movement and the trigger behind each graduation.
                </p>
              </div>
              <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
                {events.length}
              </span>
            </div>
            {events.length === 0 ? (
              <EmptyState title="No Graduation Events" description="Trust tier progressions will appear here as capabilities earn higher autonomy." />
            ) : (
              <div className="space-y-2">
                {paginatedEvents.map((e, i) => (
                  <div key={i} className="grid grid-cols-[6rem_auto_auto_auto_minmax(0,1fr)] items-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs">
                    <span className="text-text-muted font-mono w-28 shrink-0">
                      {formatDateOnly(e.timestamp)}
                    </span>
                    <TierBadge tier={e.from_tier} compact />
                    <span className="text-text-muted">&rarr;</span>
                    <TierBadge tier={e.to_tier} compact />
                    <span className="text-text-secondary truncate">{e.capability}</span>
                    <span className="col-span-5 text-[10px] text-text-muted sm:col-span-1 sm:text-right">{e.trigger}</span>
                  </div>
                ))}
              </div>
            )}
            <Pagination
              currentPage={timelinePage}
              totalPages={Math.ceil(events.length / EVENTS_PER_PAGE)}
              onPageChange={setTimelinePage}
              totalItems={events.length}
            />
          </section>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-state-healthy" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Graduation Signal</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Success streaks and tier movements show where Lancelot is earning narrowly scoped autonomy.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-state-warning" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Review Pressure</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Pending proposals are the operator workload; they should remain visible but not dominate the audit table.
          </p>
        </div>
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">Audit Trail</h3>
          </div>
          <p className="mt-2 text-xs leading-5 text-text-muted">
            Timeline entries provide the retained evidence for why a capability moved between trust tiers.
          </p>
        </div>
      </div>
    </div>
  )
}
