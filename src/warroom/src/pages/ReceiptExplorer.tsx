import { useState, useCallback, useEffect } from 'react'
import { usePolling } from '@/hooks'
import { fetchReceipts, fetchReceiptStats, fetchReceiptContext } from '@/api'
import { TierBadge, MetricCard } from '@/components'
import type { ReceiptItem, ReceiptContext } from '@/api/receipts'

const TIER_LABELS = ['T0', 'T1', 'T2', 'T3']

// V29: Action type display config — color-coded badges
const ACTION_TYPE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  tool_call:     { label: 'Tool Call',    color: 'text-blue-400',   bg: 'bg-blue-400/15' },
  llm_call:      { label: 'LLM Call',     color: 'text-purple-400', bg: 'bg-purple-400/15' },
  plan_step:     { label: 'Plan Step',    color: 'text-indigo-400', bg: 'bg-indigo-400/15' },
  file_op:       { label: 'File Op',      color: 'text-amber-400',  bg: 'bg-amber-400/15' },
  env_query:     { label: 'Env Query',    color: 'text-cyan-400',   bg: 'bg-cyan-400/15' },
  verification:  { label: 'Verify',       color: 'text-emerald-400', bg: 'bg-emerald-400/15' },
  verify_passed: { label: 'Verify Pass',  color: 'text-emerald-400', bg: 'bg-emerald-400/15' },
  verify_failed: { label: 'Verify Fail',  color: 'text-red-400',    bg: 'bg-red-400/15' },
  system:        { label: 'System',       color: 'text-gray-400',   bg: 'bg-gray-400/15' },
  user_interaction: { label: 'User',      color: 'text-sky-400',    bg: 'bg-sky-400/15' },
  token_minted:  { label: 'Token Mint',   color: 'text-green-400',  bg: 'bg-green-400/15' },
  token_revoked: { label: 'Token Revoke', color: 'text-orange-400', bg: 'bg-orange-400/15' },
  token_expired: { label: 'Token Expire', color: 'text-gray-400',   bg: 'bg-gray-400/15' },
  task_created:  { label: 'Task Create',  color: 'text-indigo-400', bg: 'bg-indigo-400/15' },
  step_started:  { label: 'Step Start',   color: 'text-blue-400',   bg: 'bg-blue-400/15' },
  step_completed:{ label: 'Step Done',    color: 'text-emerald-400', bg: 'bg-emerald-400/15' },
  step_failed:   { label: 'Step Fail',    color: 'text-red-400',    bg: 'bg-red-400/15' },
  voice_stt:     { label: 'Voice STT',    color: 'text-pink-400',   bg: 'bg-pink-400/15' },
  voice_tts:     { label: 'Voice TTS',    color: 'text-pink-400',   bg: 'bg-pink-400/15' },
}

