import { useState, useCallback } from 'react'
import { usePolling, usePageTitle } from '@/hooks'
import { MetricCard, EmptyState } from '@/components'
import { formatRelativeTime, formatTimestamp } from '@/utils/dateFormat'
import {
  fetchIncidents,
  fetchIncident,
  fetchIncidentStats,
  acknowledgeIncident,
  addTimelineEntry,
  escalateIncident,
  closeIncident,
  generateReport,
  getReportDownloadUrl,
  fetchPlaybook,
} from '@/api/incidents'
import type {
  IncidentSummary,
  IncidentDetail,
  IncidentStats,
  PlaybookStep,
} from '@/api/incidents'

// ── Constants ─────────────────────────────────────────────────

const OPERATOR_ID = 'war-room-operator'

const SEVERITY_CLASSES: Record<string, string> = {
  CRITICAL: 'text-red-400 bg-red-400/10',
  HIGH: 'text-amber-400 bg-amber-400/10',
  MEDIUM: 'text-blue-400 bg-blue-400/10',
  LOW: 'text-gray-400 bg-gray-400/10',
}

const STATUS_CLASSES: Record<string, string> = {
  OPEN: 'text-red-400 bg-red-400/10',
  INVESTIGATING: 'text-amber-400 bg-amber-400/10',
  CONTAINED: 'text-blue-400 bg-blue-400/10',
  REMEDIATING: 'text-purple-400 bg-purple-400/10',
  CLOSED: 'text-green-400 bg-green-400/10',
  FALSE_POSITIVE: 'text-gray-400 bg-gray-400/10',
}

const STATUS_OPTIONS = [
  'All',
  'OPEN',
  'INVESTIGATING',
  'CONTAINED',
  'REMEDIATING',
  'CLOSED',
  'FALSE_POSITIVE',
] as const

const SEVERITY_OPTIONS = ['All', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const

type SortField = 'severity' | 'category' | 'playbook_name' | 'status' | 'opened_at'
type SortDir = 'asc' | 'desc'

const SEVERITY_ORDER: Record<string, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
}

// ── Helpers ───────────────────────────────────────────────────

function severityBadge(severity: string) {
  const cls = SEVERITY_CLASSES[severity] ?? SEVERITY_CLASSES.LOW
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {severity}
    </span>
  )
}

function statusBadge(status: string) {
  const cls = STATUS_CLASSES[status] ?? STATUS_CLASSES.OPEN
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {status}
    </span>
  )
}

