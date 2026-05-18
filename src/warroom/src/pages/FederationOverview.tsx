import { useEffect, useState, type ReactNode } from 'react'
import {
  Activity,
  ChevronDown,
  CircleDot,
  GitBranch,
  KeyRound,
  Link2,
  Network,
  RadioTower,
  Route,
  Save,
  ShieldCheck,
  WalletCards,
} from 'lucide-react'
import { usePageTitle } from '@/hooks'
import { usePolling } from '@/hooks/usePolling'
import {
  fetchFederationStatus,
  fetchFederationHealth,
  fetchActiveTopology,
  fetchFederationSettings,
  updateFederationSettings,
  type FederationStatus,
  type FederationHealthSummary,
  type FederationSettings,
  type TopologyDocument,
} from '@/api/federation'
import { PageLoader } from '@/components/PageLoader'
import { StatusDot } from '@/components/StatusDot'
import { getErrorMessage } from '@/utils/errors'

type TileTone = 'healthy' | 'warning' | 'error' | 'muted' | 'accent'

function boolStateLabel(value?: boolean): string {
  return value ? 'running' : 'degraded'
}

function boolTone(value?: boolean): TileTone {
  return value ? 'healthy' : 'warning'
}

function readableState(value?: string): string {
  return (value || 'unknown').replace(/_/g, ' ')
}

function edgeStateColor(state: string): string {
  switch (state) {
    case 'green': return 'bg-state-healthy'
    case 'yellow': return 'bg-state-warning'
    case 'red': return 'bg-state-error'
    default: return 'bg-state-inactive'
  }
}

function roleLabel(role: string): string {
  switch (role) {
    case 'root': return 'ROOT'
    case 'child': return 'CHILD'
    case 'peer': return 'PEER'
    case 'leaf': return 'LEAF'
    default: return role.toUpperCase()
  }
}

function statusTone(value?: string): TileTone {
  const normalized = (value || '').toLowerCase()
  if (['healthy', 'green', 'ready', 'running', 'normal', 'synchronized', 'consistent'].includes(normalized)) return 'healthy'
  if (['red', 'critical', 'lost', 'error', 'degraded', 'blocked', 'exceeded'].includes(normalized)) return 'error'
  if (['yellow', 'warning', 'stale', 'restricted', 'spawn_restricted'].includes(normalized)) return 'warning'
  if (['disabled', 'inactive', 'none'].includes(normalized)) return 'muted'
  return 'accent'
}

function tileToneClass(tone: TileTone): string {
  return {
    healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
    warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
    error: 'border-state-error/30 bg-state-error/10 text-state-error',
    muted: 'border-border-default bg-surface-card text-text-muted',
    accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  }[tone]
}

function SignalTile({
  label,
  value,
  detail,
  icon,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  icon: ReactNode
  tone?: TileTone
}) {
  return (
    <div className={`min-w-0 rounded-lg border p-4 ${tileToneClass(tone)}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
        <div className="shrink-0">{icon}</div>
      </div>
      <div className="mt-3 truncate text-2xl font-semibold leading-tight text-text-primary" title={String(value)}>
        {value}
      </div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function RuntimeTile({
  label,
  value,
  tone,
}: {
  label: string
  value: string | number
  tone: TileTone
}) {
  return (
    <div className={`rounded-lg border px-3 py-3 ${tileToneClass(tone)}`}>
      <div className="flex items-center gap-2">
        <CircleDot className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</span>
      </div>
      <div className="mt-2 truncate text-sm font-semibold text-text-primary" title={String(value)}>
        {value}
      </div>
    </div>
  )
}

function ToggleButton({
  expanded,
  onClick,
  children,
}: {
  expanded: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs font-medium text-text-secondary transition-colors hover:border-border-active hover:text-text-primary"
    >
      {children}
      <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`} aria-hidden="true" />
    </button>
  )
}