function ActionTypeBadge({ type }: { type: string }) {
  const config = ACTION_TYPE_CONFIG[type] ?? { label: type, color: 'text-gray-400', bg: 'bg-gray-400/15' }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${config.color} ${config.bg}`}>
      {config.label}
    </span>
  )
}

// Unique action types for the filter dropdown
const ACTION_TYPE_OPTIONS = Object.entries(ACTION_TYPE_CONFIG).map(([value, { label }]) => ({
  value,
  label,
}))

export function ReceiptExplorer() {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [actionTypeFilter, setActionTypeFilter] = useState('')
  const [questFilter, setQuestFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const fetcher = useCallback(
    () =>
      fetchReceipts({
        limit: 100,
        q: search || undefined,
        status: statusFilter || undefined,
        action_type: actionTypeFilter || undefined,
        quest_id: questFilter || undefined,
      }),
    [search, statusFilter, actionTypeFilter, questFilter],
  )

  const { data: receiptsData } = usePolling({ fetcher, interval: 15000 })
  const { data: statsData } = usePolling({ fetcher: fetchReceiptStats, interval: 30000 })

  const receipts = (receiptsData?.receipts ?? []).filter(
    (r) => !tierFilter || String(r.tier) === tierFilter,
  )
  const stats = statsData?.stats

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Receipt Explorer</h2>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <MetricCard label="Total Receipts" value={stats.total_receipts} />
          <MetricCard label="Avg Duration" value={`${Math.round(stats.duration_ms?.average ?? 0)}ms`} />
          <MetricCard label="Total Tokens" value={stats.tokens?.total?.toLocaleString() ?? '--'} />
          <MetricCard
            label="Success Rate"
            value={
              stats.by_status
                ? `${Math.round(((stats.by_status.success ?? 0) / Math.max(stats.total_receipts, 1)) * 100)}%`
                : '--'
            }
          />
        </div>
      )}

      {/* Quest filter banner */}
      {questFilter && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 bg-accent-primary/10 border border-accent-primary/30 rounded-lg text-sm">
          <span className="text-accent-primary font-medium">Filtered by Quest:</span>
          <span className="font-mono text-xs text-text-secondary">{questFilter}</span>
          <button
            onClick={() => setQuestFilter('')}
            className="ml-auto text-text-muted hover:text-text-primary text-xs px-2 py-1 rounded hover:bg-surface-card-elevated"
          >
            Clear
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <input
          type="text"
          placeholder="Search receipts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active w-64"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
        >
          <option value="">All Statuses</option>
          <option value="success">Success</option>
          <option value="failure">Failed</option>
          <option value="pending">Pending</option>
        </select>
        <select
          value={tierFilter}
          onChange={(e) => setTierFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
        >
          <option value="">All Tiers</option>
          {TIER_LABELS.map((t, i) => (
            <option key={i} value={String(i)}>{t}</option>
          ))}
        </select>
        <select
          value={actionTypeFilter}
          onChange={(e) => setActionTypeFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary"
        >
          <option value="">All Types</option>
          {ACTION_TYPE_OPTIONS.map(({ value, label }) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
      </div>

      {/* Receipt Table */}
      <div className="bg-surface-card border border-border-default rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border-default text-text-muted text-xs uppercase tracking-wider">
              <th className="px-4 py-3 text-left">Time</th>
              <th className="px-4 py-3 text-left">Tier</th>
              <th className="px-4 py-3 text-left">Type</th>
              <th className="px-4 py-3 text-left">Action</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-right">Duration</th>
              <th className="px-4 py-3 text-right">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {receipts.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-text-muted">
                  No receipts found
                </td>
              </tr>
            ) : (
              receipts.map((r: ReceiptItem) => (
                <ReceiptRow
                  key={r.id}
                  receipt={r}
                  expanded={expandedId === r.id}
                  onToggle={() => setExpandedId(expandedId === r.id ? null : r.id)}
                  onQuestFilter={setQuestFilter}
                  onExpandParent={(parentId) => setExpandedId(parentId)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Helpers for human-readable I/O ──

function formatValue(value: unknown, maxLen = 200): string {
  if (value === null || value === undefined) return '--'
  if (typeof value === 'string') {
    return value.length > maxLen ? value.slice(0, maxLen) + '...' : value
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  const json = JSON.stringify(value)
  return json.length > maxLen ? json.slice(0, maxLen) + '...' : json
}

function KeyValuePairs({ data, label }: { data: Record<string, unknown>; label: string }) {
  const entries = Object.entries(data)
  const [expanded, setExpanded] = useState(false)
  if (entries.length === 0) return <p className="text-text-muted text-xs italic">No {label.toLowerCase()}</p>

  const displayEntries = expanded ? entries : entries.slice(0, 6)
  const hasMore = entries.length > 6

  return (
    <div>
      <span className="text-text-muted block mb-1 text-xs font-medium">{label}</span>
      <div className="space-y-1">
        {displayEntries.map(([key, val]) => (
          <div key={key} className="flex gap-2 text-xs">
            <span className="text-text-muted font-mono shrink-0">{key}:</span>
            <span className="text-text-secondary break-all">{formatValue(val)}</span>
          </div>
        ))}
      </div>
      {hasMore && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-accent-primary text-[10px] mt-1 hover:underline"
        >
          {expanded ? 'Show less' : `+${entries.length - 6} more fields`}
        </button>
      )}
    </div>
  )
}

function MetadataPills({ metadata }: { metadata: Record<string, unknown> }) {
  const entries = Object.entries(metadata).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (entries.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5 mt-2">
      {entries.map(([key, val]) => (
        <span
          key={key}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] bg-surface-input border border-border-default"
        >
          <span className="text-text-muted">{key}:</span>
          <span className="text-text-secondary font-mono">{formatValue(val, 60)}</span>
        </span>
      ))}
    </div>
  )
}

// ── Receipt Row with expandable detail panel ──

function ReceiptRow({
  receipt: r,
  expanded,
  onToggle,
  onQuestFilter,
  onExpandParent,
}: {
  receipt: ReceiptItem
  expanded: boolean
  onToggle: () => void
  onQuestFilter: (questId: string) => void
  onExpandParent: (parentId: string) => void
}) {
  const [context, setContext] = useState<ReceiptContext | null>(null)
  const [contextLoading, setContextLoading] = useState(false)

  // Fetch context when expanded
  useEffect(() => {
    if (expanded && !context && !contextLoading) {
      setContextLoading(true)
      fetchReceiptContext(r.id)
        .then(setContext)
        .catch(() => setContext(null))
        .finally(() => setContextLoading(false))
    }
    if (!expanded) {
      setContext(null)
    }
  }, [expanded, r.id])  // eslint-disable-line react-hooks/exhaustive-deps

  const statusColor =
    r.status === 'success' ? 'text-state-healthy'
    : r.status === 'completed' ? 'text-state-healthy'
    : r.status === 'failed' ? 'text-state-error'
    : r.status === 'failure' ? 'text-state-error'
    : 'text-state-degraded'

  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-border-default hover:bg-surface-card-elevated cursor-pointer transition-colors"
      >
        <td className="px-4 py-3 font-mono text-xs text-text-muted">
          {new Date(r.timestamp).toLocaleString()}
        </td>
        <td className="px-4 py-3">
          <TierBadge tier={r.tier} compact />
        </td>
        <td className="px-4 py-3">
          <ActionTypeBadge type={r.action_type} />
        </td>
        <td className="px-4 py-3 text-text-primary">{r.action_name}</td>
        <td className={`px-4 py-3 font-mono text-xs ${statusColor}`}>
          {r.status.toUpperCase()}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text-secondary">
          {r.duration_ms != null ? `${r.duration_ms}ms` : '--'}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text-secondary">
          {r.token_count ?? '--'}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-border-default">
          <td colSpan={7} className="px-4 py-4 bg-surface-card-elevated">
            {/* Context bar — connections */}
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <ActionTypeBadge type={r.action_type} />

              {r.quest_id && (
                <button
                  onClick={(e) => { e.stopPropagation(); onQuestFilter(r.quest_id!) }}
                  className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[10px] font-medium bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25 transition-colors"
                >
                  Quest: {r.quest_id.slice(0, 8)}...
                  {context?.quest_receipts_count != null && (
                    <span className="text-accent-primary/70">({context.quest_receipts_count} receipts)</span>
                  )}
                </button>
              )}

              {context?.parent && (
                <button
                  onClick={(e) => { e.stopPropagation(); onExpandParent(context.parent!.id) }}
                  className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[10px] font-medium bg-sky-400/15 text-sky-400 hover:bg-sky-400/25 transition-colors"
                >
                  Parent: {context.parent.action_name}
                </button>
              )}

              {context && context.children.length > 0 && (
                <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-[10px] font-medium bg-emerald-400/15 text-emerald-400">
                  {context.children.length} child operation{context.children.length !== 1 ? 's' : ''}
                </span>
              )}

              {contextLoading && (
                <span className="text-[10px] text-text-muted animate-pulse">Loading context...</span>
              )}
            </div>

            {/* I/O section */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              <KeyValuePairs data={r.inputs} label="Inputs" />
              <KeyValuePairs data={r.outputs} label="Outputs" />
            </div>

            {/* Error */}
            {r.error_message && (
              <div className="mt-3 text-xs text-state-error bg-state-error/10 rounded p-2">
                {r.error_message}
              </div>
            )}

            {/* Children list */}
            {context && context.children.length > 0 && (
              <div className="mt-3">
                <span className="text-text-muted text-[10px] font-medium block mb-1">Child Operations</span>
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {context.children.map((child) => (
                    <div
                      key={child.id}
                      className="flex items-center gap-2 text-[11px] px-2 py-1 rounded bg-surface-input"
                    >
                      <ActionTypeBadge type={child.action_type} />
                      <span className="text-text-primary">{child.action_name}</span>
                      <span className={`ml-auto font-mono ${child.status === 'success' ? 'text-state-healthy' : child.status === 'failure' || child.status === 'failed' ? 'text-state-error' : 'text-state-degraded'}`}>
                        {child.status.toUpperCase()}
                      </span>
                      {child.duration_ms != null && (
                        <span className="text-text-muted font-mono">{child.duration_ms}ms</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Metadata pills */}
            <MetadataPills metadata={r.metadata} />

            {/* IDs footer */}
            <div className="mt-3 pt-2 border-t border-border-default text-[10px] text-text-muted font-mono">
              ID: {r.id}
              {r.quest_id && <> | Quest: {r.quest_id}</>}
              {r.parent_id && <> | Parent: {r.parent_id}</>}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
