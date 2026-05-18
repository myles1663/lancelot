import { useMemo, useState, type ReactNode } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock,
  FileSearch,
  Filter,
  GitBranch,
  History,
  Network,
  RefreshCw,
  Search,
  Shield,
  X,
} from 'lucide-react'
import { usePolling } from '@/hooks/usePolling'
import { usePageTitle } from '@/hooks'
import { apiGet } from '@/api/client'
import type {
  AuditEntry,
  AuditSummary,
  ForensicTimeline,
} from '@/api/federation'
import { getErrorMessage } from '@/utils/errors'

type Category =
  | 'GOVERNANCE'
  | 'WORKFLOW'
  | 'HEALTH'
  | 'COST'
  | 'CONTRADICTION'
  | 'SOUL'
  | 'OTHER'

interface AuditFilters {
  questId: string
  instanceId: string
  category: string
}

const CATEGORY_LABELS: Record<string, Category> = {
  handoff: 'WORKFLOW',
  soul: 'SOUL',
  kill: 'GOVERNANCE',
  divergence: 'HEALTH',
  reconciliation: 'HEALTH',
  contradiction: 'CONTRADICTION',
  cost: 'COST',
  peer: 'HEALTH',
}

const CATEGORY_OPTIONS: Array<{ value: Category; label: string }> = [
  { value: 'GOVERNANCE', label: 'Governance' },
  { value: 'WORKFLOW', label: 'Workflow' },
  { value: 'HEALTH', label: 'Health' },
  { value: 'COST', label: 'Cost' },
  { value: 'SOUL', label: 'Soul' },
  { value: 'CONTRADICTION', label: 'Contradiction' },
  { value: 'OTHER', label: 'Other' },
]

const EVENT_TONES: Record<Category, string> = {
  GOVERNANCE: 'border-state-error bg-state-error/8 text-state-error',
  WORKFLOW: 'border-accent-primary bg-accent-primary/10 text-accent-primary',
  HEALTH: 'border-state-warning bg-state-warning/10 text-state-warning',
  COST: 'border-state-healthy bg-state-healthy/10 text-state-healthy',
  CONTRADICTION: 'border-orange-500 bg-orange-500/10 text-orange-300',
  SOUL: 'border-violet-400 bg-violet-500/10 text-violet-300',
  OTHER: 'border-border-default bg-surface-card-elevated text-text-muted',
}

const LEFT_BORDER_TONES: Record<Category, string> = {
  GOVERNANCE: 'border-l-state-error',
  WORKFLOW: 'border-l-accent-primary',
  HEALTH: 'border-l-state-warning',
  COST: 'border-l-state-healthy',
  CONTRADICTION: 'border-l-orange-500',
  SOUL: 'border-l-violet-400',
  OTHER: 'border-l-border-active',
}

function getCategory(eventType: string): Category {
  for (const [prefix, label] of Object.entries(CATEGORY_LABELS)) {
    if (eventType.startsWith(prefix)) return label
  }
  return 'OTHER'
}

function readable(value: string): string {
  return value.replace(/_/g, ' ')
}