function NodeCard({ node }: { node: TopologyDocument['nodes'][0] }) {
  const statusColor = node.connection_status === 'green'
    ? 'border-state-healthy'
    : node.connection_status === 'grey'
      ? 'border-state-inactive'
      : 'border-border-default'

  return (
    <div className={`min-w-0 rounded-lg border bg-surface-card-elevated p-4 ${statusColor}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <h3 className="min-w-0 truncate text-sm font-semibold text-text-primary">
          {node.instance_name || node.node_id}
        </h3>
        <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] ${
          node.is_local ? 'bg-accent-primary/20 text-accent-primary' : 'bg-surface-input text-text-muted'
        }`}>
          {node.is_local ? 'LOCAL' : roleLabel(node.instance_role)}
        </span>
      </div>
      <div className="space-y-2 text-xs text-text-secondary">
        {node.endpoint && (
          <div className="truncate font-mono" title={node.endpoint}>{node.endpoint}</div>
        )}
        <div className="flex min-w-0 items-center gap-2">
          <span>Soul: {node.soul_version || 'none'}</span>
          {node.soul_version_hash && (
            <span className="font-mono text-text-muted">{node.soul_version_hash.slice(0, 8)}</span>
          )}
        </div>
        {node.hive_config.enabled && (
          <div className="flex items-center gap-1">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent-secondary" />
            HIVE ({node.hive_config.max_concurrent_agents} agents)
          </div>
        )}
        <div className="text-text-muted">
          Budget: ${node.budget_config.daily_ceiling_usd.toFixed(2)}/day
        </div>
      </div>
    </div>
  )
}

