import { useState } from 'react'
import { usePolling } from '@/hooks/usePolling'
import {
  fetchActiveTopology,
  createTopology,
  addNode,
  removeNode,
  addEdge,
  removeEdge,
  validateTopology,
  acknowledgeYellow,
  checkDeploymentGate,
  deployTopology,
  saveTopologyVersion,
  fetchTopologyVersions,
  fetchFederationSettings,
  type TopologyDocument,
  type TopologyVersion,
  type DeploymentGateResult,
  type FederationSettings,
} from '@/api/federation'
import { ConfirmDialog } from '@/components/ConfirmDialog'

// ── Sub-components ───────────────────────────────────────────

function EdgeStateIndicator({ state }: { state: string }) {
  const colors: Record<string, string> = {
    green: 'bg-state-healthy',
    yellow: 'bg-state-warning',
    red: 'bg-state-error',
    unknown: 'bg-state-inactive',
  }
  return <span className={`w-2.5 h-2.5 rounded-full inline-block ${colors[state] || colors.unknown}`} />
}

function AddNodeForm({
  onAdd,
  localSettings,
}: {
  onAdd: () => void
  localSettings: FederationSettings | null
}) {
  const [nodeId, setNodeId] = useState('')
  const [name, setName] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [pubKey, setPubKey] = useState('')
  const [fingerprint, setFingerprint] = useState('')
  const [role, setRole] = useState('peer')
  const [soulMode, setSoulMode] = useState('custom')
  const [soulVersion, setSoulVersion] = useState('')
  const [soulHash, setSoulHash] = useState('')
  const [hiveEnabled, setHiveEnabled] = useState(false)
  const [hiveMaxAgents, setHiveMaxAgents] = useState(10)
  const [budgetCeiling, setBudgetCeiling] = useState(10.0)
  const [isLocal, setIsLocal] = useState(false)
  const [error, setError] = useState('')

  const applyLocalDefaults = (checked: boolean) => {
    setIsLocal(checked)
    if (!checked || !localSettings) return
    if (!endpoint.trim()) setEndpoint(localSettings.self_address || '')
    if (!pubKey.trim()) setPubKey(localSettings.public_key || '')
    if (!fingerprint.trim()) setFingerprint(localSettings.fingerprint || '')
    if (!name.trim()) setName('This Instance')
  }

  const handleSubmit = async () => {
    if (!nodeId.trim()) { setError('Node ID required'); return }
    if (!pubKey.trim()) { setError('Federation public key required'); return }
    try {
      await addNode({
        node_id: nodeId,
        instance_name: name || nodeId,
        endpoint,
        federation_identity_public_key: pubKey,
        fingerprint,
        instance_role: role,
        soul_source_mode: soulMode,
        soul_version: soulVersion,
        soul_version_hash: soulHash,
        hive_enabled: hiveEnabled,
        hive_max_agents: hiveMaxAgents,
        budget_daily_ceiling_usd: budgetCeiling,
        is_local: isLocal,
      })
      setNodeId(''); setName(''); setEndpoint(''); setPubKey(''); setFingerprint('')
      setSoulVersion(''); setSoulHash(''); setError('')
      onAdd()
    } catch (e: any) {
      setError(e.message || 'Failed to add node')
    }
  }

  return (
    <div className="bg-surface-card border border-border-default rounded-lg p-4 space-y-3">
      <h3 className="text-sm font-semibold text-text-primary">Add Node</h3>

      {/* Identity */}
      <div className="grid grid-cols-2 gap-2">
        <input
          placeholder="Node ID *"
          value={nodeId}
          onChange={e => setNodeId(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
        <input
          placeholder="Instance Name"
          value={name}
          onChange={e => setName(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
        <input
          placeholder="Endpoint URL"
          value={endpoint}
          onChange={e => setEndpoint(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary col-span-2"
        />
      </div>

      {/* Federation Identity */}
      <div className="grid grid-cols-2 gap-2">
        <input
          placeholder="Federation Public Key *"
          value={pubKey}
          onChange={e => setPubKey(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
        <input
          placeholder="Fingerprint"
          value={fingerprint}
          onChange={e => setFingerprint(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
      </div>

      {/* Soul Configuration */}
      <div className="grid grid-cols-3 gap-2">
        <select
          value={soulMode}
          onChange={e => setSoulMode(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        >
          <option value="custom">Soul: Custom</option>
          <option value="inherited">Soul: Inherited</option>
          <option value="linked">Soul: Linked</option>
        </select>
        <input
          placeholder="Soul Version (e.g. v1)"
          value={soulVersion}
          onChange={e => setSoulVersion(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
        <input
          placeholder="Soul Version Hash"
          value={soulHash}
          onChange={e => setSoulHash(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
      </div>

      {/* Role, HIVE, Budget, Local */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          value={role}
          onChange={e => setRole(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        >
          <option value="root">Root</option>
          <option value="child">Child</option>
          <option value="peer">Peer</option>
          <option value="leaf">Leaf</option>
        </select>
        <label className="flex items-center gap-1.5 text-sm text-text-secondary">
          <input type="checkbox" checked={hiveEnabled} onChange={e => setHiveEnabled(e.target.checked)} />
          HIVE
        </label>
        {hiveEnabled && (
          <input
            type="number"
            min={1}
            max={100}
            value={hiveMaxAgents}
            onChange={e => setHiveMaxAgents(parseInt(e.target.value) || 10)}
            className="w-20 px-2 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
            title="Max agents"
          />
        )}
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-text-muted">Budget $</span>
          <input
            type="number"
            min={0.01}
            step={0.5}
            value={budgetCeiling}
            onChange={e => setBudgetCeiling(parseFloat(e.target.value) || 10.0)}
            className="w-20 px-2 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
            title="Daily ceiling USD"
          />
          <span className="text-xs text-text-muted">/day</span>
        </div>
        <label className="flex items-center gap-1.5 text-sm text-text-secondary">
          <input type="checkbox" checked={isLocal} onChange={e => applyLocalDefaults(e.target.checked)} />
          Local
        </label>
        <button
          onClick={handleSubmit}
          className="ml-auto px-4 py-1.5 text-sm font-medium bg-accent-primary text-white rounded hover:bg-accent-primary/80"
        >
          Add Node
        </button>
      </div>
      {isLocal && localSettings && !localSettings.self_address && (
        <p className="text-xs text-state-warning">
          This instance does not have a federation self address yet. Set it on Federation Overview before deploying this node.
        </p>
      )}
      {error && <p className="text-xs text-state-error">{error}</p>}
    </div>
  )
}

function AddEdgeForm({ nodes, onAdd }: { nodes: TopologyDocument['nodes']; onAdd: () => void }) {
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [relType, setRelType] = useState('federated_handoff')
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!source || !target) { setError('Select source and target'); return }
    try {
      await addEdge({ source_node_id: source, target_node_id: target, relationship_type: relType })
      setSource(''); setTarget(''); setError('')
      onAdd()
    } catch (e: any) {
      setError(e.message || 'Failed to add edge')
    }
  }

  return (
    <div className="bg-surface-card border border-border-default rounded-lg p-4 space-y-3">
      <h3 className="text-sm font-semibold text-text-primary">Add Edge</h3>
      <div className="flex items-center gap-2">
        <select
          value={source}
          onChange={e => setSource(e.target.value)}
          className="flex-1 px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        >
          <option value="">Source node...</option>
          {nodes.map(n => <option key={n.node_id} value={n.node_id}>{n.instance_name || n.node_id}</option>)}
        </select>
        <span className="text-text-muted">→</span>
        <select
          value={target}
          onChange={e => setTarget(e.target.value)}
          className="flex-1 px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        >
          <option value="">Target node...</option>
          {nodes.map(n => <option key={n.node_id} value={n.node_id}>{n.instance_name || n.node_id}</option>)}
        </select>
        <select
          value={relType}
          onChange={e => setRelType(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        >
          <option value="federated_handoff">Federated Handoff</option>
          <option value="hierarchical_parent_child">Hierarchical</option>
        </select>
        <button
          onClick={handleSubmit}
          className="px-4 py-1.5 text-sm font-medium bg-accent-primary text-white rounded hover:bg-accent-primary/80"
        >
          Add Edge
        </button>
      </div>
      {error && <p className="text-xs text-state-error">{error}</p>}
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────

export function GraphBuilder() {
  const { data: topology, refetch } = usePolling<TopologyDocument>({
    fetcher: fetchActiveTopology,
    interval: 30000,
  })
  const { data: federationSettings } = usePolling<FederationSettings>({
    fetcher: fetchFederationSettings,
    interval: 15000,
  })

  const [gateResult, setGateResult] = useState<DeploymentGateResult | null>(null)
  const [versions, setVersions] = useState<TopologyVersion[]>([])
  const [showVersions, setShowVersions] = useState(false)
  const [deployConfirm, setDeployConfirm] = useState(false)
  const [message, setMessage] = useState('')

  const showMsg = (msg: string) => {
    setMessage(msg)
    setTimeout(() => setMessage(''), 3000)
  }

  const handleCreate = async () => {
    try {
      await createTopology('New Federation Topology')
      refetch()
      showMsg('Topology created')
    } catch { showMsg('Failed to create topology') }
  }

  const handleValidate = async () => {
    try {
      const result = await validateTopology()
      refetch()
      showMsg(`Validated ${result.edge_count} edges`)
    } catch { showMsg('Validation failed') }
  }

  const handleGateCheck = async () => {
    try {
      const result = await checkDeploymentGate()
      setGateResult(result)
    } catch { showMsg('Gate check failed') }
  }

  const handleDeploy = async () => {
    try {
      const result = await deployTopology()
      setDeployConfirm(false)
      setGateResult(null)
      refetch()
      showMsg(`Deployed v${result.version}`)
    } catch (e: any) {
      showMsg(e.body?.detail || 'Deploy failed')
    }
  }

  const handleSaveVersion = async () => {
    try {
      const result = await saveTopologyVersion()
      showMsg(`Saved version v${result.version}`)
    } catch { showMsg('Save failed') }
  }

  const handleLoadVersions = async () => {
    try {
      const result = await fetchTopologyVersions()
      setVersions(result.versions)
      setShowVersions(true)
    } catch { showMsg('Failed to load versions') }
  }

  const handleRemoveNode = async (nodeId: string) => {
    try {
      await removeNode(nodeId)
      refetch()
    } catch { showMsg('Failed to remove node') }
  }

  const handleRemoveEdge = async (edgeId: string) => {
    try {
      await removeEdge(edgeId)
      refetch()
    } catch { showMsg('Failed to remove edge') }
  }

  const handleAcknowledge = async (edgeId: string) => {
    try {
      await acknowledgeYellow(edgeId, 'operator', 'Reviewed and accepted')
      refetch()
      showMsg('Edge acknowledged')
    } catch { showMsg('Acknowledge failed') }
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Graph Builder</h1>
          <p className="text-sm text-text-secondary mt-0.5">Federation topology editor</p>
        </div>
        <div className="flex items-center gap-2">
          {topology && (
            <>
              <button onClick={handleSaveVersion}
                className="px-3 py-1.5 text-xs font-medium bg-surface-card border border-border-default rounded text-text-secondary hover:text-text-primary">
                Save Version
              </button>
              <button onClick={handleLoadVersions}
                className="px-3 py-1.5 text-xs font-medium bg-surface-card border border-border-default rounded text-text-secondary hover:text-text-primary">
                History
              </button>
              <button onClick={handleValidate}
                className="px-3 py-1.5 text-xs font-medium bg-accent-secondary/20 text-accent-secondary rounded hover:bg-accent-secondary/30">
                Validate All
              </button>
              <button onClick={handleGateCheck}
                className="px-3 py-1.5 text-xs font-medium bg-accent-primary/20 text-accent-primary rounded hover:bg-accent-primary/30">
                Check Deploy Gate
              </button>
            </>
          )}
          {!topology && (
            <button onClick={handleCreate}
              className="px-4 py-1.5 text-sm font-medium bg-accent-primary text-white rounded hover:bg-accent-primary/80">
              New Topology
            </button>
          )}
        </div>
      </div>

      {/* Status message */}
      {message && (
        <div className="bg-surface-card-elevated border border-border-default rounded px-4 py-2 text-sm text-text-secondary">
          {message}
        </div>
      )}

      {/* Gate Result */}
      {gateResult && (
        <div className={`border rounded-lg p-4 ${
          gateResult.deployable ? 'border-state-healthy bg-state-healthy/5' : 'border-state-error bg-state-error/5'
        }`}>
          <div className="flex items-center justify-between">
            <div>
              <span className={`text-sm font-semibold ${gateResult.deployable ? 'text-state-healthy' : 'text-state-error'}`}>
                {gateResult.report}
              </span>
              {gateResult.blocking_edges.length > 0 && (
                <p className="text-xs text-text-muted mt-1">
                  Blocking: {gateResult.blocking_edges.join(', ')}
                </p>
              )}
              {gateResult.unacknowledged_yellows.length > 0 && (
                <p className="text-xs text-text-muted mt-1">
                  Need acknowledgment: {gateResult.unacknowledged_yellows.join(', ')}
                </p>
              )}
            </div>
            {gateResult.deployable && (
              <button onClick={() => setDeployConfirm(true)}
                className="px-4 py-1.5 text-sm font-medium bg-state-healthy text-white rounded hover:bg-state-healthy/80">
                Deploy
              </button>
            )}
          </div>
        </div>
      )}

      {/* Version History */}
      {showVersions && (
        <div className="bg-surface-card border border-border-default rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-text-primary">Version History</h3>
            <button onClick={() => setShowVersions(false)} className="text-xs text-text-muted hover:text-text-primary">Close</button>
          </div>
          {versions.length === 0 ? (
            <p className="text-sm text-text-muted">No saved versions</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] text-text-muted tracking-wider uppercase">
                  <th className="pb-2">Version</th>
                  <th className="pb-2">Hash</th>
                  <th className="pb-2">Nodes</th>
                  <th className="pb-2">Edges</th>
                  <th className="pb-2">Updated</th>
                  <th className="pb-2">Deployed</th>
                </tr>
              </thead>
              <tbody>
                {versions.map(v => (
                  <tr key={v.version} className="border-t border-border-default">
                    <td className="py-2 text-text-primary font-mono">v{v.version}</td>
                    <td className="py-2 text-text-muted font-mono">{v.version_hash.slice(0, 8)}</td>
                    <td className="py-2 text-text-secondary">{v.node_count}</td>
                    <td className="py-2 text-text-secondary">{v.edge_count}</td>
                    <td className="py-2 text-text-muted">{new Date(v.updated_at).toLocaleDateString()}</td>
                    <td className="py-2">{v.deployed_at
                      ? <span className="text-state-healthy">Yes</span>
                      : <span className="text-text-muted">No</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* No topology */}
      {!topology && (
        <div className="bg-surface-card border border-border-default rounded-lg p-12 text-center">
          <p className="text-text-secondary mb-4">No active topology</p>
          <button onClick={handleCreate}
            className="px-4 py-2 text-sm font-medium bg-accent-primary text-white rounded hover:bg-accent-primary/80">
            Create Topology
          </button>
        </div>
      )}

      {/* Topology Editor */}
      {topology && (
        <div className="space-y-4">
          {/* Node editor */}
          <AddNodeForm onAdd={refetch} localSettings={federationSettings} />

          {/* Existing nodes */}
          {topology.nodes.length > 0 && (
            <div>
              <h3 className="text-[10px] font-semibold text-text-muted tracking-widest uppercase mb-2">
                Nodes ({topology.nodes.length})
              </h3>
              <div className="space-y-2">
                {topology.nodes.map(node => (
                  <div key={node.node_id}
                    className="flex items-center justify-between bg-surface-card border border-border-default rounded-lg px-4 py-3">
                    <div className="flex items-center gap-3">
                      <span className={`w-2.5 h-2.5 rounded-full ${
                        node.connection_status === 'green' ? 'bg-state-healthy' : 'bg-state-inactive'
                      }`} />
                      <div>
                        <span className="text-sm text-text-primary font-medium">
                          {node.instance_name || node.node_id}
                        </span>
                        <span className="ml-2 text-xs text-text-muted">
                          {node.instance_role} {node.is_local && '(local)'}
                        </span>
                      </div>
                    </div>
                    <button onClick={() => handleRemoveNode(node.node_id)}
                      className="text-xs text-state-error/60 hover:text-state-error">
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Edge editor */}
          {topology.nodes.length >= 2 && (
            <AddEdgeForm nodes={topology.nodes} onAdd={refetch} />
          )}

          {/* Existing edges */}
          {topology.edges.length > 0 && (
            <div>
              <h3 className="text-[10px] font-semibold text-text-muted tracking-widest uppercase mb-2">
                Edges ({topology.edges.length})
              </h3>
              <div className="space-y-2">
                {topology.edges.map(edge => {
                  const srcName = topology.nodes.find(n => n.node_id === edge.source_node_id)?.instance_name || edge.source_node_id
                  const tgtName = topology.nodes.find(n => n.node_id === edge.target_node_id)?.instance_name || edge.target_node_id
                  return (
                    <div key={edge.edge_id}
                      className="flex items-center justify-between bg-surface-card border border-border-default rounded-lg px-4 py-3">
                      <div className="flex items-center gap-3">
                        <EdgeStateIndicator state={edge.compatibility_state} />
                        <span className="text-sm text-text-primary">{srcName} → {tgtName}</span>
                        <span className="text-xs text-text-muted">{edge.compatibility_state}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        {edge.compatibility_state === 'yellow' && edge.yellow_acknowledgments.length === 0 && (
                          <button onClick={() => handleAcknowledge(edge.edge_id)}
                            className="text-xs text-state-warning hover:text-state-warning/80">
                            Acknowledge
                          </button>
                        )}
                        <button onClick={() => handleRemoveEdge(edge.edge_id)}
                          className="text-xs text-state-error/60 hover:text-state-error">
                          Remove
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Deploy confirmation */}
      <ConfirmDialog
        open={deployConfirm}
        title="Deploy Topology"
        description="This will deploy the current topology to all federation instances. This action bumps the version."
        onConfirm={handleDeploy}
        onCancel={() => setDeployConfirm(false)}
      />
    </div>
  )
}