function sortIncidents(
  items: IncidentSummary[],
  field: SortField,
  dir: SortDir,
): IncidentSummary[] {
  const sorted = [...items].sort((a, b) => {
    if (field === 'severity') {
      return (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
    }
    if (field === 'opened_at') {
      return new Date(a.opened_at).getTime() - new Date(b.opened_at).getTime()
    }
    const av = (a as never)[field] as string
    const bv = (b as never)[field] as string
    return (av ?? '').localeCompare(bv ?? '')
  })
  return dir === 'desc' ? sorted.reverse() : sorted
}

// ── Component ─────────────────────────────────────────────────

export default function IncidentsDashboard() {
  usePageTitle('Incidents')

  // ── Filters ───────────────────────────────────────────────
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [severityFilter, setSeverityFilter] = useState<string>('')

  // ── Sort ──────────────────────────────────────────────────
  const [sortField, setSortField] = useState<SortField>('opened_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // ── Detail panel ──────────────────────────────────────────
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<IncidentDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [playbookSteps, setPlaybookSteps] = useState<PlaybookStep[]>([])
  const [checkedSteps, setCheckedSteps] = useState<Set<number>>(new Set())

  // ── Close dialog ──────────────────────────────────────────
  const [showCloseDialog, setShowCloseDialog] = useState(false)
  const [rootCause, setRootCause] = useState('')
  const [falsePositive, setFalsePositive] = useState(false)
  const [fpReason, setFpReason] = useState('')
  const [genReport, setGenReport] = useState(false)

  // ── Timeline note ─────────────────────────────────────────
  const [noteText, setNoteText] = useState('')

  // ── Escalate dialog ───────────────────────────────────────
  const [showEscalate, setShowEscalate] = useState(false)
  const [escalateSeverity, setEscalateSeverity] = useState('CRITICAL')
  const [escalateReason, setEscalateReason] = useState('')

  // ── Polling ───────────────────────────────────────────────
  const { data: statsData } = usePolling<IncidentStats>({
    fetcher: fetchIncidentStats,
    interval: 10_000,
  })

  const { data: listData, refetch } = usePolling<{ incidents: IncidentSummary[]; count: number }>({
    fetcher: () =>
      fetchIncidents({
        status: statusFilter || undefined,
        category: categoryFilter || undefined,
        severity: severityFilter || undefined,
      }),
    interval: 10_000,
  })

  const incidents = listData?.incidents ?? []
  const stats = statsData

  // ── Derived values ────────────────────────────────────────
  const categories = Array.from(new Set(incidents.map((i) => i.category))).sort()
  const sortedIncidents = sortIncidents(incidents, sortField, sortDir)

  // ── Handlers ──────────────────────────────────────────────

  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
      } else {
        setSortField(field)
        setSortDir('asc')
      }
    },
    [sortField],
  )

  const selectIncident = useCallback(async (id: string) => {
    setSelectedId(id)
    setDetailLoading(true)
    setPlaybookSteps([])
    setCheckedSteps(new Set())
    setNoteText('')
    try {
      const d = await fetchIncident(id)
      setDetail(d)
      // Load playbook steps
      if (d.playbook_name) {
        try {
          const pb = await fetchPlaybook(d.playbook_name)
          setPlaybookSteps(pb.steps)
        } catch {
          // playbook may not exist
        }
      }
    } catch {
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleAcknowledge = useCallback(async () => {
    if (!detail) return
    await acknowledgeIncident(detail.incident_id, OPERATOR_ID)
    await selectIncident(detail.incident_id)
    refetch()
  }, [detail, selectIncident, refetch])

  const handleAddNote = useCallback(async () => {
    if (!detail || !noteText.trim()) return
    await addTimelineEntry(detail.incident_id, OPERATOR_ID, noteText.trim())
    setNoteText('')
    await selectIncident(detail.incident_id)
  }, [detail, noteText, selectIncident])

  const handleEscalate = useCallback(async () => {
    if (!detail || !escalateReason.trim()) return
    await escalateIncident(detail.incident_id, OPERATOR_ID, escalateSeverity, escalateReason.trim())
    setShowEscalate(false)
    setEscalateReason('')
    await selectIncident(detail.incident_id)
    refetch()
  }, [detail, escalateSeverity, escalateReason, selectIncident, refetch])

  const handleClose = useCallback(async () => {
    if (!detail) return
    const needsRootCause =
      (detail.severity === 'CRITICAL' || detail.severity === 'HIGH') && !falsePositive
    if (needsRootCause && !rootCause.trim()) return
    await closeIncident(
      detail.incident_id,
      OPERATOR_ID,
      rootCause || undefined,
      falsePositive,
      fpReason || undefined,
      genReport,
    )
    setShowCloseDialog(false)
    setRootCause('')
    setFalsePositive(false)
    setFpReason('')
    setGenReport(false)
    await selectIncident(detail.incident_id)
    refetch()
  }, [detail, rootCause, falsePositive, fpReason, genReport, selectIncident, refetch])

  const handleGenerateReport = useCallback(async () => {
    if (!detail) return
    await generateReport(detail.incident_id)
    await selectIncident(detail.incident_id)
  }, [detail, selectIncident])

  const handleDownloadReport = useCallback(() => {
    if (!detail) return
    window.open(getReportDownloadUrl(detail.incident_id), '_blank')
  }, [detail])

  const toggleStep = useCallback((step: number) => {
    setCheckedSteps((prev) => {
      const next = new Set(prev)
      if (next.has(step)) next.delete(step)
      else next.add(step)
      return next
    })
  }, [])

  const sortArrow = (field: SortField) =>
    sortField === field ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''

  // ── Render ────────────────────────────────────────────────

  return (
    <div>
      <h2 className="text-lg font-semibold text-text-primary mb-6">Incident Response</h2>

      {/* ── Stats Bar ────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard
          label="Total Open"
          value={stats?.open ?? '--'}
          className={stats && stats.open > 0 ? 'border-red-500/50' : ''}
        />
        <MetricCard
          label="Critical"
          value={stats?.by_severity?.CRITICAL ?? 0}
          className={
            stats && (stats.by_severity?.CRITICAL ?? 0) > 0 ? 'border-red-500/50' : ''
          }
        />
        <MetricCard
          label="High"
          value={stats?.by_severity?.HIGH ?? 0}
          className={
            stats && (stats.by_severity?.HIGH ?? 0) > 0 ? 'border-amber-500/50' : ''
          }
        />
        <MetricCard
          label="Medium"
          value={stats?.by_severity?.MEDIUM ?? 0}
          className={
            stats && (stats.by_severity?.MEDIUM ?? 0) > 0 ? 'border-blue-500/50' : ''
          }
        />
      </div>

      {/* ── Filters Row ──────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-1.5 text-sm text-text-primary"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s === 'All' ? '' : s}>
              {s === 'All' ? 'All Statuses' : s}
            </option>
          ))}
        </select>

        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-1.5 text-sm text-text-primary"
        >
          <option value="">All Categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="bg-surface-input border border-border-default rounded-md px-3 py-1.5 text-sm text-text-primary"
        >
          {SEVERITY_OPTIONS.map((s) => (
            <option key={s} value={s === 'All' ? '' : s}>
              {s === 'All' ? 'All Severities' : s}
            </option>
          ))}
        </select>
      </div>

      {/* ── Main Content: Table + Detail Panel ───────────────── */}
      <div className={`grid gap-6 ${selectedId ? 'grid-cols-1 lg:grid-cols-2' : 'grid-cols-1'}`}>
        {/* ── Incidents Table ───────────────────────────────── */}
        <section className="bg-surface-card border border-border-default rounded-lg p-4">
          <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider mb-3">
            Incidents ({incidents.length})
          </h3>
          {incidents.length === 0 ? (
            <EmptyState
              title="No Incidents"
              description="No incidents match the current filters."
            />
          ) : (
            <div className="overflow-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted uppercase tracking-wider border-b border-border-default">
                    <th
                      className="px-3 py-2 text-left cursor-pointer select-none"
                      onClick={() => handleSort('severity')}
                    >
                      Severity{sortArrow('severity')}
                    </th>
                    <th
                      className="px-3 py-2 text-left cursor-pointer select-none"
                      onClick={() => handleSort('category')}
                    >
                      Category{sortArrow('category')}
                    </th>
                    <th
                      className="px-3 py-2 text-left cursor-pointer select-none"
                      onClick={() => handleSort('playbook_name')}
                    >
                      Playbook{sortArrow('playbook_name')}
                    </th>
                    <th
                      className="px-3 py-2 text-left cursor-pointer select-none"
                      onClick={() => handleSort('status')}
                    >
                      Status{sortArrow('status')}
                    </th>
                    <th
                      className="px-3 py-2 text-left cursor-pointer select-none"
                      onClick={() => handleSort('opened_at')}
                    >
                      Opened{sortArrow('opened_at')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedIncidents.map((inc) => (
                    <tr
                      key={inc.incident_id}
                      onClick={() => selectIncident(inc.incident_id)}
                      className={`border-b border-border-default cursor-pointer transition-colors hover:bg-surface-card-elevated ${
                        selectedId === inc.incident_id ? 'bg-surface-card-elevated' : ''
                      }`}
                    >
                      <td className="px-3 py-2">{severityBadge(inc.severity)}</td>
                      <td className="px-3 py-2 text-text-primary">{inc.category}</td>
                      <td className="px-3 py-2 text-text-secondary font-mono truncate max-w-[140px]">
                        {inc.playbook_name}
                      </td>
                      <td className="px-3 py-2">{statusBadge(inc.status)}</td>
                      <td className="px-3 py-2 text-text-muted">
                        {formatRelativeTime(inc.opened_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* ── Detail Panel ─────────────────────────────────── */}
        {selectedId && (
          <section className="bg-surface-card border border-border-default rounded-lg p-4 space-y-5 overflow-auto max-h-[80vh]">
            {detailLoading ? (
              <p className="text-sm text-text-muted">Loading incident...</p>
            ) : !detail ? (
              <p className="text-sm text-text-muted">Failed to load incident.</p>
            ) : (
              <>
                {/* ── Header ─────────────────────────────── */}
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-sm font-semibold text-text-primary font-mono">
                      {detail.incident_id}
                    </h3>
                    {severityBadge(detail.severity)}
                    {statusBadge(detail.status)}
                  </div>
                  <p className="text-xs text-text-secondary mt-1">
                    {detail.category} &middot; {detail.playbook_name}
                  </p>
                  <p className="text-xs text-text-muted mt-0.5">
                    Opened {formatTimestamp(detail.opened_at)}
                    {detail.acknowledged_at && (
                      <> &middot; Acknowledged {formatRelativeTime(detail.acknowledged_at)}</>
                    )}
                  </p>
                </div>

                {/* ── Action Buttons ─────────────────────── */}
                <div className="flex flex-wrap gap-2">
                  {detail.status === 'OPEN' && !detail.acknowledged_at && (
                    <button
                      onClick={handleAcknowledge}
                      className="px-3 py-1.5 text-xs font-medium bg-accent-primary hover:bg-accent-primary/80 text-white rounded-md transition-colors"
                    >
                      Acknowledge
                    </button>
                  )}
                  {detail.status !== 'CLOSED' && detail.status !== 'FALSE_POSITIVE' && (
                    <>
                      <button
                        onClick={() => setShowEscalate(true)}
                        className="px-3 py-1.5 text-xs font-medium bg-amber-600 hover:bg-amber-700 text-white rounded-md transition-colors"
                      >
                        Escalate
                      </button>
                      <button
                        onClick={() => setShowCloseDialog(true)}
                        className="px-3 py-1.5 text-xs font-medium bg-state-error/80 hover:bg-state-error text-white rounded-md transition-colors"
                      >
                        Close
                      </button>
                    </>
                  )}
                  {detail.board_report_generated ? (
                    <button
                      onClick={handleDownloadReport}
                      className="px-3 py-1.5 text-xs font-medium bg-green-700 hover:bg-green-600 text-white rounded-md transition-colors"
                    >
                      Download Report
                    </button>
                  ) : (
                    <button
                      onClick={handleGenerateReport}
                      className="px-3 py-1.5 text-xs font-medium bg-surface-input border border-border-default text-text-secondary hover:bg-surface-card-elevated rounded-md transition-colors"
                    >
                      Generate Report
                    </button>
                  )}
                </div>

                {/* ── Playbook Steps ─────────────────────── */}
                {playbookSteps.length > 0 && (
                  <div>
                    <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-2">
                      Playbook Steps
                    </h4>
                    <div className="space-y-1.5">
                      {playbookSteps.map((step) => (
                        <label
                          key={step.step}
                          className="flex items-start gap-2 p-2 bg-surface-card-elevated rounded-md cursor-pointer hover:bg-surface-input transition-colors"
                        >
                          <input
                            type="checkbox"
                            checked={checkedSteps.has(step.step)}
                            onChange={() => toggleStep(step.step)}
                            className="mt-0.5 accent-accent-primary"
                          />
                          <div className="min-w-0">
                            <span className="text-xs text-text-primary font-medium">
                              {step.step}. {step.title}
                            </span>
                            <p className="text-[11px] text-text-muted mt-0.5">
                              {step.description}
                            </p>
                            {step.sla_minutes > 0 && (
                              <span className="text-[10px] text-text-muted">
                                SLA: {step.sla_minutes}m
                              </span>
                            )}
                          </div>
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                {/* ── Timeline ───────────────────────────── */}
                <div>
                  <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-2">
                    Timeline ({detail.timeline.length})
                  </h4>
                  {detail.timeline.length === 0 ? (
                    <p className="text-xs text-text-muted">No timeline entries yet.</p>
                  ) : (
                    <div className="space-y-2 max-h-60 overflow-auto">
                      {detail.timeline.map((entry, i) => (
                        <div
                          key={i}
                          className="flex items-start gap-2 text-xs p-2 bg-surface-card-elevated rounded-md"
                        >
                          <span className="text-text-muted font-mono shrink-0 w-24">
                            {formatRelativeTime(entry.timestamp)}
                          </span>
                          <span className="text-text-secondary shrink-0 font-medium">
                            [{entry.entry_type}]
                          </span>
                          <span className="text-text-primary min-w-0 break-words">
                            {entry.detail}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* ── Remediation Receipts ────────────────── */}
                {detail.remediation_receipts.length > 0 && (
                  <div>
                    <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-2">
                      Remediation Receipts ({detail.remediation_receipts.length})
                    </h4>
                    <div className="space-y-1">
                      {detail.remediation_receipts.map((rid) => (
                        <div
                          key={rid}
                          className="text-xs font-mono text-text-secondary p-2 bg-surface-card-elevated rounded-md truncate"
                          title={rid}
                        >
                          {rid}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* ── Add Note ────────────────────────────── */}
                {detail.status !== 'CLOSED' && detail.status !== 'FALSE_POSITIVE' && (
                  <div>
                    <h4 className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-2">
                      Add Timeline Note
                    </h4>
                    <textarea
                      value={noteText}
                      onChange={(e) => setNoteText(e.target.value)}
                      placeholder="Describe what you observed or did..."
                      rows={3}
                      className="w-full bg-surface-input border border-border-default rounded-md p-2 text-sm text-text-primary placeholder:text-text-muted resize-none focus:outline-none focus:border-accent-primary"
                    />
                    <button
                      onClick={handleAddNote}
                      disabled={!noteText.trim()}
                      className="mt-2 px-3 py-1.5 text-xs font-medium bg-accent-primary hover:bg-accent-primary/80 text-white rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Add Note
                    </button>
                  </div>
                )}

                {/* ── Close Dialog ────────────────────────── */}
                {showCloseDialog && (
                  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
                    <div className="bg-surface-card-elevated border border-border-default rounded-lg p-6 max-w-md w-full shadow-xl">
                      <h3 className="text-lg font-semibold text-text-primary">Close Incident</h3>
                      <p className="text-sm text-text-secondary mt-2">
                        {detail.severity === 'CRITICAL' || detail.severity === 'HIGH'
                          ? 'A root cause is required for HIGH and CRITICAL incidents.'
                          : 'Optionally provide a root cause or mark as false positive.'}
                      </p>

                      <div className="mt-4 space-y-3">
                        <div>
                          <label className="text-xs text-text-secondary block mb-1">
                            Root Cause
                            {(detail.severity === 'CRITICAL' || detail.severity === 'HIGH') &&
                              !falsePositive && (
                                <span className="text-red-400 ml-1">*</span>
                              )}
                          </label>
                          <textarea
                            value={rootCause}
                            onChange={(e) => setRootCause(e.target.value)}
                            rows={3}
                            className="w-full bg-surface-input border border-border-default rounded-md p-2 text-sm text-text-primary resize-none focus:outline-none focus:border-accent-primary"
                          />
                        </div>

                        <label className="flex items-center gap-2 text-sm text-text-primary cursor-pointer">
                          <input
                            type="checkbox"
                            checked={falsePositive}
                            onChange={(e) => setFalsePositive(e.target.checked)}
                            className="accent-accent-primary"
                          />
                          Mark as False Positive
                        </label>

                        {falsePositive && (
                          <div>
                            <label className="text-xs text-text-secondary block mb-1">
                              False Positive Reason
                            </label>
                            <input
                              type="text"
                              value={fpReason}
                              onChange={(e) => setFpReason(e.target.value)}
                              className="w-full bg-surface-input border border-border-default rounded-md px-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-primary"
                            />
                          </div>
                        )}

                        <label className="flex items-center gap-2 text-sm text-text-primary cursor-pointer">
                          <input
                            type="checkbox"
                            checked={genReport}
                            onChange={(e) => setGenReport(e.target.checked)}
                            className="accent-accent-primary"
                          />
                          Generate Board Report
                        </label>
                      </div>

                      <div className="flex justify-end gap-3 mt-6">
                        <button
                          onClick={() => setShowCloseDialog(false)}
                          className="px-4 py-2 text-sm text-text-secondary bg-surface-input border border-border-default rounded-md hover:bg-surface-card transition-colors"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={handleClose}
                          disabled={
                            (detail.severity === 'CRITICAL' || detail.severity === 'HIGH') &&
                            !falsePositive &&
                            !rootCause.trim()
                          }
                          className="px-4 py-2 text-sm font-medium bg-state-error hover:bg-state-error/80 text-white rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          Close Incident
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* ── Escalate Dialog ─────────────────────── */}
                {showEscalate && (
                  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
                    <div className="bg-surface-card-elevated border border-border-default rounded-lg p-6 max-w-md w-full shadow-xl">
                      <h3 className="text-lg font-semibold text-text-primary">
                        Escalate Incident
                      </h3>
                      <p className="text-sm text-text-secondary mt-2">
                        Increase the severity and provide a reason.
                      </p>

                      <div className="mt-4 space-y-3">
                        <div>
                          <label className="text-xs text-text-secondary block mb-1">
                            New Severity
                          </label>
                          <select
                            value={escalateSeverity}
                            onChange={(e) => setEscalateSeverity(e.target.value)}
                            className="bg-surface-input border border-border-default rounded-md px-3 py-1.5 text-sm text-text-primary w-full"
                          >
                            <option value="CRITICAL">CRITICAL</option>
                            <option value="HIGH">HIGH</option>
                            <option value="MEDIUM">MEDIUM</option>
                          </select>
                        </div>
                        <div>
                          <label className="text-xs text-text-secondary block mb-1">
                            Reason <span className="text-red-400">*</span>
                          </label>
                          <textarea
                            value={escalateReason}
                            onChange={(e) => setEscalateReason(e.target.value)}
                            rows={3}
                            className="w-full bg-surface-input border border-border-default rounded-md p-2 text-sm text-text-primary resize-none focus:outline-none focus:border-accent-primary"
                          />
                        </div>
                      </div>

                      <div className="flex justify-end gap-3 mt-6">
                        <button
                          onClick={() => setShowEscalate(false)}
                          className="px-4 py-2 text-sm text-text-secondary bg-surface-input border border-border-default rounded-md hover:bg-surface-card transition-colors"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={handleEscalate}
                          disabled={!escalateReason.trim()}
                          className="px-4 py-2 text-sm font-medium bg-amber-600 hover:bg-amber-700 text-white rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          Escalate
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
          </section>
        )}
      </div>
    </div>
  )
}
