import { useEffect, useState } from 'react'
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
import { MetricCard } from '@/components/MetricCard'
import { StatusDot } from '@/components/StatusDot'

function boolStateLabel(value?: boolean): string {
  return value ? 'running' : 'degraded'
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

// ── Node Card ────────────────────────────────────────────────

function NodeCard({ node }: { node: TopologyDocument['nodes'][0] }) {
  const statusColor = node.connection_status === 'green'
    ? 'border-state-healthy'
    : node.connection_status === 'grey'
      ? 'border-state-inactive'
      : 'border-border-default'

  return (
    <div className={`bg-surface-card-elevated border ${statusColor} rounded-lg p-4 min-w-[200px]`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-text-primary truncate">
          {node.instance_name || node.node_id}
        </h3>
        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
          node.is_local ? 'bg-accent-primary/20 text-accent-primary' : 'bg-surface-input text-text-muted'
        }`}>
          {node.is_local ? 'LOCAL' : roleLabel(node.instance_role)}
        </span>
      </div>
      <div className="space-y-1 text-xs text-text-secondary">
        {node.endpoint && (
          <div className="truncate" title={node.endpoint}>{node.endpoint}</div>
        )}
        <div className="flex items-center gap-2">
          <span>Soul: {node.soul_version || 'none'}</span>
          {node.soul_version_hash && (
            <span className="text-text-muted font-mono">{node.soul_version_hash.slice(0, 8)}</span>
          )}
        </div>
        {node.hive_config.enabled && (
          <div className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-accent-secondary inline-block" />
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

// ── Edge Row ─────────────────────────────────────────────────

function EdgeRow({ edge, nodes }: {
  edge: TopologyDocument['edges'][0]
  nodes: TopologyDocument['nodes']
}) {
  const [expanded, setExpanded] = useState(false)
  const sourceName = nodes.find(n => n.node_id === edge.source_node_id)?.instance_name || edge.source_node_id
  const targetName = nodes.find(n => n.node_id === edge.target_node_id)?.instance_name || edge.target_node_id

  return (
    <div className="border border-border-default rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface-card-elevated transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full ${edgeStateColor(edge.compatibility_state)}`} />
          <span className="text-sm text-text-primary">{sourceName}</span>
          <span className="text-text-muted">→</span>
          <span className="text-sm text-text-primary">{targetName}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">{edge.relationship_type.replace('_', ' ')}</span>
          {edge.yellow_acknowledgments.length > 0 && (
            <span className="text-[10px] bg-state-warning/20 text-state-warning px-1.5 py-0.5 rounded">
              ACK'd
            </span>
          )}
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"
            className={`transition-transform ${expanded ? 'rotate-180' : ''}`}>
            <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </div>
      </button>
      {expanded && (
        <div className="px-4 pb-3 border-t border-border-default">
          <div className="mt-3 space-y-2">
            {edge.dimension_results.map((dim) => (
              <div key={dim.dimension} className="flex items-start gap-2">
                <span className={`mt-1 w-2 h-2 rounded-full flex-shrink-0 ${edgeStateColor(dim.state)}`} />
                <div>
                  <span className="text-xs font-medium text-text-primary">
                    {dim.dimension.replace('_', ' ')}
                  </span>
                  <p className="text-xs text-text-secondary">{dim.report}</p>
                  {dim.resolution_options.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {dim.resolution_options.map((opt, i) => (
                        <span key={i} className="text-[10px] bg-surface-input text-text-muted px-1.5 py-0.5 rounded">
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
            <div className="mt-3 pt-2 border-t border-border-default">
              <span className="text-[10px] font-semibold text-text-muted tracking-wider">CONTRACT</span>
              <ul className="mt-1 space-y-0.5">
                {edge.handoff_contract.success_criteria.map((c, i) => (
                  <li key={i} className="text-xs text-text-secondary flex items-center gap-1.5">
                    <span className="w-1 h-1 rounded-full bg-text-muted" />
                    {c}
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

// ── Main Page ────────────────────────────────────────────────

export function FederationOverview() {
  const [selfAddressDraft, setSelfAddressDraft] = useState('')
  const [settingsDirty, setSettingsDirty] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [settingsMessage, setSettingsMessage] = useState('')

  const { data: status, loading: statusLoading } = usePolling<FederationStatus>({
    fetcher: fetchFederationStatus,
    interval: 10000,
  })

  const { data: settings, refetch: refetchSettings } = usePolling<FederationSettings>({
    fetcher: fetchFederationSettings,
    interval: 15000,
    enabled: Boolean(status?.enabled),
  })

  const { data: health } = usePolling<FederationHealthSummary>({
    fetcher: fetchFederationHealth,
    interval: 10000,
  })

  const { data: topology, loading: topoLoading } = usePolling<TopologyDocument>({
    fetcher: fetchActiveTopology,
    interval: 15000,
  })

  useEffect(() => {
    if (settings && !settingsDirty) {
      setSelfAddressDraft(settings.self_address)
    }
  }, [settings, settingsDirty])

  if (statusLoading) return <PageLoader />

  const notEnabled = status && !status.enabled

  const handleSaveSettings = async () => {
    try {
      setSettingsSaving(true)
      setSettingsError('')
      const result = await updateFederationSettings(selfAddressDraft)
      setSettingsMessage(result.message)
      setSettingsDirty(false)
      await refetchSettings()
    } catch (e: any) {
      setSettingsError(e.message || 'Failed to save federation settings')
    } finally {
      setSettingsSaving(false)
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Federation Overview</h1>
          <p className="text-sm text-text-secondary mt-0.5">
            Multi-instance coordination and governance
          </p>
        </div>
        {status && (
          <div className="flex items-center gap-2">
            <StatusDot state={status.enabled ? 'healthy' : 'inactive'} />
            <span className="text-sm text-text-secondary">
              {status.enabled ? status.deployment_mode.toUpperCase() : 'DISABLED'}
            </span>
          </div>
        )}
      </div>

      {notEnabled && (
        <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center">
          <p className="text-text-secondary">
            Federation is not enabled. Set <code className="text-accent-primary font-mono text-sm">FEATURE_FEDERATION=true</code> to activate.
          </p>
        </div>
      )}

      {status?.enabled && (
        <>
          {status.runtime_degraded && (
            <div className="bg-state-error/10 border border-state-error/40 rounded-lg p-4 space-y-3">
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

          {/* Summary Strip */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <MetricCard label="Instances" value={String((health?.total_peers ?? 0) + 1)} />
            <MetricCard label="Soul State" value={status.soul_consistency || 'N/A'} />
            <MetricCard label="Cost Threshold" value={status.cost_threshold || 'normal'} />
            <MetricCard label="Peer Count" value={String(status.peer_count)} />
            <MetricCard label="Deployment" value={status.deployment_mode} />
            <MetricCard label="Topology" value={topology ? `v${topology.version}` : 'none'} />
          </div>

          <div className="bg-surface-card border border-border-default rounded-lg p-4 space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-text-primary">Runtime Signals</h2>
              <p className="text-xs text-text-secondary mt-1">
                Live federation control-plane and transport state.
              </p>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
              <MetricCard label="Transport" value={boolStateLabel(status.transport_started)} />
              <MetricCard label="Heartbeat Mesh" value={boolStateLabel(status.heartbeat_mesh_running)} />
              <MetricCard label="Cost Reporter" value={boolStateLabel(status.cost_reporter_running)} />
              <MetricCard label="Transport Ready" value={status.transport_ready ? 'ready' : 'degraded'} />
              <MetricCard label="Open Circuits" value={String(status.circuit_breaker_summary?.open ?? 0)} />
              <MetricCard label="Stale Cost Peers" value={String(status.stale_instance_ids?.length ?? 0)} />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs">
              <div className="space-y-2">
                <div className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">Circuit Breakers</div>
                <div className="flex gap-4 text-text-secondary">
                  <span>Closed: {status.circuit_breaker_summary?.closed ?? 0}</span>
                  <span>Half Open: {status.circuit_breaker_summary?.half_open ?? 0}</span>
                  <span>Open: {status.circuit_breaker_summary?.open ?? 0}</span>
                </div>
              </div>
              <div className="space-y-2">
                <div className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">Subscriptions</div>
                <div className="flex flex-wrap gap-2">
                  {status.subscription_status && Object.keys(status.subscription_status).length > 0 ? (
                    Object.entries(status.subscription_status).map(([peerId, subscriptionState]) => (
                      <span
                        key={peerId}
                        className="px-2 py-1 rounded bg-surface-input text-text-secondary font-mono"
                      >
                        {peerId}: {subscriptionState}
                        {status.subscription_stream_outcome?.[peerId]
                          ? ` (${status.subscription_stream_outcome[peerId]})`
                          : ''}
                        {status.subscription_stream_errors?.[peerId]
                          ? ` - ${status.subscription_stream_errors[peerId]}`
                          : ''}
                      </span>
                    ))
                  ) : (
                    <span className="text-text-muted">No active subscriptions</span>
                  )}
                </div>
              </div>
            </div>
            {status.stale_instance_ids && status.stale_instance_ids.length > 0 && (
              <div className="space-y-2">
                <div className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">Stale Budget Peers</div>
                <div className="flex flex-wrap gap-2">
                  {status.stale_instance_ids.map((peerId) => (
                    <span key={peerId} className="px-2 py-1 rounded bg-state-warning/15 text-state-warning font-mono">
                      {peerId}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* This Instance */}
          {settings && (
            <div className="bg-surface-card border border-border-default rounded-lg p-4 space-y-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-sm font-semibold text-text-primary">This Instance</h2>
                  <p className="text-xs text-text-secondary mt-1">
                    Local federation identity and advertised address for peer bootstrap and graph deployment.
                  </p>
                </div>
                <div className="text-right text-xs text-text-muted">
                  <div>{settings.deployment_mode.toUpperCase()}</div>
                  <div>{settings.restart_required ? 'Restart required after save' : 'Live update'}</div>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <div>
                    <div className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">Self Address</div>
                    <input
                      value={selfAddressDraft}
                      onChange={(e) => {
                        setSelfAddressDraft(e.target.value)
                        setSettingsDirty(true)
                        setSettingsMessage('')
                        setSettingsError('')
                      }}
                      placeholder="https://lancelot.example.internal:8000"
                      className="mt-1 w-full px-3 py-2 text-sm bg-surface-input border border-border-default rounded text-text-primary"
                    />
                    <p className="mt-1 text-xs text-text-muted">
                      This is the externally reachable address other federation peers call during registration.
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleSaveSettings}
                      disabled={settingsSaving || !settingsDirty}
                      className="px-4 py-2 text-sm font-medium bg-accent-primary text-white rounded disabled:opacity-50 disabled:cursor-not-allowed hover:bg-accent-primary/80"
                    >
                      {settingsSaving ? 'Saving...' : 'Save Address'}
                    </button>
                    {settingsMessage && <span className="text-xs text-state-healthy">{settingsMessage}</span>}
                    {settingsError && <span className="text-xs text-state-error">{settingsError}</span>}
                  </div>
                </div>

                <div className="space-y-3">
                  <div>
                    <div className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">Instance ID</div>
                    <div className="mt-1 text-sm font-mono text-text-primary break-all">{settings.instance_id}</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">Fingerprint</div>
                    <div className="mt-1 text-sm font-mono text-text-primary break-all">{settings.fingerprint}</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">Public Key</div>
                    <div className="mt-1 text-xs font-mono text-text-secondary break-all">{settings.public_key}</div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Peer Health */}
          {health && (health.total_peers > 0) && (
            <div className="bg-surface-card border border-border-default rounded-lg p-4">
              <h2 className="text-sm font-semibold text-text-primary mb-3">Peer Health</h2>
              <div className="flex gap-4">
                {[
                  { label: 'Healthy', count: health.healthy, color: 'bg-state-healthy' },
                  { label: 'Warning', count: health.warning, color: 'bg-state-warning' },
                  { label: 'Critical', count: health.critical, color: 'bg-state-error' },
                  { label: 'Lost', count: health.lost, color: 'bg-state-inactive' },
                ].map(({ label, count, color }) => (
                  <div key={label} className="flex items-center gap-2">
                    <span className={`w-2.5 h-2.5 rounded-full ${color}`} />
                    <span className="text-sm text-text-secondary">{label}: {count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Topology Canvas */}
          {topology && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-text-primary">
                  Topology: {topology.topology_name}
                  <span className="ml-2 text-text-muted font-mono text-xs">
                    v{topology.version} ({topology.version_hash.slice(0, 8)})
                  </span>
                </h2>
                {topology.deployed_at && (
                  <span className="text-xs text-state-healthy">
                    Deployed {new Date(topology.deployed_at).toLocaleDateString()}
                  </span>
                )}
              </div>

              {/* Nodes */}
              <div>
                <h3 className="text-[10px] font-semibold text-text-muted tracking-widest uppercase mb-2">
                  Nodes ({topology.nodes.length})
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {topology.nodes.map((node) => (
                    <NodeCard key={node.node_id} node={node} />
                  ))}
                </div>
              </div>

              {/* Edges */}
              {topology.edges.length > 0 && (
                <div>
                  <h3 className="text-[10px] font-semibold text-text-muted tracking-widest uppercase mb-2">
                    Edges ({topology.edges.length})
                  </h3>
                  <div className="space-y-2">
                    {topology.edges.map((edge) => (
                      <EdgeRow key={edge.edge_id} edge={edge} nodes={topology.nodes} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {!topology && !topoLoading && (
            <div className="bg-surface-card border border-border-default rounded-lg p-6 text-center">
              <p className="text-text-secondary text-sm">
                No active topology. Use the Graph Builder to create one.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
