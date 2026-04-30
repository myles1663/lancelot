import { useState } from 'react'
import { usePolling } from '@/hooks/usePolling'
import { apiGet } from '@/api/client'
import type {
  AuditEntry,
  AuditSummary,
  ForensicTimeline,
} from '@/api/federation'

// ── Event category colors ────────────────────────────────────

const EVENT_COLORS: Record<string, string> = {
  handoff_initiated: 'border-l-blue-500',
  handoff_received: 'border-l-blue-500',
  handoff_completed: 'border-l-blue-500',
  handoff_rejected: 'border-l-blue-500',
  soul_push: 'border-l-purple-500',
  soul_activated: 'border-l-purple-500',
  kill_issued: 'border-l-red-500',
  kill_acknowledged: 'border-l-red-500',
  divergence_detected: 'border-l-amber-500',
  reconciliation_completed: 'border-l-amber-500',
  contradiction_detected: 'border-l-orange-500',
  cost_threshold_crossed: 'border-l-green-500',
  peer_registered: 'border-l-amber-500',
  peer_removed: 'border-l-amber-500',
}

const CATEGORY_LABELS: Record<string, string> = {
  handoff: 'WORKFLOW',
  soul: 'SOUL',
  kill: 'GOVERNANCE',
  divergence: 'HEALTH',
  reconciliation: 'HEALTH',
  contradiction: 'CONTRADICTION',
  cost: 'COST',
  peer: 'HEALTH',
}

function getCategoryLabel(eventType: string): string {
  for (const [prefix, label] of Object.entries(CATEGORY_LABELS)) {
    if (eventType.startsWith(prefix)) return label
  }
  return 'OTHER'
}

function getCategoryBadgeColor(category: string): string {
  switch (category) {
    case 'GOVERNANCE': return 'bg-red-500/20 text-red-400'
    case 'WORKFLOW': return 'bg-blue-500/20 text-blue-400'
    case 'HEALTH': return 'bg-amber-500/20 text-amber-400'
    case 'COST': return 'bg-green-500/20 text-green-400'
    case 'CONTRADICTION': return 'bg-orange-500/20 text-orange-400'
    case 'SOUL': return 'bg-purple-500/20 text-purple-400'
    default: return 'bg-surface-input text-text-muted'
  }
}

// ── Event Entry ──────────────────────────────────────────────

