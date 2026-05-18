import { useState, useCallback, useEffect } from 'react'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Database,
  FileSearch,
  Filter,
  Link2,
  ListTree,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import { fetchReceipts, fetchReceiptStats, fetchReceiptContext } from '@/api'
import { TierBadge, Pagination } from '@/components'
import type { ReceiptItem, ReceiptContext } from '@/api/receipts'
import { formatTimestamp } from '@/utils/dateFormat'

const TIER_LABELS = ['T0', 'T1', 'T2', 'T3']

const ACTION_TYPE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  tool_call: { label: 'Tool Call', color: 'text-blue-400', bg: 'bg-blue-400/15' },
  llm_call: { label: 'LLM Call', color: 'text-purple-400', bg: 'bg-purple-400/15' },
  plan_step: { label: 'Plan Step', color: 'text-indigo-400', bg: 'bg-indigo-400/15' },
  file_op: { label: 'File Op', color: 'text-amber-400', bg: 'bg-amber-400/15' },
  env_query: { label: 'Env Query', color: 'text-cyan-400', bg: 'bg-cyan-400/15' },
  verification: { label: 'Verify', color: 'text-emerald-400', bg: 'bg-emerald-400/15' },
  verify_passed: { label: 'Verify Pass', color: 'text-emerald-400', bg: 'bg-emerald-400/15' },
  verify_failed: { label: 'Verify Fail', color: 'text-red-400', bg: 'bg-red-400/15' },
  system: { label: 'System', color: 'text-gray-400', bg: 'bg-gray-400/15' },
  user_interaction: { label: 'User', color: 'text-sky-400', bg: 'bg-sky-400/15' },
  token_minted: { label: 'Token Mint', color: 'text-green-400', bg: 'bg-green-400/15' },
  token_revoked: { label: 'Token Revoke', color: 'text-orange-400', bg: 'bg-orange-400/15' },
  token_expired: { label: 'Token Expire', color: 'text-gray-400', bg: 'bg-gray-400/15' },
  task_created: { label: 'Task Create', color: 'text-indigo-400', bg: 'bg-indigo-400/15' },
  step_started: { label: 'Step Start', color: 'text-blue-400', bg: 'bg-blue-400/15' },
  step_completed: { label: 'Step Done', color: 'text-emerald-400', bg: 'bg-emerald-400/15' },
  step_failed: { label: 'Step Fail', color: 'text-red-400', bg: 'bg-red-400/15' },
  voice_stt: { label: 'Voice STT', color: 'text-pink-400', bg: 'bg-pink-400/15' },
  voice_tts: { label: 'Voice TTS', color: 'text-pink-400', bg: 'bg-pink-400/15' },
  uab_detect: { label: 'UAB Detect', color: 'text-teal-400', bg: 'bg-teal-400/15' },
  uab_connect: { label: 'UAB Connect', color: 'text-teal-400', bg: 'bg-teal-400/15' },
  uab_enumerate: { label: 'UAB Enum', color: 'text-teal-400', bg: 'bg-teal-400/15' },
  uab_query: { label: 'UAB Query', color: 'text-teal-400', bg: 'bg-teal-400/15' },
  uab_act: { label: 'UAB Act', color: 'text-orange-400', bg: 'bg-orange-400/15' },
  uab_state: { label: 'UAB State', color: 'text-teal-400', bg: 'bg-teal-400/15' },
  hive_task_event: { label: 'HIVE Task', color: 'text-violet-400', bg: 'bg-violet-400/15' },
  hive_agent_event: { label: 'HIVE Agent', color: 'text-violet-400', bg: 'bg-violet-400/15' },
  hive_intervention_event: { label: 'HIVE Intervention', color: 'text-rose-400', bg: 'bg-rose-400/15' },
}

const ACTION_TYPE_OPTIONS = Object.entries(ACTION_TYPE_CONFIG).map(([value, { label }]) => ({
  value,
  label,
}))

const PAGE_SIZE = 50

type ReceiptTileTone = 'accent' | 'healthy' | 'warning' | 'error' | 'muted'

const receiptTileToneClass: Record<ReceiptTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  error: 'border-state-error/30 bg-state-error/10 text-state-error',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function ActionTypeBadge({ type }: { type: string }) {
  const config = ACTION_TYPE_CONFIG[type] ?? { label: type, color: 'text-gray-400', bg: 'bg-gray-400/15' }
  return (
    <span className={`inline-flex items-center rounded border border-current/20 px-2 py-0.5 text-[10px] font-medium ${config.color} ${config.bg}`}>
      {config.label}
    </span>
  )
}

function ReceiptTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: ReceiptTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${receiptTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 break-words text-2xl font-semibold leading-tight text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '--'
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms)}ms`
}

function isSuccessfulStatus(status: string): boolean {
  return status === 'success' || status === 'completed'
}

function isFailedStatus(status: string): boolean {
  return status === 'failed' || status === 'failure' || status === 'error'
}

function statusClass(status: string): string {
  if (isSuccessfulStatus(status)) return 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy'
  if (isFailedStatus(status)) return 'border-state-error/30 bg-state-error/10 text-state-error'
  return 'border-state-warning/30 bg-state-warning/10 text-state-warning'
}

function getStatusCount(byStatus: Record<string, number> | undefined, keys: string[]): number {
  if (!byStatus) return 0
  return keys.reduce((sum, key) => sum + (byStatus[key] ?? 0), 0)
}

function formatValue(value: unknown, maxLen = 200): string {
  if (value === null || value === undefined) return '--'
  if (typeof value === 'string') {
    return value.length > maxLen ? `${value.slice(0, maxLen)}...` : value
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  const json = JSON.stringify(value)
  return json.length > maxLen ? `${json.slice(0, maxLen)}...` : json
}

export function ReceiptExplorer() {
  usePageTitle('Receipt Explorer')
  const [search, setSearch] = useState('')
  const [searchDraft, setSearchDraft] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [actionTypeFilter, setActionTypeFilter] = useState('')
  const [questFilter, setQuestFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  const fetcher = useCallback(
    () =>
      fetchReceipts({
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        q: search || undefined,
        status: statusFilter || undefined,
        action_type: actionTypeFilter || undefined,
        quest_id: questFilter || undefined,
        risk_tier: tierFilter || undefined,
      }),
    [search, statusFilter, actionTypeFilter, questFilter, tierFilter, page],
  )

  const { data: receiptsData, refetch: refetchReceipts } = usePolling({ fetcher, interval: 15000 })
  const { data: statsData } = usePolling({ fetcher: fetchReceiptStats, interval: 30000 })

  const receipts = receiptsData?.receipts ?? []
  const stats = statsData?.stats
  const totalReceipts = stats?.total_receipts ?? receiptsData?.total ?? 0
  const successCount = getStatusCount(stats?.by_status, ['success', 'completed'])
  const failureCount = getStatusCount(stats?.by_status, ['failure', 'failed', 'error'])
  const successRate =
    stats && stats.total_receipts > 0
      ? `${Math.round((successCount / stats.total_receipts) * 100)}%`
      : '--'
  const activeFilterCount = [search, statusFilter, tierFilter, actionTypeFilter, questFilter].filter(Boolean).length

  const clearFilters = () => {
    setPage(1)
    setSearch('')
    setSearchDraft('')
    setStatusFilter('')
    setTierFilter('')
    setActionTypeFilter('')
    setQuestFilter('')
  }

  const submitSearch = () => {
    setPage(1)
    setSearch(searchDraft.trim())
  }

  useEffect(() => {
    refetchReceipts()
  }, [fetcher, refetchReceipts])

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-default bg-surface-card px-5 py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-widest text-accent-primary">Audit Trail</div>
            <h2 className="mt-2 text-2xl font-semibold leading-tight text-text-primary">Receipt Explorer</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-muted">
              Search durable execution receipts, inspect parent and child context, and trace quest evidence without leaving the War Room.
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs sm:min-w-[360px]">
            <div className="rounded border border-border-default bg-surface-card-elevated px-3 py-2">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Total</div>
              <div className="mt-1 font-mono text-text-primary">{totalReceipts.toLocaleString()}</div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated px-3 py-2">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Visible</div>
              <div className="mt-1 font-mono text-text-primary">{receipts.length.toLocaleString()}</div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated px-3 py-2">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Filters</div>
              <div className="mt-1 font-mono text-text-primary">{activeFilterCount}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <ReceiptTile
          label="Receipt Volume"
          value={totalReceipts.toLocaleString()}
          detail="Durable execution records indexed."
          tone="accent"
        />
        <ReceiptTile
          label="Success Rate"
          value={successRate}
          detail={`${successCount.toLocaleString()} successful receipts.`}
          tone="healthy"
        />
        <ReceiptTile
          label="Failures"
          value={failureCount.toLocaleString()}
          detail="Receipts requiring operator review."
          tone={failureCount > 0 ? 'error' : 'muted'}
        />
        <ReceiptTile
          label="Avg Duration"
          value={formatDuration(stats?.duration_ms?.average)}
          detail="Mean execution time across receipts."
          tone="muted"
        />
        <ReceiptTile
          label="Token Volume"
          value={stats?.tokens?.total?.toLocaleString() ?? '--'}
          detail="Tracked model token usage."
          tone="muted"
        />
      </section>

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-text-primary">Audit Query</h3>
              <p className="text-xs leading-5 text-text-muted">Narrow by text, status, tier, action type, or selected quest.</p>
            </div>
          </div>
          <button
            type="button"
            onClick={clearFilters}
            disabled={activeFilterCount === 0}
            className="inline-flex items-center justify-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs text-text-secondary transition-colors hover:border-border-active hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-45"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            Clear Filters
          </button>
        </div>

        {questFilter && (
          <div className="mt-4 flex min-w-0 flex-wrap items-center gap-2 rounded border border-accent-primary/30 bg-accent-primary/10 px-3 py-2 text-sm">
            <ListTree className="h-4 w-4 shrink-0 text-accent-primary" aria-hidden="true" />
            <span className="font-medium text-accent-primary">Quest filter</span>
            <span className="min-w-0 break-all font-mono text-xs text-text-secondary">{questFilter}</span>
          </div>
        )}

        <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[minmax(260px,1.8fr)_minmax(150px,0.7fr)_minmax(150px,0.7fr)_minmax(220px,1fr)]">
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Search</span>
            <div className="flex min-w-0 flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" aria-hidden="true" />
                <input
                  type="text"
                  placeholder="Search receipt id, action, input, or output..."
                  value={searchDraft}
                  onChange={(e) => setSearchDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      submitSearch()
                    }
                  }}
                  className="w-full min-w-0 rounded-md border border-border-default bg-surface-input py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-border-active focus:outline-none"
                />
              </div>
              <button
                type="button"
                onClick={submitSearch}
                className="inline-flex items-center justify-center gap-2 rounded-md border border-accent-primary/40 bg-accent-primary/10 px-3 py-2 text-sm font-medium text-accent-primary transition-colors hover:bg-accent-primary/15"
              >
                <Search className="h-4 w-4" aria-hidden="true" />
                Search
              </button>
            </div>
            {search && searchDraft !== search && (
              <span className="mt-1 block text-[10px] text-text-muted">
                Current search: <span className="font-mono text-text-secondary">{search}</span>
              </span>
            )}
          </label>
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Status</span>
            <select
              value={statusFilter}
              onChange={(e) => {
                setPage(1)
                setStatusFilter(e.target.value)
              }}
              className="w-full min-w-0 rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-border-active focus:outline-none"
            >
              <option value="">All Statuses</option>
              <option value="success">Success</option>
              <option value="failure">Failed</option>
              <option value="pending">Pending</option>
            </select>
          </label>
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Tier</span>
            <select
              value={tierFilter}
              onChange={(e) => {
                setPage(1)
                setTierFilter(e.target.value)
              }}
              className="w-full min-w-0 rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-border-active focus:outline-none"
            >
              <option value="">All Tiers</option>
              {TIER_LABELS.map((t, i) => (
                <option key={t} value={String(i)}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Action Type</span>
            <select
              value={actionTypeFilter}
              onChange={(e) => {
                setPage(1)
                setActionTypeFilter(e.target.value)
              }}
              className="w-full min-w-0 rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-border-active focus:outline-none"
            >
              <option value="">All Types</option>
              {ACTION_TYPE_OPTIONS.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section className="rounded-lg border border-border-default bg-surface-card">
        <div className="flex flex-col gap-3 border-b border-border-default px-4 py-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2">
            <FileSearch className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-text-primary">Receipt Stream</h3>
              <p className="text-xs leading-5 text-text-muted">Click a receipt to inspect inputs, outputs, lineage, and IDs.</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1">
              Page {page}
            </span>
            <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1">
              {receipts.length} visible
            </span>
          </div>
        </div>

        <div className="lg:hidden">
          {receipts.length === 0 ? (
            <div className="px-4 py-12 text-center">
              <div className="mx-auto flex max-w-sm flex-col items-center gap-2">
                <Database className="h-6 w-6 text-text-muted" aria-hidden="true" />
                <div className="text-sm font-medium text-text-primary">No receipts match current filters</div>
                <div className="text-xs leading-5 text-text-muted">Clear filters or adjust the query to broaden the audit window.</div>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-border-default">
              {receipts.map((r: ReceiptItem) => (
                <MobileReceiptCard
                  key={r.id}
                  receipt={r}
                  expanded={expandedId === r.id}
                  onToggle={() => setExpandedId(expandedId === r.id ? null : r.id)}
                  onQuestFilter={(questId) => {
                    setPage(1)
                    setQuestFilter(questId)
                  }}
                  onExpandParent={(parentId) => setExpandedId(parentId)}
                />
              ))}
            </div>
          )}
        </div>

        <div className="hidden overflow-x-auto lg:block">
          <table className="w-full min-w-[980px] text-sm">
            <thead>
              <tr className="border-b border-border-default text-xs uppercase tracking-wider text-text-muted">
                <th className="w-10 px-4 py-3 text-left" aria-label="Expand" />
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
                  <td colSpan={8} className="px-4 py-12 text-center">
                    <div className="mx-auto flex max-w-sm flex-col items-center gap-2">
                      <Database className="h-6 w-6 text-text-muted" aria-hidden="true" />
                      <div className="text-sm font-medium text-text-primary">No receipts match current filters</div>
                      <div className="text-xs leading-5 text-text-muted">Clear filters or adjust the query to broaden the audit window.</div>
                    </div>
                  </td>
                </tr>
              ) : (
                receipts.map((r: ReceiptItem) => (
                  <ReceiptRow
                    key={r.id}
                    receipt={r}
                    expanded={expandedId === r.id}
                    onToggle={() => setExpandedId(expandedId === r.id ? null : r.id)}
                    onQuestFilter={(questId) => {
                      setPage(1)
                      setQuestFilter(questId)
                    }}
                    onExpandParent={(parentId) => setExpandedId(parentId)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
        {receiptsData && (
          <Pagination
            currentPage={page}
            totalPages={Math.ceil((receiptsData.total ?? 0) / PAGE_SIZE)}
            onPageChange={setPage}
            totalItems={receiptsData.total}
          />
        )}
      </section>
    </div>
  )
}

function KeyValuePairs({ data, label }: { data: Record<string, unknown>; label: string }) {
  const entries = Object.entries(data)
  const [expanded, setExpanded] = useState(false)
  if (entries.length === 0) {
    return (
      <div className="rounded border border-border-default bg-surface-card px-3 py-3">
        <span className="text-xs font-medium uppercase tracking-wider text-text-muted">{label}</span>
        <p className="mt-2 text-xs italic text-text-muted">No {label.toLowerCase()}</p>
      </div>
    )
  }

  const displayEntries = expanded ? entries : entries.slice(0, 6)
  const hasMore = entries.length > 6

  return (
    <div className="rounded border border-border-default bg-surface-card px-3 py-3">
      <span className="text-xs font-medium uppercase tracking-wider text-text-muted">{label}</span>
      <div className="mt-3 space-y-2">
        {displayEntries.map(([key, val]) => (
          <div key={key} className="grid min-w-0 gap-1 text-xs sm:grid-cols-[150px_minmax(0,1fr)]">
            <span className="min-w-0 break-all font-mono text-text-muted">{key}</span>
            <span className="min-w-0 break-words text-text-secondary">{formatValue(val)}</span>
          </div>
        ))}
      </div>
      {hasMore && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="mt-3 text-xs text-accent-primary hover:underline"
        >
          {expanded ? 'Show less' : `Show ${entries.length - 6} more fields`}
        </button>
      )}
    </div>
  )
}

function MetadataPills({ metadata }: { metadata: Record<string, unknown> }) {
  const entries = Object.entries(metadata).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (entries.length === 0) return null
  return (
    <div className="flex min-w-0 flex-wrap gap-1.5">
      {entries.map(([key, val]) => (
        <span
          key={key}
          className="inline-flex max-w-full items-center gap-1 rounded border border-border-default bg-surface-input px-2 py-0.5 text-[10px]"
        >
          <span className="shrink-0 text-text-muted">{key}:</span>
          <span className="min-w-0 break-all font-mono text-text-secondary">{formatValue(val, 60)}</span>
        </span>
      ))}
    </div>
  )
}

function useReceiptContextState(expanded: boolean, receiptId: string) {
  const [context, setContext] = useState<ReceiptContext | null>(null)
  const [contextLoading, setContextLoading] = useState(false)

  useEffect(() => {
    if (expanded && !context && !contextLoading) {
      setContextLoading(true)
      fetchReceiptContext(receiptId)
        .then(setContext)
        .catch(() => setContext(null))
        .finally(() => setContextLoading(false))
    }
    if (!expanded) {
      setContext(null)
    }
  }, [expanded, receiptId]) // eslint-disable-line react-hooks/exhaustive-deps

  return { context, contextLoading }
}

function ReceiptDetail({
  receipt: r,
  context,
  contextLoading,
  onQuestFilter,
  onExpandParent,
}: {
  receipt: ReceiptItem
  context: ReceiptContext | null
  contextLoading: boolean
  onQuestFilter: (questId: string) => void
  onExpandParent: (parentId: string) => void
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 rounded border border-border-default bg-surface-card px-3 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <ActionTypeBadge type={r.action_type} />
          <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-mono ${statusClass(r.status)}`}>
            {isSuccessfulStatus(r.status) ? (
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            ) : isFailedStatus(r.status) ? (
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <Activity className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {r.status.toUpperCase()}
          </span>
          {contextLoading && (
            <span className="text-xs text-text-muted">Loading context...</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
          <span className="inline-flex items-center gap-1 rounded border border-border-default bg-surface-input px-2 py-1">
            <Clock className="h-3.5 w-3.5" aria-hidden="true" />
            {formatDuration(r.duration_ms)}
          </span>
          <span className="inline-flex items-center gap-1 rounded border border-border-default bg-surface-input px-2 py-1">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            T{r.tier}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {r.quest_id && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onQuestFilter(r.quest_id!)
            }}
            className="min-w-0 rounded border border-accent-primary/30 bg-accent-primary/10 px-3 py-2 text-left text-xs transition-colors hover:bg-accent-primary/15"
          >
            <span className="flex items-center gap-2 font-medium text-accent-primary">
              <ListTree className="h-3.5 w-3.5" aria-hidden="true" />
              Quest Lineage
            </span>
            <span className="mt-1 block break-all font-mono text-text-secondary">{r.quest_id}</span>
            {context?.quest_receipts_count != null && (
              <span className="mt-1 block text-text-muted">{context.quest_receipts_count} related receipts</span>
            )}
          </button>
        )}

        {context?.parent && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onExpandParent(context.parent!.id)
            }}
            className="min-w-0 rounded border border-sky-400/30 bg-sky-400/10 px-3 py-2 text-left text-xs transition-colors hover:bg-sky-400/15"
          >
            <span className="flex items-center gap-2 font-medium text-sky-400">
              <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
              Parent Receipt
            </span>
            <span className="mt-1 block truncate text-text-primary">{context.parent.action_name}</span>
            <span className="mt-1 block break-all font-mono text-text-muted">{context.parent.id}</span>
          </button>
        )}

        {context && context.children.length > 0 && (
          <div className="min-w-0 rounded border border-state-healthy/30 bg-state-healthy/10 px-3 py-2 text-xs">
            <span className="flex items-center gap-2 font-medium text-state-healthy">
              <ListTree className="h-3.5 w-3.5" aria-hidden="true" />
              Child Operations
            </span>
            <span className="mt-1 block text-text-secondary">
              {context.children.length} child operation{context.children.length !== 1 ? 's' : ''}
            </span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <KeyValuePairs data={r.inputs} label="Inputs" />
        <KeyValuePairs data={r.outputs} label="Outputs" />
      </div>

      {r.error_message && (
        <div className="rounded border border-state-error/30 bg-state-error/10 p-3 text-xs leading-5 text-state-error">
          {r.error_message}
        </div>
      )}

      {context && context.children.length > 0 && (
        <div className="rounded border border-border-default bg-surface-card px-3 py-3">
          <span className="text-xs font-medium uppercase tracking-wider text-text-muted">Child Operations</span>
          <div className="mt-3 max-h-44 space-y-2 overflow-y-auto">
            {context.children.map((child) => (
              <div
                key={child.id}
                className="grid min-w-0 gap-2 rounded bg-surface-input px-3 py-2 text-xs md:grid-cols-[auto_minmax(0,1fr)_auto_auto]"
              >
                <ActionTypeBadge type={child.action_type} />
                <span className="min-w-0 truncate text-text-primary" title={child.action_name}>
                  {child.action_name}
                </span>
                <span className={`font-mono ${isSuccessfulStatus(child.status) ? 'text-state-healthy' : isFailedStatus(child.status) ? 'text-state-error' : 'text-state-degraded'}`}>
                  {child.status.toUpperCase()}
                </span>
                <span className="font-mono text-text-muted">{formatDuration(child.duration_ms)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <MetadataPills metadata={r.metadata} />

      <div className="rounded border border-border-default bg-surface-card px-3 py-2 text-[10px] font-mono leading-5 text-text-muted">
        <div className="break-all">Receipt: {r.id}</div>
        {r.quest_id && <div className="break-all">Quest: {r.quest_id}</div>}
        {r.parent_id && <div className="break-all">Parent: {r.parent_id}</div>}
      </div>
    </div>
  )
}

function MobileReceiptCard({
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
  const { context, contextLoading } = useReceiptContextState(expanded, r.id)

  return (
    <div className={`px-4 py-3 ${expanded ? 'bg-accent-primary/5' : ''}`}>
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left"
        aria-expanded={expanded}
      >
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              {expanded ? <ChevronDown className="h-4 w-4 text-text-muted" aria-hidden="true" /> : <ChevronRight className="h-4 w-4 text-text-muted" aria-hidden="true" />}
              <TierBadge tier={r.tier} compact />
              <ActionTypeBadge type={r.action_type} />
              <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-mono ${statusClass(r.status)}`}>
                {r.status.toUpperCase()}
              </span>
            </div>
            <div className="mt-2 min-w-0 break-words text-sm font-medium leading-5 text-text-primary">
              {r.action_name}
            </div>
          </div>
          <div className="shrink-0 text-right font-mono text-[11px] text-text-muted">
            <div>{formatDuration(r.duration_ms)}</div>
            <div>{r.token_count?.toLocaleString() ?? '--'} tok</div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-text-muted">
          <div className="min-w-0 rounded border border-border-default bg-surface-card-elevated px-2 py-1">
            <div className="uppercase tracking-wider">Time</div>
            <div className="mt-1 break-words font-mono text-text-secondary">{formatTimestamp(r.timestamp)}</div>
          </div>
          <div className="min-w-0 rounded border border-border-default bg-surface-card-elevated px-2 py-1">
            <div className="uppercase tracking-wider">Evidence</div>
            <div className="mt-1 font-mono text-text-secondary">{r.quest_id ? 'Quest linked' : r.parent_id ? 'Parent linked' : 'Standalone'}</div>
          </div>
        </div>
      </button>

      {expanded && (
        <div className="mt-4">
          <ReceiptDetail
            receipt={r}
            context={context}
            contextLoading={contextLoading}
            onQuestFilter={onQuestFilter}
            onExpandParent={onExpandParent}
          />
        </div>
      )}
    </div>
  )
}

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
  const { context, contextLoading } = useReceiptContextState(expanded, r.id)

  return (
    <>
      <tr
        onClick={onToggle}
        className={`border-b border-border-default cursor-pointer transition-colors hover:bg-surface-card-elevated ${
          expanded ? 'bg-accent-primary/5' : ''
        }`}
      >
        <td className="px-4 py-3 text-text-muted">
          {expanded ? <ChevronDown className="h-4 w-4" aria-hidden="true" /> : <ChevronRight className="h-4 w-4" aria-hidden="true" />}
        </td>
        <td className="px-4 py-3 font-mono text-xs text-text-muted">
          {formatTimestamp(r.timestamp)}
        </td>
        <td className="px-4 py-3">
          <TierBadge tier={r.tier} compact />
        </td>
        <td className="px-4 py-3">
          <ActionTypeBadge type={r.action_type} />
        </td>
        <td className="max-w-[320px] truncate px-4 py-3 text-text-primary" title={r.action_name}>
          {r.action_name}
        </td>
        <td className="px-4 py-3">
          <span className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-mono ${statusClass(r.status)}`}>
            {r.status.toUpperCase()}
          </span>
        </td>
        <td className="px-4 py-3 text-right font-mono text-text-secondary">
          {formatDuration(r.duration_ms)}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text-secondary">
          {r.token_count?.toLocaleString() ?? '--'}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-border-default">
          <td colSpan={8} className="bg-surface-card-elevated px-4 py-4">
            <ReceiptDetail
              receipt={r}
              context={context}
              contextLoading={contextLoading}
              onQuestFilter={onQuestFilter}
              onExpandParent={onExpandParent}
            />
          </td>
        </tr>
      )}
    </>
  )
}
