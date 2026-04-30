import { useCallback, useEffect, useState } from 'react'
import { usePageTitle, usePolling } from '@/hooks'
import {
  approveFederationDashboardApproval,
  denyFederationDashboardApproval,
  fetchFederationDashboard,
  subscribeFederationDashboard,
  type FleetActivityEvent,
  type FleetApproval,
  type FleetDashboardInstance,
  type FleetTrustProposal,
} from '@/api/federation'
import { MetricCard } from '@/components/MetricCard'
import { PageLoader } from '@/components/PageLoader'
import { formatRelativeTime, formatTimestamp } from '@/utils/dateFormat'
import { getErrorMessage } from '@/utils/errors'

const STATE_STYLES: Record<FleetDashboardInstance['state'], {
  border: string
  dot: string
  text: string
  label: string
}> = {
  healthy: {
    border: 'border-state-healthy/70',
    dot: 'bg-state-healthy',
    text: 'text-state-healthy',
    label: 'Healthy',
  },
  attention: {
    border: 'border-state-warning/80',
    dot: 'bg-state-warning',
    text: 'text-state-warning',
    label: 'Attention',
  },
  critical: {
    border: 'border-state-error',
    dot: 'bg-state-error',
    text: 'text-state-error',
    label: 'Critical',
  },
  paused: {
    border: 'border-accent-primary',
    dot: 'bg-accent-primary',
    text: 'text-accent-primary',
    label: 'Paused',
  },
}

type DashboardDecision = 'approve' | 'deny'

interface DecisionTarget {
  decision: DashboardDecision
  instanceId: string
  approvalId: string
  title: string
  subtitle: string
}

function roleLabel(role: string): string {
  if (!role) return 'PEER'
  return role.toUpperCase()
}

function compactHash(hash: string): string {
  return hash ? hash.slice(0, 10) : 'unavailable'
}

function formatHeartbeat(instance: FleetDashboardInstance): string {
  if (instance.heartbeat_state === 'lost') return 'LOST'
  if (instance.heartbeat_age_s === null) return instance.heartbeat_state.toUpperCase()
  if (instance.heartbeat_age_s < 60) return `${Math.round(instance.heartbeat_age_s)}s ago`
  return formatRelativeTime(instance.last_heartbeat_at)
}

function formatPct(value: number): string {
  return `${Math.round(value)}%`
}

function instanceGridClass(count: number): string {
  if (count <= 1) return 'grid grid-cols-1 gap-4 lg:max-w-2xl'
  if (count === 2) return 'grid grid-cols-1 gap-4 lg:grid-cols-2 xl:max-w-5xl'
  return 'grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3'
}

function DetailPill({ label, value, tone = 'muted' }: {
  label: string
  value: string | number
  tone?: 'muted' | 'warning' | 'error' | 'healthy'
}) {
  const toneClass = {
    muted: 'text-text-secondary bg-surface-input',
    warning: 'text-state-warning bg-state-warning/10',
    error: 'text-state-error bg-state-error/10',
    healthy: 'text-state-healthy bg-state-healthy/10',
  }[tone]

  return (
    <div className={`min-h-[52px] min-w-0 rounded px-2.5 py-2 ${toneClass}`}>
      <div className="text-[10px] text-text-muted">{label}</div>
      <div className="truncate text-xs font-medium">{value}</div>
    </div>
  )
}