function formatTime(value?: string): string {
  if (!value) return 'N/A'
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDate(value?: string): string {
  if (!value) return 'N/A'
  return new Date(value).toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function shortHash(value?: string): string {
  if (!value) return 'None'
  return value.length > 14 ? `${value.slice(0, 14)}...` : value
}

function safeDetails(details: Record<string, unknown>): string {
  try {
    return JSON.stringify(details, null, 2)
  } catch {
    return '{}'
  }
}

function hasActiveFilters(filters: AuditFilters): boolean {
  return Boolean(filters.questId || filters.instanceId || filters.category)
}

function SummaryCard({
  label,
  value,
  detail,
  icon,
  tone = 'border-border-default bg-surface-card text-text-muted',
}: {
  label: string
  value: string | number
  detail: string
  icon: ReactNode
  tone?: string
}) {
  return (
    <div className={`min-w-0 rounded-lg border p-4 ${tone}`}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider opacity-80">{label}</span>
        <span className="shrink-0">{icon}</span>
      </div>
      <div className="mt-3 truncate text-2xl font-semibold leading-tight text-text-primary" title={String(value)}>
        {value}
      </div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function CategoryPill({
  category,
  count,
  active,
  onClick,
}: {
  category: Category
  count: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center justify-between gap-3 rounded border px-3 py-2 text-xs font-medium transition-colors ${
        active ? EVENT_TONES[category] : 'border-border-default bg-surface-card-elevated text-text-secondary hover:border-border-active'
      }`}
    >
      <span>{CATEGORY_OPTIONS.find((option) => option.value === category)?.label || category}</span>
      <span className="font-mono text-[11px] opacity-80">{count}</span>
    </button>
  )
}

function AuditEntryRow({
  entry,
  onQuestSelect,
}: {
  entry: AuditEntry
  onQuestSelect: (questId: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const category = getCategory(entry.event_type)
  const detailsCount = Object.keys(entry.details || {}).length

  return (
    <article className={`overflow-hidden rounded-r-lg border border-l-4 border-border-default ${LEFT_BORDER_TONES[category]} bg-surface-card`}>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full flex-col gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-card-elevated sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="flex min-w-0 items-start gap-3">
          <span className={`mt-0.5 rounded border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider ${EVENT_TONES[category]}`}>
            {category}
          </span>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium capitalize text-text-primary">
              {readable(entry.event_type)}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
              <span className="font-mono">{entry.instance_id || 'unknown-instance'}</span>
              {entry.federation_quest_id && <span>Quest {shortHash(entry.federation_quest_id)}</span>}
              {entry.risk_tier && <span>{entry.risk_tier}</span>}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-4 pl-0 sm:justify-end sm:pl-3">
          <div className="text-right">
            <div className="text-xs font-medium text-text-secondary">{formatTime(entry.timestamp)}</div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">{formatDate(entry.timestamp)}</div>
          </div>
          <ChevronDown className={`h-4 w-4 text-text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} aria-hidden="true" />
        </div>
      </button>

      {expanded && (
        <div className="space-y-3 border-t border-border-default bg-surface-card/70 px-4 py-3">
          <div className="grid gap-2 text-xs text-text-secondary sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded border border-border-default bg-surface-card-elevated p-2">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Entry</div>
              <div className="mt-1 truncate font-mono text-text-primary" title={entry.entry_id}>{entry.entry_id}</div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated p-2">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Quest</div>
              <div className="mt-1 truncate font-mono text-text-primary" title={entry.federation_quest_id || ''}>
                {entry.federation_quest_id || 'None'}
              </div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated p-2">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Soul Hash</div>
              <div className="mt-1 truncate font-mono text-text-primary" title={entry.soul_version_hash || ''}>
                {shortHash(entry.soul_version_hash)}
              </div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated p-2">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Related</div>
              <div className="mt-1 text-text-primary">{entry.related_entry_ids?.length || 0} entries</div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {entry.federation_quest_id && (
              <button
                type="button"
                onClick={() => onQuestSelect(entry.federation_quest_id)}
                className="inline-flex items-center gap-2 rounded border border-accent-primary/30 bg-accent-primary/10 px-3 py-1.5 text-xs font-medium text-accent-primary hover:border-accent-primary"
              >
                <GitBranch className="h-3.5 w-3.5" aria-hidden="true" />
                Reconstruct Quest
              </button>
            )}
          </div>

          {detailsCount > 0 ? (
            <pre className="max-h-80 overflow-auto rounded border border-border-default bg-surface-input p-3 text-[11px] leading-5 text-text-secondary">
              {safeDetails(entry.details)}
            </pre>
          ) : (
            <div className="rounded border border-border-default bg-surface-input p-3 text-xs text-text-muted">
              No additional detail payload.
            </div>
          )}
        </div>
      )}
    </article>
  )
}

function TimelinePanel({
  timeline,
  onClose,
  onQuestSelect,
}: {
  timeline: ForensicTimeline
  onClose: () => void
  onQuestSelect: (questId: string) => void
}) {
  return (
    <section className="rounded-lg border border-accent-primary/30 bg-accent-primary/5 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <FileSearch className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-text-primary">Forensic Timeline</h2>
          </div>
          <p className="mt-1 break-all text-xs text-text-muted">{timeline.quest_id}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="inline-flex items-center gap-2 self-start rounded border border-border-default bg-surface-card px-3 py-2 text-xs font-medium text-text-secondary hover:border-border-active hover:text-text-primary"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
          Close
        </button>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard label="Entries" value={timeline.total_entries} detail="Events in this quest." icon={<History className="h-4 w-4" />} />
        <SummaryCard label="Instances" value={timeline.instances_involved} detail="Participating peers." icon={<Network className="h-4 w-4" />} />
        <SummaryCard
          label="Contradictions"
          value={timeline.contradictions_found}
          detail="Conflicting evidence found."
          icon={<AlertTriangle className="h-4 w-4" />}
          tone={timeline.contradictions_found > 0 ? EVENT_TONES.CONTRADICTION : EVENT_TONES.COST}
        />
        <SummaryCard
          label="Window"
          value={timeline.start_time && timeline.end_time ? `${formatTime(timeline.start_time)} to ${formatTime(timeline.end_time)}` : 'N/A'}
          detail="Observed start and end."
          icon={<Clock className="h-4 w-4" />}
        />
      </div>

      <div className="mt-4 max-h-[28rem] space-y-2 overflow-y-auto pr-1">
        {timeline.entries.map((entry) => (
          <AuditEntryRow key={entry.entry_id} entry={entry} onQuestSelect={onQuestSelect} />
        ))}
      </div>
    </section>
  )
}

export function FederationAudit() {
  usePageTitle('Federation Audit')

  const [draftFilters, setDraftFilters] = useState<AuditFilters>({ questId: '', instanceId: '', category: '' })
  const [appliedFilters, setAppliedFilters] = useState<AuditFilters>({ questId: '', instanceId: '', category: '' })
  const [timeline, setTimeline] = useState<ForensicTimeline | null>(null)
  const [timelineError, setTimelineError] = useState('')

  const {
    data: summary,
    error: summaryError,
    loading: summaryLoading,
  } = usePolling<AuditSummary>({
    fetcher: () => apiGet<AuditSummary>('/api/federation/audit/summary'),
    interval: 15000,
  })

  const {
    data: entries,
    error: entriesError,
    loading: entriesLoading,
    refetch,
  } = usePolling<AuditEntry[]>({
    fetcher: async () => {
      const params: Record<string, string> = {}
      if (appliedFilters.questId) params.quest_id = appliedFilters.questId.trim()
      if (appliedFilters.instanceId) params.instance_id = appliedFilters.instanceId.trim()
      const resp = await apiGet<{ entries: AuditEntry[]; total: number }>('/api/federation/audit', params)
      return resp.entries
    },
    interval: 10000,
  })

  const filteredEntries = useMemo(() => {
    return (entries || []).filter((entry) => {
      if (!appliedFilters.category) return true
      return getCategory(entry.event_type) === appliedFilters.category
    })
  }, [entries, appliedFilters.category])

  const categoryCounts = useMemo(() => {
    const counts: Record<Category, number> = {
      GOVERNANCE: 0,
      WORKFLOW: 0,
      HEALTH: 0,
      COST: 0,
      CONTRADICTION: 0,
      SOUL: 0,
      OTHER: 0,
    }
    ;(entries || []).forEach((entry) => {
      counts[getCategory(entry.event_type)] += 1
    })
    return counts
  }, [entries])

  const topEventTypes = useMemo(() => {
    return Object.entries(summary?.event_type_counts || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
  }, [summary])

  const blockingCount = categoryCounts.GOVERNANCE + categoryCounts.CONTRADICTION
  const activeFilterCount = [appliedFilters.questId, appliedFilters.instanceId, appliedFilters.category].filter(Boolean).length

  const applyFilters = () => {
    setAppliedFilters({
      questId: draftFilters.questId.trim(),
      instanceId: draftFilters.instanceId.trim(),
      category: draftFilters.category,
    })
  }

  const clearFilters = () => {
    const empty = { questId: '', instanceId: '', category: '' }
    setDraftFilters(empty)
    setAppliedFilters(empty)
  }

  const reconstructQuest = async (questId: string) => {
    const cleanQuest = questId.trim()
    if (!cleanQuest) return
    setTimelineError('')
    try {
      const result = await apiGet<ForensicTimeline>(`/api/federation/audit/quest/${encodeURIComponent(cleanQuest)}`)
      setTimeline(result)
      setDraftFilters((filters) => ({ ...filters, questId: cleanQuest }))
      setAppliedFilters((filters) => ({ ...filters, questId: cleanQuest }))
    } catch (error) {
      setTimeline(null)
      setTimelineError(getErrorMessage(error, 'Failed to reconstruct quest timeline'))
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-accent-primary">
              <Shield className="h-4 w-4" aria-hidden="true" />
              Federation Evidence
            </div>
            <h1 className="mt-2 text-2xl font-semibold text-text-primary">Federation Audit Trail</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">
              Review cross-instance events, isolate quests, and reconstruct governed federation activity from recorded audit evidence.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-3 xl:min-w-[28rem]">
            <div className="rounded border border-border-default bg-surface-card-elevated p-3">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Loaded</div>
              <div className="mt-1 text-lg font-semibold text-text-primary">{filteredEntries.length}</div>
            </div>
            <div className={`rounded border p-3 ${blockingCount > 0 ? EVENT_TONES.GOVERNANCE : EVENT_TONES.COST}`}>
              <div className="text-[10px] uppercase tracking-wider opacity-80">Attention</div>
              <div className="mt-1 text-lg font-semibold text-text-primary">{blockingCount}</div>
            </div>
            <div className="rounded border border-border-default bg-surface-card-elevated p-3">
              <div className="text-[10px] uppercase tracking-wider text-text-muted">Filters</div>
              <div className="mt-1 text-lg font-semibold text-text-primary">{activeFilterCount}</div>
            </div>
          </div>
        </div>
      </section>

      {summaryError && (
        <div className="rounded-lg border border-state-error/40 bg-state-error/10 p-3 text-sm text-state-error">
          {getErrorMessage(summaryError, 'Unable to load audit summary')}
        </div>
      )}

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCard
          label="Total Events"
          value={summary?.total_entries ?? (summaryLoading ? '...' : 0)}
          detail="All recorded federation audit entries."
          icon={<History className="h-4 w-4" />}
          tone="border-accent-primary/30 bg-accent-primary/10 text-accent-primary"
        />
        <SummaryCard
          label="Quests"
          value={summary?.unique_quests ?? (summaryLoading ? '...' : 0)}
          detail="Distinct quest identifiers in the trail."
          icon={<GitBranch className="h-4 w-4" />}
        />
        <SummaryCard
          label="Instances"
          value={summary?.unique_instances ?? (summaryLoading ? '...' : 0)}
          detail="Peers represented in evidence."
          icon={<Network className="h-4 w-4" />}
        />
        <SummaryCard
          label="Event Types"
          value={summary ? Object.keys(summary.event_type_counts).length : summaryLoading ? '...' : 0}
          detail="Kinds of federation events observed."
          icon={<Filter className="h-4 w-4" />}
        />
      </section>

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Search Audit Evidence</h2>
            <p className="mt-1 text-xs leading-5 text-text-secondary">
              Apply filters explicitly so longer audit queries do not reload on every keystroke.
            </p>
          </div>
          <button
            type="button"
            onClick={refetch}
            className="inline-flex items-center gap-2 self-start rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs font-medium text-text-secondary hover:border-border-active hover:text-text-primary xl:self-auto"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            Refresh
          </button>
        </div>

        <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_12rem_auto]">
          <input
            placeholder="Quest ID"
            value={draftFilters.questId}
            onChange={(event) => setDraftFilters((filters) => ({ ...filters, questId: event.target.value }))}
            className="min-w-0 rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
          />
          <input
            placeholder="Instance ID"
            value={draftFilters.instanceId}
            onChange={(event) => setDraftFilters((filters) => ({ ...filters, instanceId: event.target.value }))}
            className="min-w-0 rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
          />
          <select
            value={draftFilters.category}
            onChange={(event) => setDraftFilters((filters) => ({ ...filters, category: event.target.value }))}
            className="rounded border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary"
          >
            <option value="">All categories</option>
            {CATEGORY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={applyFilters}
              className="inline-flex items-center gap-2 rounded border border-accent-primary/40 bg-accent-primary/15 px-3 py-2 text-xs font-medium text-accent-primary hover:border-accent-primary"
            >
              <Search className="h-3.5 w-3.5" aria-hidden="true" />
              Search
            </button>
            {hasActiveFilters(draftFilters) || hasActiveFilters(appliedFilters) ? (
              <button
                type="button"
                onClick={clearFilters}
                className="inline-flex items-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs font-medium text-text-secondary hover:border-border-active hover:text-text-primary"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
                Clear
              </button>
            ) : null}
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {CATEGORY_OPTIONS.map((option) => (
            <CategoryPill
              key={option.value}
              category={option.value}
              count={categoryCounts[option.value]}
              active={appliedFilters.category === option.value}
              onClick={() => {
                const nextCategory = appliedFilters.category === option.value ? '' : option.value
                setDraftFilters((filters) => ({ ...filters, category: nextCategory }))
                setAppliedFilters((filters) => ({ ...filters, category: nextCategory }))
              }}
            />
          ))}
        </div>

        {topEventTypes.length > 0 && (
          <div className="mt-4 rounded border border-border-default bg-surface-card-elevated p-3">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Most Common Event Types</div>
            <div className="flex flex-wrap gap-2">
              {topEventTypes.map(([eventType, count]) => (
                <span key={eventType} className="rounded border border-border-default bg-surface-input px-2 py-1 text-xs text-text-secondary">
                  <span className="capitalize text-text-primary">{readable(eventType)}</span>
                  <span className="ml-2 font-mono text-text-muted">{count}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      {timelineError && (
        <div className="rounded-lg border border-state-error/40 bg-state-error/10 p-3 text-sm text-state-error">
          {timelineError}
        </div>
      )}

      {appliedFilters.questId && (
        <section className="rounded-lg border border-border-default bg-surface-card p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Quest Focus</div>
              <div className="mt-1 break-all font-mono text-sm text-text-primary">{appliedFilters.questId}</div>
            </div>
            <button
              type="button"
              onClick={() => reconstructQuest(appliedFilters.questId)}
              className="inline-flex items-center justify-center gap-2 rounded border border-accent-primary/40 bg-accent-primary/15 px-3 py-2 text-xs font-medium text-accent-primary hover:border-accent-primary"
            >
              <FileSearch className="h-3.5 w-3.5" aria-hidden="true" />
              Reconstruct Quest
            </button>
          </div>
        </section>
      )}

      {timeline && (
        <TimelinePanel
          timeline={timeline}
          onClose={() => setTimeline(null)}
          onQuestSelect={reconstructQuest}
        />
      )}

      {entriesError && (
        <div className="rounded-lg border border-state-error/40 bg-state-error/10 p-3 text-sm text-state-error">
          {getErrorMessage(entriesError, 'Unable to load audit entries')}
        </div>
      )}

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text-primary">Event Feed</h2>
            <p className="mt-1 text-xs text-text-secondary">
              {entriesLoading ? 'Loading audit entries...' : `${filteredEntries.length} entries shown`}
            </p>
          </div>
          {blockingCount === 0 ? (
            <span className="inline-flex items-center gap-2 self-start rounded border border-state-healthy/30 bg-state-healthy/10 px-3 py-1.5 text-xs font-medium text-state-healthy">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
              No blocking categories loaded
            </span>
          ) : (
            <span className="inline-flex items-center gap-2 self-start rounded border border-state-error/30 bg-state-error/10 px-3 py-1.5 text-xs font-medium text-state-error">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
              {blockingCount} attention events
            </span>
          )}
        </div>

        {filteredEntries.length === 0 ? (
          <div className="rounded-lg border border-border-default bg-surface-card-elevated p-8 text-center">
            <div className="text-sm font-medium text-text-primary">No audit entries found</div>
            <div className="mt-1 text-xs leading-5 text-text-muted">Clear filters or broaden the query window.</div>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredEntries.map((entry) => (
              <AuditEntryRow key={entry.entry_id} entry={entry} onQuestSelect={reconstructQuest} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
