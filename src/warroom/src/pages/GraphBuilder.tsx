import { useState, type ReactNode } from 'react'
import {
  CheckCircle2,
  ChevronDown,
  CircleDot,
  GitBranch,
  GitPullRequestArrow,
  History,
  Link2,
  Network,
  Plus,
  Rocket,
  Save,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { usePageTitle } from '@/hooks'
import { usePolling } from '@/hooks/usePolling'
import {
  fetchFederationStatus,
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
  type FederationStatus,
} from '@/api/federation'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { getErrorMessage } from '@/utils/errors'

type PanelTone = 'accent' | 'healthy' | 'warning' | 'error' | 'muted'

function toneClass(tone: PanelTone): string {
  return {
    accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
    healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
    warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
    error: 'border-state-error/30 bg-state-error/10 text-state-error',
    muted: 'border-border-default bg-surface-card text-text-muted',
  }[tone]
}

function readable(value: string): string {
  return value.replace(/_/g, ' ')
}

function EdgeStateIndicator({ state }: { state: string }) {
  const colors: Record<string, string> = {
    green: 'bg-state-healthy',
    yellow: 'bg-state-warning',
    red: 'bg-state-error',
    unknown: 'bg-state-inactive',
  }
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${colors[state] || colors.unknown}`} />
}

function SummaryTile({
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
  tone?: PanelTone
}) {
  return (
    <div className={`min-w-0 rounded-lg border p-4 ${toneClass(tone)}`}>
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

function ActionButton({
  children,
  onClick,
  tone = 'muted',
  disabled = false,
}: {
  children: ReactNode
  onClick: () => void
  tone?: PanelTone
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded border px-3 py-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${toneClass(tone)} hover:border-border-active`}
    >
      {children}
    </button>
  )
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
  const [advancedOpen, setAdvancedOpen] = useState(false)
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
      setNodeId('')
      setName('')
      setEndpoint('')
      setPubKey('')
      setFingerprint('')
      setSoulVersion('')
      setSoulHash('')
      setError('')
      onAdd()
    } catch (error) {
      setError(getErrorMessage(error, 'Failed to add node'))
    }
  }

  return (
    <section className="rounded-lg border border-border-default bg-surface-card p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Plus className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h3 className="text-sm font-semibold text-text-primary">Add Node</h3>
          </div>
          <p className="mt-1 text-xs text-text-secondary">Register a local or remote instance in the active topology.</p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm text-text-secondary">
          <input type="checkbox" checked={isLocal} onChange={(e) => applyLocalDefaults(e.target.checked)} className="accent-accent-primary" />
          This instance
        </label>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <label className="min-w-0">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Node ID</span>
          <input
            placeholder="node-root-1"
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          />
        </label>
        <label className="min-w-0">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Instance Name</span>
          <input
            placeholder="Production Lancelot"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          />
        </label>
        <label className="min-w-0 lg:col-span-2">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Endpoint URL</span>
          <input
            placeholder="https://lancelot.example.internal:8000"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          />
        </label>
        <label className="min-w-0">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Federation Public Key</span>
          <input
            placeholder="public key"
            value={pubKey}
            onChange={(e) => setPubKey(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          />
        </label>
        <label className="min-w-0">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Role</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          >
            <option value="root">Root</option>
            <option value="child">Child</option>
            <option value="peer">Peer</option>
            <option value="leaf">Leaf</option>
          </select>
        </label>
      </div>

      <div className="mt-4">
        <ToggleButton expanded={advancedOpen} onClick={() => setAdvancedOpen((value) => !value)}>
          {advancedOpen ? 'Hide Advanced Fields' : 'Show Advanced Fields'}
        </ToggleButton>
      </div>

      {advancedOpen && (
        <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Fingerprint</span>
            <input
              placeholder="fingerprint"
              value={fingerprint}
              onChange={(e) => setFingerprint(e.target.value)}
              className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
            />
          </label>
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Soul Mode</span>
            <select
              value={soulMode}
              onChange={(e) => setSoulMode(e.target.value)}
              className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
            >
              <option value="custom">Custom</option>
              <option value="inherited">Inherited</option>
              <option value="linked">Linked</option>
            </select>
          </label>
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Soul Version</span>
            <input
              placeholder="v1"
              value={soulVersion}
              onChange={(e) => setSoulVersion(e.target.value)}
              className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
            />
          </label>
          <label className="min-w-0 lg:col-span-2">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Soul Version Hash</span>
            <input
              placeholder="hash"
              value={soulHash}
              onChange={(e) => setSoulHash(e.target.value)}
              className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
            />
          </label>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex items-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-sm text-text-secondary">
              <input type="checkbox" checked={hiveEnabled} onChange={(e) => setHiveEnabled(e.target.checked)} className="accent-accent-primary" />
              HIVE enabled
            </label>
            {hiveEnabled && (
              <input
                type="number"
                min={1}
                max={100}
                value={hiveMaxAgents}
                onChange={(e) => setHiveMaxAgents(parseInt(e.target.value, 10) || 10)}
                className="rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
                title="Max agents"
              />
            )}
          </div>
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Daily Budget USD</span>
            <input
              type="number"
              min={0.01}
              step={0.5}
              value={budgetCeiling}
              onChange={(e) => setBudgetCeiling(parseFloat(e.target.value) || 10.0)}
              className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
              title="Daily ceiling USD"
            />
          </label>
        </div>
      )}

      {isLocal && localSettings && !localSettings.self_address && (
        <p className="mt-3 text-xs text-state-warning">
          This instance does not have a federation self address yet. Set it on Federation Overview before deploying this node.
        </p>
      )}
      {error && <p className="mt-3 text-xs text-state-error">{error}</p>}

      <div className="mt-4 flex justify-end">
        <ActionButton onClick={handleSubmit} tone="accent">
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          Add Node
        </ActionButton>
      </div>
    </section>
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
      setSource('')
      setTarget('')
      setError('')
      onAdd()
    } catch (error) {
      setError(getErrorMessage(error, 'Failed to add edge'))
    }
  }

  return (
    <section className="rounded-lg border border-border-default bg-surface-card p-4">
      <div className="flex items-center gap-2">
        <GitPullRequestArrow className="h-4 w-4 text-accent-primary" aria-hidden="true" />
        <h3 className="text-sm font-semibold text-text-primary">Add Edge</h3>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_minmax(180px,0.5fr)_auto] lg:items-end">
        <label className="min-w-0">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Source</span>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          >
            <option value="">Source node...</option>
            {nodes.map((n) => <option key={n.node_id} value={n.node_id}>{n.instance_name || n.node_id}</option>)}
          </select>
        </label>
        <span className="hidden pb-2 text-xs text-text-muted lg:block">to</span>
        <label className="min-w-0">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Target</span>
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          >
            <option value="">Target node...</option>
            {nodes.map((n) => <option key={n.node_id} value={n.node_id}>{n.instance_name || n.node_id}</option>)}
          </select>
        </label>
        <label className="min-w-0">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Relationship</span>
          <select
            value={relType}
            onChange={(e) => setRelType(e.target.value)}
            className="w-full rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
          >
            <option value="federated_handoff">Federated Handoff</option>
            <option value="hierarchical_parent_child">Hierarchical</option>
          </select>
        </label>
        <ActionButton onClick={handleSubmit} tone="accent">
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          Add Edge
        </ActionButton>
      </div>
      {error && <p className="mt-3 text-xs text-state-error">{error}</p>}
    </section>
  )
}