function InstanceCard({ instance }: { instance: FleetDashboardInstance }) {
  const style = STATE_STYLES[instance.state]
  const approvalTone = instance.pending_approvals > 0 ? 'warning' : 'muted'
  const proposalTone = instance.trust_proposals > 0 ? 'warning' : 'muted'
  const budgetTone = instance.budget_threshold === 'normal'
    ? 'healthy'
    : instance.budget_threshold === 'warning' || instance.budget_threshold === 'spawn_restricted'
      ? 'warning'
      : 'error'
  const commandCenterUrl = instance.command_center_url || (instance.is_self ? '/war-room/command' : '')

  return (
    <article className={`min-h-[28rem] min-w-0 rounded-lg border bg-surface-card p-5 ${style.border}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className={`h-2.5 w-2.5 rounded-full ${style.dot}`} />
            <h2 className="truncate text-sm font-semibold text-text-primary">{instance.name}</h2>
          </div>
          <div className="mt-1 font-mono text-[11px] text-text-muted">
            {instance.instance_short_id || 'local'}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className={`text-[11px] font-medium ${style.text}`}>{style.label}</span>
          <span className="rounded bg-surface-input px-2 py-1 text-[10px] text-text-secondary">
            {instance.is_self ? 'SELF' : roleLabel(instance.role)}
          </span>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2">
        <DetailPill label="Health" value={instance.health.toUpperCase()} tone={instance.health === 'healthy' ? 'healthy' : instance.health === 'paused' ? 'warning' : 'error'} />
        <DetailPill label="Heartbeat" value={formatHeartbeat(instance)} tone={instance.heartbeat_state === 'fresh' ? 'healthy' : instance.heartbeat_state === 'warning' ? 'warning' : 'error'} />
        <DetailPill label="Active Agents" value={instance.active_agents} />
        <DetailPill label="Approvals" value={instance.pending_approvals} tone={approvalTone} />
        <DetailPill label="Trust Proposals" value={instance.trust_proposals} tone={proposalTone} />
        <DetailPill label="Budget" value={formatPct(instance.budget_utilization_pct)} tone={budgetTone} />
      </div>

      <div className="mt-4 space-y-2 border-t border-border-default pt-3">
        <div className="flex items-center justify-between gap-3 text-xs">
          <span className="text-text-muted">Soul</span>
          <span className={instance.soul_matches_root === false ? 'text-state-warning' : 'text-text-secondary'}>
            {compactHash(instance.soul_version_hash)}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded bg-surface-input">
          <div
            className={`h-full ${budgetTone === 'healthy' ? 'bg-state-healthy' : budgetTone === 'warning' ? 'bg-state-warning' : 'bg-state-error'}`}
            style={{ width: `${Math.min(100, Math.max(0, instance.budget_utilization_pct))}%` }}
          />
        </div>
      </div>

      <div className="mt-4 min-h-12">
        <div className="mb-1 text-[10px] uppercase tracking-wider text-text-muted">
          Latest Activity
        </div>
        {instance.recent_activity ? (
          <p className="line-clamp-2 text-xs text-text-secondary">
            {instance.recent_activity}
            {instance.recent_activity_at && (
              <span className="ml-1 text-text-muted">({formatRelativeTime(instance.recent_activity_at)})</span>
            )}
          </p>
        ) : (
          <p className="text-xs text-text-muted">No recent activity</p>
        )}
      </div>

      {instance.attention_reasons.length > 0 && (
        <div className="mt-3 space-y-1.5 border-t border-border-default pt-3">
          <div className="text-[10px] uppercase tracking-wider text-text-muted">
            Needs Attention
          </div>
          {instance.attention_reasons.slice(0, 4).map((reason) => (
            <div
              key={reason}
              className="truncate rounded bg-surface-input px-2 py-1 text-[11px] text-text-secondary"
              title={reason}
            >
              {reason}
            </div>
          ))}
        </div>
      )}

      <div className="mt-4">
        <a
          href={commandCenterUrl}
          target={instance.is_self ? undefined : '_blank'}
          rel={instance.is_self ? undefined : 'noreferrer'}
          aria-label={`Open ${instance.name} Command Center`}
          className={`inline-flex w-full items-center justify-center rounded border px-3 py-2 text-sm font-medium ${
            commandCenterUrl
              ? 'border-accent-primary bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/20'
              : 'pointer-events-none border-border-default text-text-muted'
          }`}
        >
          Open Command Center
        </a>
      </div>
    </article>
  )
}

function ApprovalsTable({
  approvals,
  onDecision,
}: {
  approvals: FleetApproval[]
  onDecision: (target: DecisionTarget) => void
}) {
  if (approvals.length === 0) {
    return (
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <h2 className="text-sm font-semibold text-text-primary">Unified Approval Queue</h2>
        <p className="mt-3 text-sm text-text-muted">No pending approvals</p>
      </section>
    )
  }

  return (
    <section className="rounded-lg border border-border-default bg-surface-card p-4">
      <h2 className="text-sm font-semibold text-text-primary">Unified Approval Queue</h2>
      <div className="mt-3 overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="text-xs text-text-muted">
            <tr>
              <th className="py-2 pr-4 font-medium">Instance</th>
              <th className="py-2 pr-4 font-medium">Action</th>
              <th className="py-2 pr-4 font-medium">Tier</th>
              <th className="py-2 pr-4 font-medium">Capability</th>
              <th className="py-2 pr-4 font-medium">Waiting</th>
              <th className="py-2 pr-4 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {approvals.map((item) => (
              <tr key={`${item.instance_id}-${item.id}`} className="border-t border-border-default">
                <td className="py-2 pr-4 text-text-primary">{item.instance_name}</td>
                <td className="py-2 pr-4 text-text-secondary">
                  <div>{item.action_name}</div>
                  {item.context && (
                    <div className="mt-1 max-w-sm truncate text-xs text-text-muted" title={item.context}>
                      {item.context}
                    </div>
                  )}
                </td>
                <td className="py-2 pr-4">
                  <span className="rounded bg-state-warning/10 px-2 py-1 text-xs text-state-warning">
                    {item.risk_tier || 'T3'}
                  </span>
                </td>
                <td className="max-w-md truncate py-2 pr-4 font-mono text-xs text-text-secondary" title={item.context}>
                  {item.capability}
                </td>
                <td className="py-2 pr-4 text-text-muted">{formatRelativeTime(item.waiting_since || item.created_at)}</td>
                <td className="py-2 pr-4">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onDecision({
                        decision: 'approve',
                        instanceId: item.instance_id,
                        approvalId: item.id,
                        title: item.action_name || item.capability,
                        subtitle: `${item.instance_name} - ${item.risk_tier || 'T3'}`,
                      })}
                      className="rounded border border-state-healthy/60 px-2 py-1 text-xs font-medium text-state-healthy hover:bg-state-healthy/10"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => onDecision({
                        decision: 'deny',
                        instanceId: item.instance_id,
                        approvalId: item.id,
                        title: item.action_name || item.capability,
                        subtitle: `${item.instance_name} - ${item.risk_tier || 'T3'}`,
                      })}
                      className="rounded border border-state-error/60 px-2 py-1 text-xs font-medium text-state-error hover:bg-state-error/10"
                    >
                      Deny
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function TrustTable({
  proposals,
  onDecision,
}: {
  proposals: FleetTrustProposal[]
  onDecision: (target: DecisionTarget) => void
}) {
  if (proposals.length === 0) {
    return (
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <h2 className="text-sm font-semibold text-text-primary">Unified Trust Proposals</h2>
        <p className="mt-3 text-sm text-text-muted">No pending trust proposals</p>
      </section>
    )
  }

  return (
    <section className="rounded-lg border border-border-default bg-surface-card p-4">
      <h2 className="text-sm font-semibold text-text-primary">Unified Trust Proposals</h2>
      <div className="mt-3 overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="text-xs text-text-muted">
            <tr>
              <th className="py-2 pr-4 font-medium">Instance</th>
              <th className="py-2 pr-4 font-medium">Capability</th>
              <th className="py-2 pr-4 font-medium">Current</th>
              <th className="py-2 pr-4 font-medium">Proposed</th>
              <th className="py-2 pr-4 font-medium">Successes</th>
              <th className="py-2 pr-4 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {proposals.map((item) => (
              <tr key={`${item.instance_id}-${item.id}`} className="border-t border-border-default">
                <td className="py-2 pr-4 text-text-primary">{item.instance_name}</td>
                <td className="max-w-md truncate py-2 pr-4 font-mono text-xs text-text-secondary">{item.capability}</td>
                <td className="py-2 pr-4 text-text-secondary">T{item.current_tier}</td>
                <td className="py-2 pr-4 text-state-healthy">T{item.proposed_tier}</td>
                <td className="py-2 pr-4 text-text-muted">{item.consecutive_successes}</td>
                <td className="py-2 pr-4">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onDecision({
                        decision: 'approve',
                        instanceId: item.instance_id,
                        approvalId: item.id,
                        title: item.capability,
                        subtitle: `${item.instance_name} - T${item.current_tier} to T${item.proposed_tier}`,
                      })}
                      className="rounded border border-state-healthy/60 px-2 py-1 text-xs font-medium text-state-healthy hover:bg-state-healthy/10"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => onDecision({
                        decision: 'deny',
                        instanceId: item.instance_id,
                        approvalId: item.id,
                        title: item.capability,
                        subtitle: `${item.instance_name} - T${item.current_tier} to T${item.proposed_tier}`,
                      })}
                      className="rounded border border-state-error/60 px-2 py-1 text-xs font-medium text-state-error hover:bg-state-error/10"
                    >
                      Deny
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function ActivityFeed({ events }: { events: FleetActivityEvent[] }) {
  return (
    <section className="rounded-lg border border-border-default bg-surface-card p-4">
      <h2 className="text-sm font-semibold text-text-primary">Fleet Activity</h2>
      {events.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No recent fleet activity</p>
      ) : (
        <div className="mt-3 max-h-80 space-y-2 overflow-y-auto">
          {events.slice(0, 40).map((event) => (
            <div key={`${event.instance_id}-${event.id}-${event.timestamp}`} className="rounded bg-surface-card-elevated px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm text-text-primary">{event.description}</div>
                  <div className="truncate text-xs text-text-muted">{event.instance_name} - {event.event_type}</div>
                </div>
                <div className="shrink-0 text-right text-[11px] text-text-muted">
                  {formatRelativeTime(event.timestamp)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function DecisionDialog({
  target,
  reason,
  error,
  submitting,
  onReasonChange,
  onCancel,
  onConfirm,
}: {
  target: DecisionTarget | null
  reason: string
  error: Error | null
  submitting: boolean
  onReasonChange: (value: string) => void
  onCancel: () => void
  onConfirm: () => void
}) {
  if (!target) return null

  const label = target.decision === 'approve' ? 'Approve' : 'Deny'
  const confirmClass = target.decision === 'approve'
    ? 'border-state-healthy/70 text-state-healthy hover:bg-state-healthy/10'
    : 'border-state-error/70 text-state-error hover:bg-state-error/10'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4">
      <div className="w-full max-w-lg rounded-lg border border-border-default bg-surface-card p-5 shadow-2xl">
        <div>
          <h2 className="text-base font-semibold text-text-primary">{label} Decision</h2>
          <p className="mt-1 truncate text-sm text-text-secondary" title={target.title}>
            {target.title}
          </p>
          <p className="mt-1 text-xs text-text-muted">{target.subtitle}</p>
        </div>

        <label className="mt-4 block text-xs font-medium text-text-muted" htmlFor="fleet-decision-reason">
          Reason
        </label>
        <textarea
          id="fleet-decision-reason"
          value={reason}
          onChange={(event) => onReasonChange(event.target.value)}
          className="mt-2 h-28 w-full resize-none rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary outline-none focus:border-accent-primary"
          maxLength={1000}
          autoFocus
        />

        {error && (
          <div className="mt-3 rounded border border-state-error/50 bg-state-error/10 px-3 py-2 text-sm text-state-error">
            {getErrorMessage(error, 'Decision failed')}
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded border border-border-default px-3 py-2 text-sm text-text-secondary hover:bg-surface-input disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={submitting || !reason.trim()}
            className={`rounded border px-3 py-2 text-sm font-medium disabled:opacity-50 ${confirmClass}`}
          >
            {submitting ? 'Submitting...' : label}
          </button>
        </div>
      </div>
    </div>
  )
}

export function FleetDashboard() {
  usePageTitle('Fleet Dashboard')
  const [streamData, setStreamData] = useState<Awaited<ReturnType<typeof fetchFederationDashboard>> | null>(null)
  const [streamConnected, setStreamConnected] = useState(false)
  const [streamError, setStreamError] = useState<Error | null>(null)
  const [decisionTarget, setDecisionTarget] = useState<DecisionTarget | null>(null)
  const [decisionReason, setDecisionReason] = useState('')
  const [decisionError, setDecisionError] = useState<Error | null>(null)
  const [decisionSubmitting, setDecisionSubmitting] = useState(false)
  const pollingIntervalMs = Math.max(
    2000,
    Math.round((streamData?.dashboard.poll_interval_s ?? 10) * 1000),
  )
  const { data: polledData, error: pollingError, loading, refetch } = usePolling({
    fetcher: fetchFederationDashboard,
    interval: pollingIntervalMs,
    enabled: !streamConnected || !streamData,
  })
  const data = streamConnected && streamData ? streamData : polledData ?? streamData
  const error = streamConnected ? streamError : pollingError ?? streamError

  useEffect(() => subscribeFederationDashboard(
    (snapshot) => {
      setStreamData(snapshot)
      setStreamError(null)
    },
    setStreamError,
    setStreamConnected,
  ), [])

  const openDecision = useCallback((target: DecisionTarget) => {
    setDecisionTarget(target)
    setDecisionReason('')
    setDecisionError(null)
  }, [])

  const closeDecision = useCallback(() => {
    if (decisionSubmitting) return
    setDecisionTarget(null)
    setDecisionReason('')
    setDecisionError(null)
  }, [decisionSubmitting])

  const confirmDecision = useCallback(async () => {
    if (!decisionTarget || !decisionReason.trim()) return

    setDecisionSubmitting(true)
    setDecisionError(null)
    try {
      if (decisionTarget.decision === 'approve') {
        await approveFederationDashboardApproval(
          decisionTarget.instanceId,
          decisionTarget.approvalId,
          decisionReason,
        )
      } else {
        await denyFederationDashboardApproval(
          decisionTarget.instanceId,
          decisionTarget.approvalId,
          decisionReason,
        )
      }
      setDecisionTarget(null)
      setDecisionReason('')
      if (streamConnected) {
        setStreamData(await fetchFederationDashboard())
      } else {
        refetch()
      }
    } catch (err) {
      setDecisionError(err instanceof Error ? err : new Error(String(err)))
    } finally {
      setDecisionSubmitting(false)
    }
  }, [decisionReason, decisionTarget, refetch, streamConnected])

  if (loading && !data) return <PageLoader />

  if (!data && error) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-text-primary">Fleet Dashboard</h1>
        <div className="rounded-lg border border-state-error/50 bg-state-error/10 p-4 text-sm text-state-error">
          {getErrorMessage(error, 'Failed to load fleet dashboard')}
        </div>
      </div>
    )
  }

  if (!data) return null

  if (!data.enabled) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-text-primary">Fleet Dashboard</h1>
        <div className="rounded-lg border border-border-default bg-surface-card p-6">
          <p className="text-sm text-text-secondary">
            {data.disabled_reason || 'Federation Dashboard is disabled'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Fleet Dashboard</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-text-muted">
            <span>Updated {formatTimestamp(data.generated_at)}</span>
            <span className={`rounded px-2 py-0.5 text-xs ${streamConnected ? 'bg-state-healthy/10 text-state-healthy' : 'bg-state-warning/10 text-state-warning'}`}>
              {streamConnected ? 'Live' : 'Polling'}
            </span>
          </div>
        </div>
        {error && (
          <div className="rounded border border-state-warning/50 bg-state-warning/10 px-3 py-2 text-sm text-state-warning">
            {getErrorMessage(error, 'Latest refresh failed')}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <MetricCard label="Instances" value={data.fleet.total_instances} />
        <MetricCard label="Need Attention" value={data.fleet.instances_needing_attention} />
        <MetricCard label="Critical" value={data.fleet.critical_instances} />
        <MetricCard label="Approvals" value={data.fleet.pending_approvals} />
        <MetricCard label="Active Agents" value={data.fleet.active_agents} />
        <MetricCard label="Fleet Cost" value={formatPct(data.fleet.fleet_cost_utilization_pct)} />
      </div>

      {data.errors.length > 0 && (
        <div className="rounded-lg border border-state-warning/40 bg-state-warning/10 p-4">
          <h2 className="text-sm font-semibold text-text-primary">Remote Detail Warnings</h2>
          <div className="mt-2 space-y-1">
            {data.errors.map((item) => (
              <p key={`${item.instance_id}-${item.message}`} className="text-sm text-state-warning">
                {item.instance_id}: {item.message}
              </p>
            ))}
          </div>
        </div>
      )}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">Instances</h2>
          <div className="text-xs text-text-muted">
            Soul {data.fleet.soul_consistency.toUpperCase()} - Budget {data.fleet.budget_threshold}
          </div>
        </div>
        <div className={instanceGridClass(data.instances.length)}>
          {data.instances.map((instance) => (
            <InstanceCard key={instance.instance_id || instance.name} instance={instance} />
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <ApprovalsTable approvals={data.approvals} onDecision={openDecision} />
        <TrustTable proposals={data.trust_proposals} onDecision={openDecision} />
      </div>

      <ActivityFeed events={data.activity} />

      <DecisionDialog
        target={decisionTarget}
        reason={decisionReason}
        error={decisionError}
        submitting={decisionSubmitting}
        onReasonChange={setDecisionReason}
        onCancel={closeDecision}
        onConfirm={confirmDecision}
      />
    </div>
  )
}