function EdgeRow({
  edge,
  nodes,
}: {
  edge: TopologyDocument['edges'][0]
  nodes: TopologyDocument['nodes']
}) {
  const [expanded, setExpanded] = useState(false)
  const sourceName = nodes.find((n) => n.node_id === edge.source_node_id)?.instance_name || edge.source_node_id
  const targetName = nodes.find((n) => n.node_id === edge.target_node_id)?.instance_name || edge.target_node_id

  return (
    <div className="overflow-hidden rounded-lg border border-border-default">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-card-elevated"
      >
        <div className="flex min-w-0 items-center gap-3">
          <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${edgeStateColor(edge.compatibility_state)}`} />
          <span className="truncate text-sm text-text-primary">{sourceName}</span>
          <span className="text-xs text-text-muted">to</span>
          <span className="truncate text-sm text-text-primary">{targetName}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="hidden text-xs text-text-muted sm:inline">{readableState(edge.relationship_type)}</span>
          {edge.yellow_acknowledgments.length > 0 && (
            <span className="rounded bg-state-warning/20 px-1.5 py-0.5 text-[10px] text-state-warning">
              ACK'd
            </span>
          )}
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className={`transition-transform ${expanded ? 'rotate-180' : ''}`}>
            <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </div>
      </button>
      {expanded && (
        <div className="border-t border-border-default px-4 pb-3">
          <div className="mt-3 space-y-2">
            {edge.dimension_results.map((dim) => (
              <div key={dim.dimension} className="flex items-start gap-2">
                <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${edgeStateColor(dim.state)}`} />
                <div className="min-w-0">
                  <span className="text-xs font-medium text-text-primary">
                    {readableState(dim.dimension)}
                  </span>
                  <p className="text-xs text-text-secondary">{dim.report}</p>
                  {dim.resolution_options.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {dim.resolution_options.map((opt, i) => (
                        <span key={`${opt}-${i}`} className="rounded bg-surface-input px-1.5 py-0.5 text-[10px] text-text-muted">
                          {opt}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          {edge.handoff_contract.success_criteria.length > 0 && (
            <div className="mt-3 border-t border-border-default pt-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Contract</span>
              <ul className="mt-1 space-y-0.5">
                {edge.handoff_contract.success_criteria.map((criterion, i) => (
                  <li key={`${criterion}-${i}`} className="flex items-center gap-1.5 text-xs text-text-secondary">
                    <span className="h-1 w-1 rounded-full bg-text-muted" />
                    {criterion}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function FederationOverview() {
  usePageTitle('Federation Overview')
  const [selfAddressDraft, setSelfAddressDraft] = useState('')
  const [settingsDirty, setSettingsDirty] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [settingsMessage, setSettingsMessage] = useState('')
  const [showRuntimeDetail, setShowRuntimeDetail] = useState(false)
  const [showIdentityDetail, setShowIdentityDetail] = useState(false)
  const [showTopologyDetail, setShowTopologyDetail] = useState(false)

  const { data: status, loading: statusLoading } = usePolling<FederationStatus>({
    fetcher: fetchFederationStatus,
    interval: 10000,
  })

  const federationEnabled = status?.enabled === true

  const { data: settings, refetch: refetchSettings } = usePolling<FederationSettings>({
    fetcher: fetchFederationSettings,
    interval: 15000,
    enabled: federationEnabled,
  })

  const { data: health } = usePolling<FederationHealthSummary>({
    fetcher: fetchFederationHealth,
    interval: 10000,
    enabled: federationEnabled,
  })

  const { data: topology, loading: topoLoading } = usePolling<TopologyDocument>({
    fetcher: fetchActiveTopology,
    interval: 15000,
    enabled: federationEnabled,
  })

  useEffect(() => {
    if (settings && !settingsDirty) {
      setSelfAddressDraft(settings.self_address)
    }
  }, [settings, settingsDirty])

  if (statusLoading) return <PageLoader />

  const notEnabled = !statusLoading && !federationEnabled

  const handleSaveSettings = async () => {
    try {
      setSettingsSaving(true)
      setSettingsError('')
      const result = await updateFederationSettings(selfAddressDraft)
      setSettingsMessage(result.message)
      setSettingsDirty(false)
      await refetchSettings()
    } catch (error) {
      setSettingsError(getErrorMessage(error, 'Failed to save federation settings'))
    } finally {
      setSettingsSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-default bg-surface-card px-5 py-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-accent-primary">
              <GitBranch className="h-3.5 w-3.5" aria-hidden="true" />
              Federation Control Plane
            </div>
            <h1 className="mt-2 text-2xl font-semibold leading-tight text-text-primary">Federation Overview</h1>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Configure this Lancelot instance, inspect runtime signals, and review the deployed federation topology.
            </p>
          </div>
          {status && (
            <div className={`rounded-lg border px-4 py-3 ${tileToneClass(status.enabled ? (status.runtime_degraded ? 'warning' : 'healthy') : 'muted')}`}>
              <div className="flex items-center gap-2">
                <StatusDot state={status.enabled ? (status.runtime_degraded ? 'error' : 'healthy') : 'inactive'} />
                <span className="text-sm font-semibold text-text-primary">
                  {status.enabled ? status.deployment_mode.toUpperCase() : 'DISABLED'}
                </span>
              </div>
              <div className="mt-2 text-xs leading-5 text-text-muted">
                {status.enabled
                  ? `${status.peer_count} peers, ${status.soul_consistency || 'unknown'} soul state.`
                  : 'Federation feature flag is not active.'}
              </div>
            </div>
          )}
        </div>
      </section>

      {notEnabled && (
        <div className="rounded-lg border border-border-default bg-surface-card p-6 text-center">
          <p className="text-text-secondary">
            Federation is not enabled. Set <code className="font-mono text-sm text-accent-primary">FEATURE_FEDERATION=true</code> to activate.
          </p>
        </div>
      )}

      {status && federationEnabled && (
        <>
          {status.runtime_degraded && (
            <div className="space-y-3 rounded-lg border border-state-error/40 bg-state-error/10 p-4">
              <div className="flex items-center gap-2">
                <StatusDot state="error" />
                <h2 className="text-sm font-semibold text-text-primary">Federation Runtime Degraded</h2>
              </div>
              {status.degraded_reasons && status.degraded_reasons.length > 0 && (
                <div className="space-y-1">
                  {status.degraded_reasons.map((reason) => (
                    <div key={reason} className="text-xs text-text-secondary">
                      {reason}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <section className={`rounded-lg border p-4 ${
            status.runtime_degraded
              ? 'border-state-error/40 bg-state-error/10'
              : !topology
                ? 'border-state-warning/40 bg-state-warning/10'
                : 'border-state-healthy/30 bg-state-healthy/10'
          }`}>
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Operator Focus</div>
                <h2 className="mt-1 text-base font-semibold text-text-primary">
                  {status.runtime_degraded
                    ? 'Runtime needs attention'
                    : !topology
                      ? 'No active topology deployed'
                      : 'Federation is ready'}
                </h2>
                <p className="mt-1 text-sm leading-6 text-text-muted">
                  {status.runtime_degraded
                    ? 'Review degraded reasons first, then inspect runtime detail only if the summary does not explain the issue.'
                    : !topology
                      ? 'Use Graph Builder when you are ready to define or deploy a federation topology.'
                      : 'No immediate operator action is required. Expand sections below only when changing configuration or investigating a peer.'}
                </p>
              </div>
              <div className="flex flex-wrap gap-2 text-xs">
                <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1 text-text-secondary">
                  {status.peer_count} peers
                </span>
                <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1 text-text-secondary">
                  {status.deployment_mode}
                </span>
                <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1 text-text-secondary">
                  {topology ? `${topology.nodes.length} nodes / ${topology.edges.length} edges` : 'topology none'}
                </span>
              </div>
            </div>
          </section>

          <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <SignalTile label="Instances" value={(health?.total_peers ?? 0) + 1} detail="Local plus registered peers." icon={<Network className="h-4 w-4" />} tone="accent" />
            <SignalTile label="Soul State" value={status.soul_consistency || 'N/A'} detail="Root soul propagation posture." icon={<ShieldCheck className="h-4 w-4" />} tone={statusTone(status.soul_consistency)} />
            <SignalTile label="Cost Threshold" value={readableState(status.cost_threshold || 'normal')} detail="Federated budget gate." icon={<WalletCards className="h-4 w-4" />} tone={statusTone(status.cost_threshold)} />
            <SignalTile label="Topology" value={topology ? `v${topology.version}` : 'none'} detail={topology ? topology.topology_name : 'No active topology.'} icon={<Route className="h-4 w-4" />} tone={topology ? 'healthy' : 'muted'} />
          </section>

          <section className="rounded-lg border border-border-default bg-surface-card p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <RadioTower className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                  <h2 className="text-sm font-semibold text-text-primary">Runtime Signals</h2>
                </div>
                <p className="mt-1 text-xs text-text-secondary">
                  Primary transport signals. Expand for circuit and subscription internals.
                </p>
              </div>
              <ToggleButton expanded={showRuntimeDetail} onClick={() => setShowRuntimeDetail((value) => !value)}>
                {showRuntimeDetail ? 'Hide Detail' : 'Show Detail'}
              </ToggleButton>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <RuntimeTile label="Transport" value={boolStateLabel(status.transport_started)} tone={boolTone(status.transport_started)} />
              <RuntimeTile label="Heartbeat Mesh" value={boolStateLabel(status.heartbeat_mesh_running)} tone={boolTone(status.heartbeat_mesh_running)} />
              <RuntimeTile label="Open Circuits" value={status.circuit_breaker_summary?.open ?? 0} tone={(status.circuit_breaker_summary?.open ?? 0) > 0 ? 'error' : 'healthy'} />
            </div>

            {showRuntimeDetail && (
              <>
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <RuntimeTile label="Cost Reporter" value={boolStateLabel(status.cost_reporter_running)} tone={boolTone(status.cost_reporter_running)} />
                  <RuntimeTile label="Transport Ready" value={status.transport_ready ? 'ready' : 'degraded'} tone={boolTone(status.transport_ready)} />
                  <RuntimeTile label="Stale Cost Peers" value={status.stale_instance_ids?.length ?? 0} tone={(status.stale_instance_ids?.length ?? 0) > 0 ? 'warning' : 'healthy'} />
                </div>

                <div className="mt-4 grid grid-cols-1 gap-4 text-xs lg:grid-cols-2">
                  <div className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Circuit Breakers</div>
                    <div className="mt-2 flex flex-wrap gap-3 text-text-secondary">
                      <span>Closed: {status.circuit_breaker_summary?.closed ?? 0}</span>
                      <span>Half Open: {status.circuit_breaker_summary?.half_open ?? 0}</span>
                      <span>Open: {status.circuit_breaker_summary?.open ?? 0}</span>
                    </div>
                  </div>
                  <div className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Subscriptions</div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {status.subscription_status && Object.keys(status.subscription_status).length > 0 ? (
                        Object.entries(status.subscription_status).map(([peerId, subscriptionState]) => (
                          <span key={peerId} className="max-w-full break-all rounded bg-surface-input px-2 py-1 font-mono text-text-secondary">
                            {peerId}: {subscriptionState}
                            {status.subscription_stream_outcome?.[peerId] ? ` (${status.subscription_stream_outcome[peerId]})` : ''}
                            {status.subscription_stream_errors?.[peerId] ? ` - ${status.subscription_stream_errors[peerId]}` : ''}
                          </span>
                        ))
                      ) : (
                        <span className="text-text-muted">No active subscriptions</span>
                      )}
                    </div>
                  </div>
                </div>
                {status.stale_instance_ids && status.stale_instance_ids.length > 0 && (
                  <div className="mt-4 space-y-2">
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Stale Budget Peers</div>
                    <div className="flex flex-wrap gap-2">
                      {status.stale_instance_ids.map((peerId) => (
                        <span key={peerId} className="rounded bg-state-warning/15 px-2 py-1 font-mono text-state-warning">
                          {peerId}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </section>

          {settings && (
            <section className="rounded-lg border border-border-default bg-surface-card p-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <KeyRound className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold text-text-primary">This Instance</h2>
                  </div>
                  <p className="mt-1 text-xs text-text-secondary">
                    Local advertised address and identity used by federation peers.
                  </p>
                </div>
                <div className="shrink-0 text-right text-xs text-text-muted">
                  <div>{settings.deployment_mode.toUpperCase()}</div>
                  <div>{settings.restart_required ? 'Restart required after save' : 'Live update'}</div>
                </div>
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
                <div className="space-y-2">
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Self Address</div>
                    <input
                      value={selfAddressDraft}
                      onChange={(e) => {
                        setSelfAddressDraft(e.target.value)
                        setSettingsDirty(true)
                        setSettingsMessage('')
                        setSettingsError('')
                      }}
                      placeholder="https://lancelot.example.internal:8000"
                      className="mt-1 w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
                    />
                    <p className="mt-1 text-xs text-text-muted">
                      This is the externally reachable address other federation peers call during registration.
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={handleSaveSettings}
                      disabled={settingsSaving || !settingsDirty}
                      className="inline-flex items-center gap-2 rounded border border-accent-primary bg-accent-primary/10 px-4 py-2 text-sm font-medium text-accent-primary hover:bg-accent-primary/20 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Save className="h-3.5 w-3.5" aria-hidden="true" />
                      {settingsSaving ? 'Saving...' : 'Save Address'}
                    </button>
                    {settingsMessage && <span className="text-xs text-state-healthy">{settingsMessage}</span>}
                    {settingsError && <span className="text-xs text-state-error">{settingsError}</span>}
                    <ToggleButton expanded={showIdentityDetail} onClick={() => setShowIdentityDetail((value) => !value)}>
                      {showIdentityDetail ? 'Hide Identity' : 'Show Identity'}
                    </ToggleButton>
                  </div>
                </div>

                {showIdentityDetail && (
                  <div className="grid grid-cols-1 gap-3">
                    <div className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                      <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Instance ID</div>
                      <div className="mt-1 break-all font-mono text-sm text-text-primary">{settings.instance_id}</div>
                    </div>
                    <div className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                      <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Fingerprint</div>
                      <div className="mt-1 break-all font-mono text-sm text-text-primary">{settings.fingerprint}</div>
                    </div>
                    <div className="rounded-lg border border-border-default bg-surface-card-elevated p-3">
                      <div className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">Public Key</div>
                      <div className="mt-1 break-all font-mono text-xs text-text-secondary">{settings.public_key}</div>
                    </div>
                  </div>
                )}
              </div>
            </section>
          )}

          {health && health.total_peers > 0 && (
            <section className="rounded-lg border border-border-default bg-surface-card p-4">
              <div className="mb-3 flex items-center gap-2">
                <Activity className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                <h2 className="text-sm font-semibold text-text-primary">Peer Health</h2>
              </div>
              <div className="flex flex-wrap gap-4">
                {[
                  { label: 'Healthy', count: health.healthy, color: 'bg-state-healthy' },
                  { label: 'Warning', count: health.warning, color: 'bg-state-warning' },
                  { label: 'Critical', count: health.critical, color: 'bg-state-error' },
                  { label: 'Lost', count: health.lost, color: 'bg-state-inactive' },
                ].map(({ label, count, color }) => (
                  <div key={label} className="flex items-center gap-2">
                    <span className={`h-2.5 w-2.5 rounded-full ${color}`} />
                    <span className="text-sm text-text-secondary">{label}: {count}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {topology && (
            <section className="space-y-4 rounded-lg border border-border-default bg-surface-card p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <Link2 className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold text-text-primary">Topology: {topology.topology_name}</h2>
                  </div>
                  <span className="mt-1 block font-mono text-xs text-text-muted">
                    v{topology.version} ({topology.version_hash.slice(0, 8)}) / {topology.nodes.length} nodes / {topology.edges.length} edges
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {topology.deployed_at && (
                    <span className="rounded border border-state-healthy/30 bg-state-healthy/10 px-2 py-1 text-xs text-state-healthy">
                      Deployed {new Date(topology.deployed_at).toLocaleDateString()}
                    </span>
                  )}
                  <ToggleButton expanded={showTopologyDetail} onClick={() => setShowTopologyDetail((value) => !value)}>
                    {showTopologyDetail ? 'Hide Topology' : 'Show Topology'}
                  </ToggleButton>
                </div>
              </div>

              {showTopologyDetail && (
                <>
                  <div>
                    <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-text-muted">
                      Nodes ({topology.nodes.length})
                    </h3>
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
                      {topology.nodes.map((node) => (
                        <NodeCard key={node.node_id} node={node} />
                      ))}
                    </div>
                  </div>

                  {topology.edges.length > 0 && (
                    <div>
                      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-text-muted">
                        Edges ({topology.edges.length})
                      </h3>
                      <div className="space-y-2">
                        {topology.edges.map((edge) => (
                          <EdgeRow key={edge.edge_id} edge={edge} nodes={topology.nodes} />
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </section>
          )}

          {!topology && !topoLoading && (
            <div className="rounded-lg border border-border-default bg-surface-card p-6 text-center">
              <p className="text-sm text-text-secondary">
                No active topology. Use the Graph Builder to create one.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