export function GraphBuilder() {
  usePageTitle('Graph Builder')
  const { data: federationStatus, loading: federationStatusLoading } = usePolling<FederationStatus>({
    fetcher: fetchFederationStatus,
    interval: 10000,
  })
  const federationEnabled = federationStatus?.enabled === true

  const { data: topology, refetch } = usePolling<TopologyDocument>({
    fetcher: fetchActiveTopology,
    interval: 30000,
    enabled: federationEnabled,
  })
  const { data: federationSettings } = usePolling<FederationSettings>({
    fetcher: fetchFederationSettings,
    interval: 15000,
    enabled: federationEnabled,
  })

  const [gateResult, setGateResult] = useState<DeploymentGateResult | null>(null)
  const [versions, setVersions] = useState<TopologyVersion[]>([])
  const [showVersions, setShowVersions] = useState(false)
  const [showNodeEditor, setShowNodeEditor] = useState(false)
  const [showEdgeEditor, setShowEdgeEditor] = useState(false)
  const [deployConfirm, setDeployConfirm] = useState(false)
  const [message, setMessage] = useState('')

  const showMsg = (msg: string) => {
    setMessage(msg)
    window.setTimeout(() => setMessage(''), 3000)
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
    } catch (error) {
      showMsg(getErrorMessage(error, 'Deploy failed'))
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

  if (!federationStatusLoading && !federationEnabled) {
    return (
      <div className="space-y-6">
        <section className="rounded-lg border border-border-default bg-surface-card px-5 py-5">
          <h1 className="text-2xl font-semibold text-text-primary">Graph Builder</h1>
          <p className="mt-2 text-sm text-text-secondary">
            Federation is not enabled. Set <code className="font-mono text-sm text-accent-primary">FEATURE_FEDERATION=true</code> to edit topology.
          </p>
        </section>
      </div>
    )
  }

  const redEdges = topology?.edges.filter((edge) => edge.compatibility_state === 'red').length ?? 0
  const yellowEdges = topology?.edges.filter((edge) => edge.compatibility_state === 'yellow').length ?? 0
  const unacknowledgedYellow = topology?.edges.filter((edge) => edge.compatibility_state === 'yellow' && edge.yellow_acknowledgments.length === 0).length ?? 0

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-default bg-surface-card px-5 py-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-accent-primary">
              <GitBranch className="h-3.5 w-3.5" aria-hidden="true" />
              Federation Topology
            </div>
            <h1 className="mt-2 text-2xl font-semibold leading-tight text-text-primary">Graph Builder</h1>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Build and validate the governed federation topology before deploying it across Lancelot instances.
            </p>
          </div>
          <div className={`rounded-lg border px-4 py-3 ${toneClass(redEdges > 0 ? 'error' : unacknowledgedYellow > 0 ? 'warning' : topology ? 'healthy' : 'muted')}`}>
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              <span className="text-sm font-semibold text-text-primary">
                {topology ? topology.topology_name : 'No Active Topology'}
              </span>
            </div>
            <div className="mt-2 text-xs leading-5 text-text-muted">
              {topology
                ? `v${topology.version}, ${topology.nodes.length} nodes, ${topology.edges.length} edges.`
                : 'Create a topology to start editing.'}
            </div>
          </div>
        </div>
      </section>

      {message && (
        <div className="rounded border border-border-default bg-surface-card-elevated px-4 py-2 text-sm text-text-secondary">
          {message}
        </div>
      )}

      {topology && (
        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryTile label="Nodes" value={topology.nodes.length} detail="Instances in this topology." icon={<Network className="h-4 w-4" />} tone="accent" />
          <SummaryTile label="Edges" value={topology.edges.length} detail="Governed handoff paths." icon={<Link2 className="h-4 w-4" />} tone="accent" />
          <SummaryTile label="Red Edges" value={redEdges} detail="Blocking compatibility failures." icon={<CircleDot className="h-4 w-4" />} tone={redEdges > 0 ? 'error' : 'healthy'} />
          <SummaryTile label="Yellow Edges" value={yellowEdges} detail={`${unacknowledgedYellow} need acknowledgment.`} icon={<CircleDot className="h-4 w-4" />} tone={unacknowledgedYellow > 0 ? 'warning' : yellowEdges > 0 ? 'accent' : 'healthy'} />
        </section>
      )}

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Topology Workflow</h2>
            <p className="mt-1 text-xs text-text-secondary">
              Edit nodes and edges, validate compatibility, then run the deployment gate before deploy.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {topology ? (
              <>
                <ActionButton onClick={() => setShowNodeEditor((value) => !value)} tone="muted">
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                  {showNodeEditor ? 'Hide Node Editor' : 'Add Node'}
                </ActionButton>
                <ActionButton onClick={() => setShowEdgeEditor((value) => !value)} tone="muted" disabled={topology.nodes.length < 2}>
                  <GitPullRequestArrow className="h-3.5 w-3.5" aria-hidden="true" />
                  {showEdgeEditor ? 'Hide Edge Editor' : 'Add Edge'}
                </ActionButton>
                <ActionButton onClick={handleSaveVersion}>
                  <Save className="h-3.5 w-3.5" aria-hidden="true" />
                  Save Version
                </ActionButton>
                <ActionButton onClick={handleLoadVersions}>
                  <History className="h-3.5 w-3.5" aria-hidden="true" />
                  History
                </ActionButton>
                <ActionButton onClick={handleValidate} tone="accent">
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                  Validate
                </ActionButton>
                <ActionButton onClick={handleGateCheck} tone="accent">
                  <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                  Deployment Gate
                </ActionButton>
              </>
            ) : (
              <ActionButton onClick={handleCreate} tone="accent">
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                New Topology
              </ActionButton>
            )}
          </div>
        </div>
      </section>

      {gateResult && (
        <section className={`rounded-lg border p-4 ${gateResult.deployable ? 'border-state-healthy bg-state-healthy/5' : 'border-state-error bg-state-error/5'}`}>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <span className={`text-sm font-semibold ${gateResult.deployable ? 'text-state-healthy' : 'text-state-error'}`}>
                {gateResult.report}
              </span>
              {gateResult.blocking_edges.length > 0 && (
                <p className="mt-1 text-xs text-text-muted">
                  Blocking: {gateResult.blocking_edges.join(', ')}
                </p>
              )}
              {gateResult.unacknowledged_yellows.length > 0 && (
                <p className="mt-1 text-xs text-text-muted">
                  Need acknowledgment: {gateResult.unacknowledged_yellows.join(', ')}
                </p>
              )}
            </div>
            {gateResult.deployable && (
              <ActionButton onClick={() => setDeployConfirm(true)} tone="healthy">
                <Rocket className="h-3.5 w-3.5" aria-hidden="true" />
                Deploy
              </ActionButton>
            )}
          </div>
        </section>
      )}

      {showVersions && (
        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-text-primary">Version History</h3>
            <button type="button" onClick={() => setShowVersions(false)} className="text-xs text-text-muted hover:text-text-primary">Close</button>
          </div>
          {versions.length === 0 ? (
            <p className="text-sm text-text-muted">No saved versions</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-text-muted">
                    <th className="pb-2">Version</th>
                    <th className="pb-2">Hash</th>
                    <th className="pb-2">Nodes</th>
                    <th className="pb-2">Edges</th>
                    <th className="pb-2">Updated</th>
                    <th className="pb-2">Deployed</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((version) => (
                    <tr key={version.version} className="border-t border-border-default">
                      <td className="py-2 font-mono text-text-primary">v{version.version}</td>
                      <td className="py-2 font-mono text-text-muted">{version.version_hash.slice(0, 8)}</td>
                      <td className="py-2 text-text-secondary">{version.node_count}</td>
                      <td className="py-2 text-text-secondary">{version.edge_count}</td>
                      <td className="py-2 text-text-muted">{new Date(version.updated_at).toLocaleDateString()}</td>
                      <td className="py-2">{version.deployed_at ? <span className="text-state-healthy">Yes</span> : <span className="text-text-muted">No</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {!topology && (
        <section className="rounded-lg border border-border-default bg-surface-card p-8 text-center">
          <p className="mb-4 text-text-secondary">No active topology</p>
          <ActionButton onClick={handleCreate} tone="accent">
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            Create Topology
          </ActionButton>
        </section>
      )}

      {topology && (
        <div className="space-y-4">
          {showNodeEditor && <AddNodeForm onAdd={refetch} localSettings={federationSettings} />}
          {showEdgeEditor && topology.nodes.length >= 2 && <AddEdgeForm nodes={topology.nodes} onAdd={refetch} />}

          <section className="rounded-lg border border-border-default bg-surface-card p-4">
            <div className="mb-3 flex items-center gap-2">
              <Network className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h3 className="text-sm font-semibold text-text-primary">Nodes</h3>
              <span className="text-xs text-text-muted">({topology.nodes.length})</span>
            </div>
            {topology.nodes.length === 0 ? (
              <p className="text-sm text-text-muted">No nodes added yet.</p>
            ) : (
              <div className="space-y-2">
                {topology.nodes.map((node) => (
                  <div key={node.node_id} className="flex flex-col gap-3 rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex min-w-0 items-center gap-3">
                      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${node.connection_status === 'green' ? 'bg-state-healthy' : 'bg-state-inactive'}`} />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-text-primary">{node.instance_name || node.node_id}</div>
                        <div className="truncate text-xs text-text-muted">{node.instance_role} {node.is_local && '(local)'} / {node.node_id}</div>
                      </div>
                    </div>
                    <button type="button" onClick={() => handleRemoveNode(node.node_id)} className="inline-flex items-center gap-1 text-xs text-state-error/70 hover:text-state-error">
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="rounded-lg border border-border-default bg-surface-card p-4">
            <div className="mb-3 flex items-center gap-2">
              <Link2 className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h3 className="text-sm font-semibold text-text-primary">Edges</h3>
              <span className="text-xs text-text-muted">({topology.edges.length})</span>
            </div>
            {topology.edges.length === 0 ? (
              <p className="text-sm text-text-muted">No edges added yet.</p>
            ) : (
              <div className="space-y-2">
                {topology.edges.map((edge) => {
                  const srcName = topology.nodes.find((n) => n.node_id === edge.source_node_id)?.instance_name || edge.source_node_id
                  const tgtName = topology.nodes.find((n) => n.node_id === edge.target_node_id)?.instance_name || edge.target_node_id
                  return (
                    <div key={edge.edge_id} className="flex flex-col gap-3 rounded-lg border border-border-default bg-surface-card-elevated px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex min-w-0 items-center gap-3">
                        <EdgeStateIndicator state={edge.compatibility_state} />
                        <div className="min-w-0">
                          <div className="truncate text-sm text-text-primary">{srcName} to {tgtName}</div>
                          <div className="truncate text-xs text-text-muted">{readable(edge.relationship_type)} / {edge.compatibility_state}</div>
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {edge.compatibility_state === 'yellow' && edge.yellow_acknowledgments.length === 0 && (
                          <button type="button" onClick={() => handleAcknowledge(edge.edge_id)} className="inline-flex items-center gap-1 text-xs text-state-warning hover:text-state-warning/80">
                            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                            Acknowledge
                          </button>
                        )}
                        <button type="button" onClick={() => handleRemoveEdge(edge.edge_id)} className="inline-flex items-center gap-1 text-xs text-state-error/70 hover:text-state-error">
                          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                          Remove
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </section>
        </div>
      )}

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