function AuditEntryRow({ entry }: { entry: AuditEntry }) {
  const [expanded, setExpanded] = useState(false)
  const category = getCategoryLabel(entry.event_type)
  const borderColor = EVENT_COLORS[entry.event_type] || 'border-l-gray-500'

  return (
    <div className={`border-l-2 ${borderColor} bg-surface-card rounded-r-lg overflow-hidden`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-surface-card-elevated transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${getCategoryBadgeColor(category)}`}>
            {category}
          </span>
          <span className="text-sm text-text-primary">
            {entry.event_type.replace(/_/g, ' ')}
          </span>
          <span className="text-xs text-text-muted">{entry.instance_id}</span>
        </div>
        <div className="flex items-center gap-3">
          {entry.risk_tier && (
            <span className="text-[10px] font-mono text-text-muted">{entry.risk_tier}</span>
          )}
          <span className="text-xs text-text-muted">
            {new Date(entry.timestamp).toLocaleTimeString()}
          </span>
        </div>
      </button>
      {expanded && (
        <div className="px-4 pb-3 text-xs space-y-1 border-t border-border-default pt-2">
          <div className="text-text-muted">Entry: <span className="font-mono">{entry.entry_id}</span></div>
          {entry.federation_quest_id && (
            <div className="text-text-muted">Quest: <span className="font-mono">{entry.federation_quest_id}</span></div>
          )}
          {entry.soul_version_hash && (
            <div className="text-text-muted">Soul: <span className="font-mono">{entry.soul_version_hash}</span></div>
          )}
          {Object.keys(entry.details).length > 0 && (
            <pre className="mt-1 p-2 bg-surface-input rounded text-[10px] text-text-secondary overflow-x-auto">
              {JSON.stringify(entry.details, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────

export function FederationAudit() {
  const [questFilter, setQuestFilter] = useState('')
  const [instanceFilter, setInstanceFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')

  const { data: summary } = usePolling<AuditSummary>({
    fetcher: () => apiGet<AuditSummary>('/api/federation/audit/summary'),
    interval: 15000,
  })

  const { data: entries } = usePolling<AuditEntry[]>({
    fetcher: async () => {
      const params: Record<string, string> = {}
      if (questFilter) params.quest_id = questFilter
      if (instanceFilter) params.instance_id = instanceFilter
      const resp = await apiGet<{ entries: AuditEntry[]; total: number }>('/api/federation/audit', params)
      return resp.entries
    },
    interval: 10000,
  })

  const [timeline, setTimeline] = useState<ForensicTimeline | null>(null)

  const handleReconstructQuest = async () => {
    if (!questFilter) return
    try {
      const result = await apiGet<ForensicTimeline>(`/api/federation/audit/quest/${questFilter}`)
      setTimeline(result)
    } catch { /* ignore */ }
  }

  // Filter entries by category client-side
  const filtered = (entries || []).filter(e => {
    if (!categoryFilter) return true
    return getCategoryLabel(e.event_type) === categoryFilter
  })

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-text-primary">Federation Audit</h1>
        <p className="text-sm text-text-secondary mt-0.5">
          Cross-instance event trail and quest reconstruction
        </p>
      </div>

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="bg-surface-card border border-border-default rounded-lg p-3">
            <div className="text-lg font-bold text-text-primary">{summary.total_entries}</div>
            <div className="text-[10px] text-text-muted uppercase tracking-wider">Total Events</div>
          </div>
          <div className="bg-surface-card border border-border-default rounded-lg p-3">
            <div className="text-lg font-bold text-text-primary">{summary.unique_quests}</div>
            <div className="text-[10px] text-text-muted uppercase tracking-wider">Quests</div>
          </div>
          <div className="bg-surface-card border border-border-default rounded-lg p-3">
            <div className="text-lg font-bold text-text-primary">{summary.unique_instances}</div>
            <div className="text-[10px] text-text-muted uppercase tracking-wider">Instances</div>
          </div>
          <div className="bg-surface-card border border-border-default rounded-lg p-3">
            <div className="text-lg font-bold text-text-primary">
              {Object.keys(summary.event_type_counts).length}
            </div>
            <div className="text-[10px] text-text-muted uppercase tracking-wider">Event Types</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3">
        <input
          placeholder="Filter by Quest ID..."
          value={questFilter}
          onChange={e => setQuestFilter(e.target.value)}
          className="flex-1 px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
        <input
          placeholder="Instance ID..."
          value={instanceFilter}
          onChange={e => setInstanceFilter(e.target.value)}
          className="w-40 px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        />
        <select
          value={categoryFilter}
          onChange={e => setCategoryFilter(e.target.value)}
          className="px-3 py-1.5 text-sm bg-surface-input border border-border-default rounded text-text-primary"
        >
          <option value="">All Categories</option>
          <option value="GOVERNANCE">Governance</option>
          <option value="WORKFLOW">Workflow</option>
          <option value="HEALTH">Health</option>
          <option value="COST">Cost</option>
          <option value="SOUL">Soul</option>
          <option value="CONTRADICTION">Contradiction</option>
        </select>
        {questFilter && (
          <button onClick={handleReconstructQuest}
            className="px-3 py-1.5 text-xs font-medium bg-accent-primary/20 text-accent-primary rounded hover:bg-accent-primary/30">
            Reconstruct Quest
          </button>
        )}
      </div>

      {/* Forensic Timeline */}
      {timeline && (
        <div className="bg-surface-card border border-accent-primary/30 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-accent-primary">
              Quest Timeline: {timeline.quest_id}
            </h3>
            <button onClick={() => setTimeline(null)} className="text-xs text-text-muted hover:text-text-primary">
              Close
            </button>
          </div>
          <div className="grid grid-cols-4 gap-3 mb-3 text-xs text-text-secondary">
            <div>Entries: <span className="text-text-primary font-medium">{timeline.total_entries}</span></div>
            <div>Instances: <span className="text-text-primary font-medium">{timeline.instances_involved}</span></div>
            <div>Contradictions: <span className={`font-medium ${timeline.contradictions_found > 0 ? 'text-state-error' : 'text-text-primary'}`}>
              {timeline.contradictions_found}
            </span></div>
            <div>Duration: <span className="text-text-primary font-medium">
              {timeline.start_time && timeline.end_time
                ? `${new Date(timeline.start_time).toLocaleTimeString()} — ${new Date(timeline.end_time).toLocaleTimeString()}`
                : 'N/A'}
            </span></div>
          </div>
          <div className="space-y-1 max-h-60 overflow-y-auto">
            {timeline.entries.map(e => (
              <AuditEntryRow key={e.entry_id} entry={e} />
            ))}
          </div>
        </div>
      )}

      {/* Event Feed */}
      <div className="space-y-1.5">
        {filtered.length === 0 ? (
          <div className="bg-surface-card border border-border-default rounded-lg p-8 text-center text-text-muted text-sm">
            No audit entries found
          </div>
        ) : (
          filtered.map(entry => (
            <AuditEntryRow key={entry.entry_id} entry={entry} />
          ))
        )}
      </div>
    </div>
  )
}
