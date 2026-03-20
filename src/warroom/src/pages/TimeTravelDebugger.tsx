import { useState, useCallback, useMemo } from 'react'
import { usePageTitle } from '@/hooks/usePageTitle'
import { usePolling } from '@/hooks/usePolling'
import {
  fetchTimeTravelStatus,
  fetchQuestReceipts,
  fetchReceiptSnapshot,
  createInspection,
  createReplay,
  createFork,
  type TimeTravelStatus,
  type ReceiptNode,
  type StateSnapshot,
  type QuestReceiptsResponse,
} from '@/api/timetravel'

// ── Status Badge ────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    success: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    failure: 'bg-red-500/20 text-red-400 border-red-500/30',
    pending: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    cancelled: 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono border ${colors[status] || colors.pending}`}>
      {status}
    </span>
  )
}

function TierBadge({ tier }: { tier: number }) {
  const colors = ['text-zinc-400', 'text-blue-400', 'text-amber-400', 'text-red-400']
  return <span className={`font-mono text-xs ${colors[tier] || colors[0]}`}>T{tier}</span>
}

// ── DAG Navigator ───────────────────────────────────────────

interface DagNodeProps {
  node: ReceiptNode
  x: number
  y: number
  selected: boolean
  onClick: () => void
}

function DagNode({ node, x, y, selected, onClick }: DagNodeProps) {
  const statusColors: Record<string, string> = {
    success: '#10b981',
    failure: '#ef4444',
    pending: '#f59e0b',
    cancelled: '#71717a',
  }
  const fill = statusColors[node.status] || '#71717a'
  const r = selected ? 14 : 10

  return (
    <g onClick={onClick} className="cursor-pointer">
      {selected && (
        <circle cx={x} cy={y} r={r + 4} fill="none" stroke="#818cf8" strokeWidth={2} opacity={0.6} />
      )}
      <circle cx={x} cy={y} r={r} fill={fill} stroke={selected ? '#818cf8' : '#27272a'} strokeWidth={2} />
      <title>{`${node.action_type} — ${node.action_name}\n${node.status} | T${node.tier}`}</title>
    </g>
  )
}

function DagNavigator({
  receipts,
  selectedId,
  onSelect,
}: {
  receipts: ReceiptNode[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  // Layout: assign x,y positions. Simple left-to-right timeline.
  const nodeSpacing = 60
  const rowHeight = 60
  const padding = 30

  // Build parent→children map for edge drawing
  const idIndex = useMemo(() => {
    const map = new Map<string, number>()
    receipts.forEach((r, i) => map.set(r.id, i))
    return map
  }, [receipts])

  const width = receipts.length * nodeSpacing + padding * 2
  const height = rowHeight + padding * 2

  return (
    <div className="overflow-x-auto border border-zinc-700 rounded-lg bg-zinc-900/50">
      <svg width={Math.max(width, 300)} height={height} className="min-w-full">
        {/* Edges */}
        {receipts.map((node, i) => {
          if (!node.parent_id) return null
          const parentIdx = idIndex.get(node.parent_id)
          if (parentIdx === undefined) return null
          const x1 = padding + parentIdx * nodeSpacing
          const x2 = padding + i * nodeSpacing
          const y = padding + rowHeight / 2
          return (
            <line
              key={`edge-${node.id}`}
              x1={x1}
              y1={y}
              x2={x2}
              y2={y}
              stroke="#3f3f46"
              strokeWidth={1.5}
              markerEnd="url(#arrow)"
            />
          )
        })}
        {/* Arrow marker */}
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX={8} refY={5} markerWidth={6} markerHeight={6} orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#3f3f46" />
          </marker>
        </defs>
        {/* Nodes */}
        {receipts.map((node, i) => (
          <DagNode
            key={node.id}
            node={node}
            x={padding + i * nodeSpacing}
            y={padding + rowHeight / 2}
            selected={node.id === selectedId}
            onClick={() => onSelect(node.id)}
          />
        ))}
        {/* Labels under nodes */}
        {receipts.map((node, i) => (
          <text
            key={`label-${node.id}`}
            x={padding + i * nodeSpacing}
            y={padding + rowHeight / 2 + 22}
            textAnchor="middle"
            fill="#a1a1aa"
            fontSize={9}
            fontFamily="monospace"
          >
            {node.action_type.replace(/^(quest_|time_travel_|t3_fork_|fork_)/, '').slice(0, 8)}
          </text>
        ))}
      </svg>
    </div>
  )
}

// ── State Inspector ─────────────────────────────────────────

function StateInspector({ snapshot }: { snapshot: StateSnapshot | null }) {
  if (!snapshot) {
    return (
      <div className="text-zinc-500 text-sm p-4 border border-zinc-700 rounded-lg">
        Select a receipt node to inspect its governance state.
      </div>
    )
  }

  return (
    <div className="border border-zinc-700 rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-semibold text-zinc-300 uppercase tracking-wider">State Inspector</h3>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div>
          <span className="text-zinc-500 block">Soul Version</span>
          <span className="text-zinc-200 font-mono">{snapshot.soul_version || '—'}</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Trust Tier</span>
          <span className="text-zinc-200 font-mono">T{snapshot.trust_tier ?? '?'}</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Receipt Chain</span>
          <span className="text-zinc-200 font-mono">{snapshot.receipt_chain_length} receipts</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Timestamp</span>
          <span className="text-zinc-200 font-mono text-xs">{snapshot.timestamp}</span>
        </div>
      </div>

      {/* Kill Switches */}
      {Object.keys(snapshot.kill_switches).length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-zinc-400 uppercase mb-1">Kill Switches Active</h4>
          <div className="flex flex-wrap gap-1">
            {Object.entries(snapshot.kill_switches)
              .filter(([, active]) => active)
              .map(([name]) => (
                <span key={name} className="px-2 py-0.5 bg-red-500/20 text-red-400 text-xs rounded font-mono border border-red-500/30">
                  {name}
                </span>
              ))}
            {Object.values(snapshot.kill_switches).every((v) => !v) && (
              <span className="text-zinc-500 text-xs">None active</span>
            )}
          </div>
        </div>
      )}

      {/* Cost Data */}
      {snapshot.cost_data && Object.keys(snapshot.cost_data).length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-zinc-400 uppercase mb-1">Cost Data</h4>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <span className="text-zinc-500">Tokens</span>
              <span className="block text-zinc-200 font-mono">
                {(snapshot.cost_data.total_tokens as number)?.toLocaleString() ?? '—'}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Receipts</span>
              <span className="block text-zinc-200 font-mono">
                {(snapshot.cost_data.total_receipts as number)?.toLocaleString() ?? '—'}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Duration</span>
              <span className="block text-zinc-200 font-mono">
                {(snapshot.cost_data.total_duration_ms as number)?.toLocaleString() ?? '—'}ms
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Metadata */}
      <div>
        <h4 className="text-xs font-semibold text-zinc-400 uppercase mb-1">Governance Context</h4>
        <div className="flex flex-wrap gap-2 text-xs">
          {snapshot.metadata?.soul_constraints_active !== undefined && (
            <span className={`px-2 py-0.5 rounded font-mono border ${snapshot.metadata.soul_constraints_active ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 'bg-zinc-600/20 text-zinc-400 border-zinc-600/30'}`}>
              Soul: {snapshot.metadata.soul_constraints_active ? 'ON' : 'OFF'}
            </span>
          )}
          {snapshot.metadata?.apl_rules_active !== undefined && (
            <span className={`px-2 py-0.5 rounded font-mono border ${snapshot.metadata.apl_rules_active ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 'bg-zinc-600/20 text-zinc-400 border-zinc-600/30'}`}>
              APL: {snapshot.metadata.apl_rules_active ? 'ON' : 'OFF'}
            </span>
          )}
          {snapshot.metadata?.trust_ledger_active !== undefined && (
            <span className={`px-2 py-0.5 rounded font-mono border ${snapshot.metadata.trust_ledger_active ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 'bg-zinc-600/20 text-zinc-400 border-zinc-600/30'}`}>
              Trust: {snapshot.metadata.trust_ledger_active ? 'ON' : 'OFF'}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Receipt Detail Panel ────────────────────────────────────

function ReceiptDetail({ receipt }: { receipt: ReceiptNode | null }) {
  if (!receipt) return null

  return (
    <div className="border border-zinc-700 rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-300 uppercase tracking-wider">Receipt Detail</h3>
        <div className="flex items-center gap-2">
          <TierBadge tier={receipt.tier} />
          <StatusBadge status={receipt.status} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <span className="text-zinc-500 block">ID</span>
          <span className="text-zinc-200 font-mono text-xs break-all">{receipt.id}</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Action</span>
          <span className="text-zinc-200 font-mono">{receipt.action_type}</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Name</span>
          <span className="text-zinc-200">{receipt.action_name}</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Duration</span>
          <span className="text-zinc-200 font-mono">{receipt.duration_ms ?? '—'}ms</span>
        </div>
      </div>

      {Object.keys(receipt.inputs).length > 0 && (
        <details className="text-xs">
          <summary className="text-zinc-400 cursor-pointer hover:text-zinc-300">Inputs</summary>
          <pre className="mt-1 p-2 bg-zinc-800 rounded text-zinc-300 overflow-x-auto max-h-40">
            {JSON.stringify(receipt.inputs, null, 2)}
          </pre>
        </details>
      )}

      {Object.keys(receipt.outputs).length > 0 && (
        <details className="text-xs">
          <summary className="text-zinc-400 cursor-pointer hover:text-zinc-300">Outputs</summary>
          <pre className="mt-1 p-2 bg-zinc-800 rounded text-zinc-300 overflow-x-auto max-h-40">
            {JSON.stringify(receipt.outputs, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}

// ── Fork Confirmation Modal ─────────────────────────────────

function ForkModal({
  questId,
  onClose,
  onFork,
  onReplay,
  status,
}: {
  questId: string
  onClose: () => void
  onFork: (mods: Record<string, unknown>) => void
  onReplay: () => void
  status: TimeTravelStatus | null
}) {
  const [mode, setMode] = useState<'replay' | 'fork'>('replay')
  const [modsText, setModsText] = useState('{}')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      if (mode === 'replay') {
        onReplay()
      } else {
        const mods = JSON.parse(modsText)
        onFork(mods)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invalid JSON')
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-zinc-800 border border-zinc-600 rounded-xl p-6 max-w-lg w-full mx-4 space-y-4" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-zinc-200">Time-Travel Operation</h2>
        <p className="text-sm text-zinc-400">Quest: <span className="font-mono text-zinc-300">{questId.slice(0, 12)}...</span></p>

        {status && !status.fork_allowed && (
          <div className="bg-amber-500/10 border border-amber-500/30 rounded p-3 text-sm text-amber-400">
            Fork/Replay is disabled in the current Soul. Enable fork_permissions.allow_fork to proceed.
          </div>
        )}

        <div className="flex gap-2">
          <button
            onClick={() => setMode('replay')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${mode === 'replay' ? 'bg-indigo-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}
          >
            Replay
          </button>
          <button
            onClick={() => setMode('fork')}
            className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${mode === 'fork' ? 'bg-indigo-600 text-white' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'}`}
          >
            Fork
          </button>
        </div>

        {mode === 'replay' && (
          <p className="text-sm text-zinc-400">
            Re-execute this quest unchanged under the current Soul. A new quest ID will be assigned.
            Requires T{status?.require_approval_tier ?? 3} approval.
          </p>
        )}

        {mode === 'fork' && (
          <div className="space-y-2">
            <p className="text-sm text-zinc-400">
              Fork this quest with modified inputs. Requires T{status?.require_approval_tier ?? 3} approval.
            </p>
            <label className="text-xs text-zinc-500 block">Modifications (JSON)</label>
            <textarea
              value={modsText}
              onChange={(e) => setModsText(e.target.value)}
              className="w-full h-24 bg-zinc-900 border border-zinc-600 rounded p-2 text-sm font-mono text-zinc-300 focus:outline-none focus:border-indigo-500"
              placeholder='{"inputs.query": "new prompt"}'
            />
          </div>
        )}

        {error && (
          <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2">{error}</div>
        )}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors">Cancel</button>
          <button
            onClick={handleSubmit}
            disabled={submitting || (status !== null && !status.fork_allowed)}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white text-sm rounded font-medium transition-colors"
          >
            {submitting ? 'Processing...' : mode === 'replay' ? 'Replay Quest' : 'Fork Quest'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────────

export function TimeTravelDebugger() {
  usePageTitle('Time-Travel Debugger')

  const [questId, setQuestId] = useState('')
  const [searchedQuestId, setSearchedQuestId] = useState<string | null>(null)
  const [questData, setQuestData] = useState<QuestReceiptsResponse | null>(null)
  const [selectedReceiptId, setSelectedReceiptId] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<StateSnapshot | null>(null)
  const [showForkModal, setShowForkModal] = useState(false)
  const [loadingQuest, setLoadingQuest] = useState(false)
  const [loadingSnapshot, setLoadingSnapshot] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resultMsg, setResultMsg] = useState<string | null>(null)

  const { data: status } = usePolling<TimeTravelStatus>({
    fetcher: fetchTimeTravelStatus,
    interval: 30_000,
  })

  const selectedReceipt = useMemo(() => {
    if (!questData || !selectedReceiptId) return null
    return questData.receipts.find((r) => r.id === selectedReceiptId) ?? null
  }, [questData, selectedReceiptId])

  const handleSearch = useCallback(async () => {
    if (!questId.trim()) return
    setLoadingQuest(true)
    setError(null)
    setQuestData(null)
    setSelectedReceiptId(null)
    setSnapshot(null)
    try {
      const data = await fetchQuestReceipts(questId.trim())
      setQuestData(data)
      setSearchedQuestId(questId.trim())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch quest')
    } finally {
      setLoadingQuest(false)
    }
  }, [questId])

  const handleSelectReceipt = useCallback(async (id: string) => {
    setSelectedReceiptId(id)
    setLoadingSnapshot(true)
    setSnapshot(null)
    try {
      // Trigger inspection (emits receipt) and fetch snapshot
      await createInspection(id)
      const snap = await fetchReceiptSnapshot(id)
      setSnapshot(snap)
    } catch (e) {
      // Snapshot may fail but selection still works
      console.warn('Snapshot fetch failed:', e)
    } finally {
      setLoadingSnapshot(false)
    }
  }, [])

  const handleReplay = useCallback(async () => {
    if (!searchedQuestId) return
    setShowForkModal(false)
    setResultMsg(null)
    try {
      const result = await createReplay(searchedQuestId)
      if (result.success) {
        setResultMsg(`Replay created: ${result.replay_quest_id}`)
      } else {
        setError(result.error || 'Replay failed')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Replay failed')
    }
  }, [searchedQuestId])

  const handleFork = useCallback(async (mods: Record<string, unknown>) => {
    if (!searchedQuestId) return
    setShowForkModal(false)
    setResultMsg(null)
    try {
      const result = await createFork(searchedQuestId, mods)
      if (result.success) {
        setResultMsg(`Fork created: ${result.fork_quest_id}`)
      } else {
        setError(result.error || 'Fork failed')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Fork failed')
    }
  }, [searchedQuestId])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Time-Travel Debugger</h1>
          <p className="text-sm text-zinc-400 mt-1">Inspect, replay, and fork past quest executions under governed control.</p>
        </div>
        <div className="flex items-center gap-3">
          {status && (
            <>
              <span className={`px-2 py-1 rounded text-xs font-mono border ${status.enabled ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30' : 'bg-zinc-600/20 text-zinc-400 border-zinc-600/30'}`}>
                {status.enabled ? 'ENABLED' : 'DISABLED'}
              </span>
              {status.fork_allowed && (
                <span className="px-2 py-1 rounded text-xs font-mono border bg-indigo-500/20 text-indigo-400 border-indigo-500/30">
                  FORK: T{status.require_approval_tier}
                </span>
              )}
            </>
          )}
        </div>
      </div>

      {/* Quest Search */}
      <div className="flex gap-2">
        <input
          type="text"
          value={questId}
          onChange={(e) => setQuestId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="Enter Quest ID..."
          className="flex-1 bg-zinc-800 border border-zinc-600 rounded-lg px-4 py-2 text-sm font-mono text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
        />
        <button
          onClick={handleSearch}
          disabled={loadingQuest || !questId.trim()}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white text-sm rounded-lg font-medium transition-colors"
        >
          {loadingQuest ? 'Loading...' : 'Load Quest'}
        </button>
        {searchedQuestId && (
          <button
            onClick={() => setShowForkModal(true)}
            disabled={!status?.enabled}
            className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white text-sm rounded-lg font-medium transition-colors"
          >
            Fork / Replay
          </button>
        )}
      </div>

      {/* Messages */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm text-red-400 flex justify-between items-center">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300">x</button>
        </div>
      )}
      {resultMsg && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-3 text-sm text-emerald-400 flex justify-between items-center">
          <span>{resultMsg}</span>
          <button onClick={() => setResultMsg(null)} className="text-emerald-400 hover:text-emerald-300">x</button>
        </div>
      )}

      {/* DAG Navigator */}
      {questData && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wider">
              Receipt DAG — {questData.receipt_count} receipts
            </h2>
            <span className="text-xs text-zinc-500 font-mono">{searchedQuestId?.slice(0, 12)}...</span>
          </div>
          <DagNavigator
            receipts={questData.receipts}
            selectedId={selectedReceiptId}
            onSelect={handleSelectReceipt}
          />
        </div>
      )}

      {/* Detail Panels */}
      {questData && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ReceiptDetail receipt={selectedReceipt} />
          <div>
            {loadingSnapshot ? (
              <div className="border border-zinc-700 rounded-lg p-4 text-zinc-500 text-sm animate-pulse">
                Loading state snapshot...
              </div>
            ) : (
              <StateInspector snapshot={snapshot} />
            )}
          </div>
        </div>
      )}

      {/* Empty State */}
      {!questData && !loadingQuest && !error && (
        <div className="text-center py-16 text-zinc-500">
          <div className="text-4xl mb-3 opacity-30">&#8634;</div>
          <p className="text-lg">Enter a Quest ID to explore its receipt DAG</p>
          <p className="text-sm mt-1">You can find Quest IDs in the Receipt Explorer</p>
        </div>
      )}

      {/* Fork Modal */}
      {showForkModal && searchedQuestId && (
        <ForkModal
          questId={searchedQuestId}
          onClose={() => setShowForkModal(false)}
          onFork={handleFork}
          onReplay={handleReplay}
          status={status}
        />
      )}
    </div>
  )
}
