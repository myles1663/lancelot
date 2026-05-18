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
import { emitWarRoomNotification } from '@/utils/notifications'

// ── Status Badge ────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    success: 'bg-state-healthy/15 text-state-healthy border-state-healthy/30',
    failure: 'bg-state-error/15 text-state-error border-state-error/30',
    pending: 'bg-state-degraded/15 text-state-degraded border-state-degraded/30',
    cancelled: 'bg-surface-input text-text-muted border-border-default',
  }
  return (
    <span className={`rounded border px-2 py-0.5 text-xs font-mono ${colors[status] || colors.pending}`}>
      {status}
    </span>
  )
}

function TierBadge({ tier }: { tier: number }) {
  const colors = ['text-text-muted', 'text-accent-primary', 'text-state-degraded', 'text-state-error']
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
    <div className="overflow-x-auto rounded-lg border border-border-default bg-surface-card-elevated/60">
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
      <div className="rounded-lg border border-border-default bg-surface-card p-4 text-sm text-text-muted">
        Select a receipt node to inspect its governance state.
      </div>
    )
  }

  return (
    <div className="space-y-4 rounded-lg border border-border-default bg-surface-card p-4">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-text-secondary">State Inspector</h3>

      <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <span className="block text-text-muted">Soul Version</span>
          <span className="font-mono text-text-primary">{snapshot.soul_version || '-'}</span>
        </div>
        <div>
          <span className="block text-text-muted">Trust Tier</span>
          <span className="font-mono text-text-primary">T{snapshot.trust_tier ?? '?'}</span>
        </div>
        <div>
          <span className="block text-text-muted">Receipt Chain</span>
          <span className="font-mono text-text-primary">{snapshot.receipt_chain_length} receipts</span>
        </div>
        <div>
          <span className="block text-text-muted">Timestamp</span>
          <span className="break-all font-mono text-xs text-text-primary">{snapshot.timestamp}</span>
        </div>
      </div>

      {/* Kill Switches */}
      {Object.keys(snapshot.kill_switches).length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase text-text-secondary">Kill Switches Active</h4>
          <div className="flex flex-wrap gap-1">
            {Object.entries(snapshot.kill_switches)
              .filter(([, active]) => active)
              .map(([name]) => (
                <span key={name} className="rounded border border-state-error/30 bg-state-error/15 px-2 py-0.5 text-xs font-mono text-state-error">
                  {name}
                </span>
              ))}
            {Object.values(snapshot.kill_switches).every((v) => !v) && (
              <span className="text-xs text-text-muted">None active</span>
            )}
          </div>
        </div>
      )}

      {/* Cost Data */}
      {snapshot.cost_data && Object.keys(snapshot.cost_data).length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase text-text-secondary">Cost Data</h4>
          <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
            <div>
              <span className="text-text-muted">Tokens</span>
              <span className="block font-mono text-text-primary">
                {(snapshot.cost_data.total_tokens as number)?.toLocaleString() ?? '—'}
              </span>
            </div>
            <div>
              <span className="text-text-muted">Receipts</span>
              <span className="block font-mono text-text-primary">
                {(snapshot.cost_data.total_receipts as number)?.toLocaleString() ?? '—'}
              </span>
            </div>
            <div>
              <span className="text-text-muted">Duration</span>
              <span className="block font-mono text-text-primary">
                {(snapshot.cost_data.total_duration_ms as number)?.toLocaleString() ?? '—'}ms
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Metadata */}
      <div>
        <h4 className="mb-1 text-xs font-semibold uppercase text-text-secondary">Governance Context</h4>
        <div className="flex flex-wrap gap-2 text-xs">
          {snapshot.metadata?.soul_constraints_active !== undefined && (
            <span className={`rounded border px-2 py-0.5 font-mono ${snapshot.metadata.soul_constraints_active ? 'bg-state-healthy/15 text-state-healthy border-state-healthy/30' : 'bg-surface-input text-text-muted border-border-default'}`}>
              Soul: {snapshot.metadata.soul_constraints_active ? 'ON' : 'OFF'}
            </span>
          )}
          {snapshot.metadata?.apl_rules_active !== undefined && (
            <span className={`rounded border px-2 py-0.5 font-mono ${snapshot.metadata.apl_rules_active ? 'bg-state-healthy/15 text-state-healthy border-state-healthy/30' : 'bg-surface-input text-text-muted border-border-default'}`}>
              APL: {snapshot.metadata.apl_rules_active ? 'ON' : 'OFF'}
            </span>
          )}
          {snapshot.metadata?.trust_ledger_active !== undefined && (
            <span className={`rounded border px-2 py-0.5 font-mono ${snapshot.metadata.trust_ledger_active ? 'bg-state-healthy/15 text-state-healthy border-state-healthy/30' : 'bg-surface-input text-text-muted border-border-default'}`}>
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
    <div className="space-y-3 rounded-lg border border-border-default bg-surface-card p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-text-secondary">Receipt Detail</h3>
        <div className="flex flex-wrap items-center gap-2">
          <TierBadge tier={receipt.tier} />
          <StatusBadge status={receipt.status} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <div>
          <span className="block text-text-muted">ID</span>
          <span className="break-all font-mono text-xs text-text-primary">{receipt.id}</span>
        </div>
        <div>
          <span className="block text-text-muted">Action</span>
          <span className="break-all font-mono text-text-primary">{receipt.action_type}</span>
        </div>
        <div>
          <span className="block text-text-muted">Name</span>
          <span className="break-words text-text-primary">{receipt.action_name}</span>
        </div>
        <div>
          <span className="block text-text-muted">Duration</span>
          <span className="font-mono text-text-primary">{receipt.duration_ms ?? '-'}ms</span>
        </div>
      </div>

      {Object.keys(receipt.inputs).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-text-secondary hover:text-text-primary">Inputs</summary>
          <pre className="mt-1 max-h-40 overflow-x-auto rounded bg-surface-input p-2 text-text-secondary">
            {JSON.stringify(receipt.inputs, null, 2)}
          </pre>
        </details>
      )}

      {Object.keys(receipt.outputs).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-text-secondary hover:text-text-primary">Outputs</summary>
          <pre className="mt-1 max-h-40 overflow-x-auto rounded bg-surface-input p-2 text-text-secondary">
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-lg space-y-4 rounded-xl border border-border-default bg-surface-card p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-text-primary">Time-Travel Operation</h2>
        <p className="text-sm text-text-muted">Quest: <span className="break-all font-mono text-text-secondary">{questId.slice(0, 24)}...</span></p>

        {status && !status.fork_allowed && (
          <div className="rounded border border-state-degraded/30 bg-state-degraded/10 p-3 text-sm text-state-degraded">
            Fork/Replay is disabled in the current Soul. Enable fork_permissions.allow_fork to proceed.
          </div>
        )}

        <div className="flex gap-2">
          <button
            onClick={() => setMode('replay')}
            className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${mode === 'replay' ? 'bg-accent-primary text-white' : 'bg-surface-input text-text-secondary hover:text-text-primary'}`}
          >
            Replay
          </button>
          <button
            onClick={() => setMode('fork')}
            className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${mode === 'fork' ? 'bg-accent-primary text-white' : 'bg-surface-input text-text-secondary hover:text-text-primary'}`}
          >
            Fork
          </button>
        </div>

        {mode === 'replay' && (
          <p className="text-sm text-text-muted">
            Re-execute this quest unchanged under the current Soul. A new quest ID will be assigned.
            Requires T{status?.require_approval_tier ?? 3} approval.
          </p>
        )}

        {mode === 'fork' && (
          <div className="space-y-2">
            <p className="text-sm text-text-muted">
              Fork this quest with modified inputs. Requires T{status?.require_approval_tier ?? 3} approval.
            </p>
            <label className="block text-xs text-text-muted">Modifications (JSON)</label>
            <textarea
              value={modsText}
              onChange={(e) => setModsText(e.target.value)}
              className="h-24 w-full rounded border border-border-default bg-surface-input p-2 text-sm font-mono text-text-primary focus:outline-none focus:ring-1 focus:ring-accent-primary"
              placeholder='{"inputs.query": "new prompt"}'
            />
          </div>
        )}

        {error && (
          <div className="rounded border border-state-error/30 bg-state-error/10 p-2 text-sm text-state-error">{error}</div>
        )}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-text-muted transition-colors hover:text-text-primary">Cancel</button>
          <button
            onClick={handleSubmit}
            disabled={submitting || (status !== null && !status.fork_allowed)}
            className="rounded bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary/80 disabled:bg-surface-input disabled:text-text-muted"
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
      const reason = e instanceof Error ? e.message : 'Unknown error'
      emitWarRoomNotification(`Snapshot fetch failed: ${reason}`, 'normal')
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

  const runtimeDegraded = !!status?.runtime_degraded
  const readinessChecks = [
    { label: 'Engine', ready: status?.engine_ready ?? status?.enabled },
    { label: 'Executor', ready: status?.quest_executor_ready },
    { label: 'Snapshots', ready: status?.snapshot_reader_ready },
    { label: 'Receipts', ready: status?.receipt_service_ready },
  ]

  return (
    <div className="space-y-4 p-4 md:p-6">
      {/* Header */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-accent-primary">System Forensics</p>
          <h1 className="text-2xl font-bold text-text-primary">Time-Travel Debugger</h1>
          <p className="mt-1 max-w-3xl text-sm text-text-muted">Inspect, replay, and fork past quest executions under governed control.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 lg:justify-end">
          {status && (
            <>
              <span className={`rounded border px-2 py-1 text-xs font-mono ${status.enabled && !runtimeDegraded ? 'bg-state-healthy/15 text-state-healthy border-state-healthy/30' : status.enabled ? 'bg-state-degraded/15 text-state-degraded border-state-degraded/30' : 'bg-surface-input text-text-muted border-border-default'}`}>
                {status.enabled ? (runtimeDegraded ? 'DEGRADED' : 'ENABLED') : 'DISABLED'}
              </span>
              {status.fork_allowed && (
                <span className="rounded border border-accent-primary/30 bg-accent-primary/15 px-2 py-1 text-xs font-mono text-accent-primary">
                  FORK: T{status.require_approval_tier}
                </span>
              )}
              {status.soul_version && (
                <span className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
                  Soul {status.soul_version}
                </span>
              )}
            </>
          )}
        </div>
      </div>

      {status && (
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {readinessChecks.map((item) => (
            <div key={item.label} className="flex items-center justify-between gap-2 rounded-lg border border-border-default bg-surface-card px-3 py-2">
              <span className="text-xs text-text-muted">{item.label}</span>
              <span className={`text-[10px] font-mono ${item.ready ? 'text-state-healthy' : 'text-state-degraded'}`}>
                {item.ready ? 'ready' : 'standby'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Quest Search */}
      <div className="rounded-lg border border-border-default bg-surface-card p-3">
        <div className="flex flex-col gap-2 lg:flex-row">
        <input
          type="text"
          value={questId}
          onChange={(e) => setQuestId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="Enter Quest ID..."
          className="min-w-0 flex-1 rounded-lg border border-border-default bg-surface-input px-4 py-2 text-sm font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent-primary"
        />
        <button
          onClick={handleSearch}
          disabled={loadingQuest || !questId.trim()}
          className="rounded-lg bg-accent-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-primary/80 disabled:bg-surface-input disabled:text-text-muted"
        >
          {loadingQuest ? 'Loading...' : 'Load Quest'}
        </button>
        {searchedQuestId && (
          <button
            onClick={() => setShowForkModal(true)}
            disabled={!status?.enabled}
            className="rounded-lg bg-state-degraded px-4 py-2 text-sm font-medium text-black transition-colors hover:bg-state-degraded/80 disabled:bg-surface-input disabled:text-text-muted"
          >
            Fork / Replay
          </button>
        )}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-muted">
          <span>Quest IDs are sourced from receipts.</span>
          <a href="/war-room/receipts" className="text-accent-primary hover:text-accent-primary/80">Open Receipt Explorer</a>
        </div>
      </div>

      {/* Messages */}
      {status?.degraded_reasons && status.degraded_reasons.length > 0 && (
        <div className="rounded-lg border border-state-degraded/30 bg-state-degraded/10 p-3 text-sm text-state-degraded">
          {status.degraded_reasons.join(' / ')}
        </div>
      )}
      {error && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-state-error/30 bg-state-error/10 p-3 text-sm text-state-error">
          <span className="min-w-0 break-words">{error}</span>
          <button onClick={() => setError(null)} className="text-state-error hover:text-state-error/80">x</button>
        </div>
      )}
      {resultMsg && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-state-healthy/30 bg-state-healthy/10 p-3 text-sm text-state-healthy">
          <span className="min-w-0 break-words">{resultMsg}</span>
          <button onClick={() => setResultMsg(null)} className="text-state-healthy hover:text-state-healthy/80">x</button>
        </div>
      )}

      {/* DAG Navigator */}
      {questData && (
        <div className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="mb-2 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-text-secondary">
              Receipt DAG — {questData.receipt_count} receipts
            </h2>
            <span className="break-all text-xs font-mono text-text-muted">{searchedQuestId?.slice(0, 24)}...</span>
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
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ReceiptDetail receipt={selectedReceipt} />
          <div>
            {loadingSnapshot ? (
              <div className="animate-pulse rounded-lg border border-border-default bg-surface-card p-4 text-sm text-text-muted">
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
        <div className="rounded-lg border border-border-default bg-surface-card p-6 text-text-muted">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-medium text-text-primary">No quest loaded</p>
              <p className="mt-1 text-sm">Load a quest to inspect receipts, governance state, replay controls, and fork controls.</p>
            </div>
            <a href="/war-room/receipts" className="inline-flex items-center justify-center rounded-lg border border-accent-primary/30 bg-accent-primary/10 px-3 py-2 text-sm font-medium text-accent-primary hover:bg-accent-primary/15">
              Receipt Explorer
            </a>
          </div>
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
