import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  ArrowUpDown,
  Bell,
  CheckCircle2,
  ClipboardList,
  Clock,
  FileDown,
  FileText,
  Filter,
  Flag,
  ListChecks,
  MessageSquarePlus,
  ShieldAlert,
  Siren,
  X,
} from 'lucide-react'
import { usePolling, usePageTitle } from '@/hooks'
import { EmptyState } from '@/components'
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

const OPERATOR_ID = 'war-room-operator'

const SEVERITY_CLASSES: Record<string, string> = {
  CRITICAL: 'border-state-error/30 bg-state-error/10 text-state-error',
  HIGH: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  MEDIUM: 'border-blue-400/30 bg-blue-400/10 text-blue-400',
  LOW: 'border-border-default bg-surface-input text-text-muted',
}

const STATUS_CLASSES: Record<string, string> = {
  OPEN: 'border-state-error/30 bg-state-error/10 text-state-error',
  INVESTIGATING: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  CONTAINED: 'border-blue-400/30 bg-blue-400/10 text-blue-400',
  REMEDIATING: 'border-purple-500/30 bg-purple-500/10 text-purple-400',
  CLOSED: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  FALSE_POSITIVE: 'border-border-default bg-surface-input text-text-muted',
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
type IncidentTileTone = 'accent' | 'healthy' | 'warning' | 'error' | 'muted'

const SEVERITY_ORDER: Record<string, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
}

const incidentTileToneClass: Record<IncidentTileTone, string> = {
  accent: 'border-accent-primary/30 bg-accent-primary/10 text-accent-primary',
  healthy: 'border-state-healthy/30 bg-state-healthy/10 text-state-healthy',
  warning: 'border-state-warning/30 bg-state-warning/10 text-state-warning',
  error: 'border-state-error/30 bg-state-error/10 text-state-error',
  muted: 'border-border-default bg-surface-card-elevated text-text-muted',
}

function IncidentTile({
  label,
  value,
  detail,
  tone = 'muted',
}: {
  label: string
  value: string | number
  detail: string
  tone?: IncidentTileTone
}) {
  return (
    <div className={`rounded-lg border px-4 py-3 ${incidentTileToneClass[tone]}`}>
      <div className="text-[10px] font-medium uppercase tracking-wider opacity-80">{label}</div>
      <div className="mt-2 break-words text-2xl font-semibold leading-tight text-text-primary">{value}</div>
      <div className="mt-1 text-xs leading-5 text-text-muted">{detail}</div>
    </div>
  )
}

function severityBadge(severity: string) {
  const cls = SEVERITY_CLASSES[severity] ?? SEVERITY_CLASSES.LOW
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-medium ${cls}`}>
      {severity}
    </span>
  )
}

function statusBadge(status: string) {
  const cls = STATUS_CLASSES[status] ?? STATUS_CLASSES.OPEN
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-medium ${cls}`}>
      {status}
    </span>
  )
}

function sortIncidents(items: IncidentSummary[], field: SortField, dir: SortDir): IncidentSummary[] {
  const sorted = [...items].sort((a, b) => {
    if (field === 'severity') {
      return (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
    }
    if (field === 'opened_at') {
      return new Date(a.opened_at).getTime() - new Date(b.opened_at).getTime()
    }
    const av = a[field] ?? ''
    const bv = b[field] ?? ''
    return String(av).localeCompare(String(bv))
  })
  return dir === 'desc' ? sorted.reverse() : sorted
}

function actionButtonClass(tone: 'primary' | 'warning' | 'danger' | 'muted') {
  if (tone === 'primary') return 'border-accent-primary/40 bg-accent-primary/10 text-accent-primary hover:bg-accent-primary/15'
  if (tone === 'warning') return 'border-state-warning/40 bg-state-warning/10 text-state-warning hover:bg-state-warning/15'
  if (tone === 'danger') return 'border-state-error/40 bg-state-error/10 text-state-error hover:bg-state-error/15'
  return 'border-border-default bg-surface-card-elevated text-text-secondary hover:border-border-active hover:text-text-primary'
}

function IncidentActionButton({
  children,
  onClick,
  tone = 'muted',
  disabled = false,
}: {
  children: React.ReactNode
  onClick: () => void
  tone?: 'primary' | 'warning' | 'danger' | 'muted'
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded border px-3 py-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${actionButtonClass(tone)}`}
    >
      {children}
    </button>
  )
}

function IncidentSummaryCard({
  incident,
  selected,
  onSelect,
}: {
  incident: IncidentSummary
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full px-4 py-4 text-left transition-colors hover:bg-surface-card-elevated ${
        selected ? 'bg-accent-primary/5' : ''
      }`}
    >
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {severityBadge(incident.severity)}
            {statusBadge(incident.status)}
          </div>
          <div className="mt-2 break-words text-sm font-medium text-text-primary">{incident.category}</div>
          <div className="mt-1 truncate font-mono text-xs text-text-muted">{incident.playbook_name}</div>
        </div>
        <span className="shrink-0 text-right text-xs text-text-muted">{formatRelativeTime(incident.opened_at)}</span>
      </div>
    </button>
  )
}

function ModalFrame({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-lg border border-border-default bg-surface-card-elevated p-6 shadow-xl">
        <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
        <p className="mt-2 text-sm leading-6 text-text-secondary">{description}</p>
        {children}
      </div>
    </div>
  )
}

export default function IncidentsDashboard() {
  usePageTitle('Incident Response')

  const [statusFilter, setStatusFilter] = useState<string>('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [severityFilter, setSeverityFilter] = useState<string>('')
  const [sortField, setSortField] = useState<SortField>('opened_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<IncidentDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [playbookSteps, setPlaybookSteps] = useState<PlaybookStep[]>([])
  const [checkedSteps, setCheckedSteps] = useState<Set<number>>(new Set())
  const [showCloseDialog, setShowCloseDialog] = useState(false)
  const [rootCause, setRootCause] = useState('')
  const [falsePositive, setFalsePositive] = useState(false)
  const [fpReason, setFpReason] = useState('')
  const [genReport, setGenReport] = useState(false)
  const [noteText, setNoteText] = useState('')
  const [showEscalate, setShowEscalate] = useState(false)
  const [escalateSeverity, setEscalateSeverity] = useState('CRITICAL')
  const [escalateReason, setEscalateReason] = useState('')

  const { data: statsData } = usePolling<IncidentStats>({
    fetcher: fetchIncidentStats,
    interval: 10_000,
  })

  const listFetcher = useCallback(
    () =>
      fetchIncidents({
        status: statusFilter || undefined,
        category: categoryFilter || undefined,
        severity: severityFilter || undefined,
      }),
    [statusFilter, categoryFilter, severityFilter],
  )

  const { data: listData, refetch } = usePolling<{ incidents: IncidentSummary[]; count: number }>({
    fetcher: listFetcher,
    interval: 10_000,
  })

  useEffect(() => {
    refetch()
  }, [listFetcher, refetch])

  const incidents = listData?.incidents ?? []
  const stats = statsData
  const categories = useMemo(
    () => Array.from(new Set(incidents.map((i) => i.category))).sort(),
    [incidents],
  )
  const sortedIncidents = useMemo(
    () => sortIncidents(incidents, sortField, sortDir),
    [incidents, sortField, sortDir],
  )
  const highPriority = (stats?.by_severity?.CRITICAL ?? 0) + (stats?.by_severity?.HIGH ?? 0)
  const activeFilterCount = [statusFilter, categoryFilter, severityFilter].filter(Boolean).length

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
      if (d.playbook_name) {
        try {
          const pb = await fetchPlaybook(d.playbook_name)
          setPlaybookSteps(pb.steps)
        } catch {
          setPlaybookSteps([])
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
    const needsRootCause = (detail.severity === 'CRITICAL' || detail.severity === 'HIGH') && !falsePositive
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

  const sortLabel = (field: SortField) => (sortField === field ? (sortDir === 'asc' ? 'Asc' : 'Desc') : '')

  const incidentDetailPanel = (
    <div className="border-t border-accent-primary/20 bg-accent-primary/5 p-3 sm:p-4">
      {detailLoading ? (
        <div className="rounded-lg border border-border-default bg-surface-card p-4 text-sm text-text-muted">Loading incident...</div>
      ) : !detail ? (
        <div className="rounded-lg border border-border-default bg-surface-card p-4 text-sm text-text-muted">Failed to load incident.</div>
      ) : (
        <div className="space-y-5 rounded-lg border border-accent-primary/30 bg-surface-card p-4">
          <div className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="break-all font-mono text-sm font-semibold text-text-primary">{detail.incident_id}</span>
                  {severityBadge(detail.severity)}
                  {statusBadge(detail.status)}
                </div>
                <p className="mt-2 text-sm text-text-secondary">
                  {detail.category} / {detail.playbook_name}
                </p>
                <p className="mt-1 text-xs leading-5 text-text-muted">
                  Opened {formatTimestamp(detail.opened_at)}
                  {detail.acknowledged_at ? ` / Acknowledged ${formatRelativeTime(detail.acknowledged_at)}` : ''}
                </p>
              </div>
              <span className="shrink-0 rounded border border-border-default bg-surface-input px-2 py-1 text-xs font-mono text-text-muted">
                {detail.timeline.length} events
              </span>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {detail.status === 'OPEN' && !detail.acknowledged_at && (
              <IncidentActionButton onClick={handleAcknowledge} tone="primary">
                <Bell className="h-3.5 w-3.5" aria-hidden="true" />
                Acknowledge
              </IncidentActionButton>
            )}
            {detail.status !== 'CLOSED' && detail.status !== 'FALSE_POSITIVE' && (
              <>
                <IncidentActionButton onClick={() => setShowEscalate(true)} tone="warning">
                  <Flag className="h-3.5 w-3.5" aria-hidden="true" />
                  Escalate
                </IncidentActionButton>
                <IncidentActionButton onClick={() => setShowCloseDialog(true)} tone="danger">
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                  Close
                </IncidentActionButton>
              </>
            )}
            {detail.board_report_generated ? (
              <IncidentActionButton onClick={handleDownloadReport} tone="primary">
                <FileDown className="h-3.5 w-3.5" aria-hidden="true" />
                Download Report
              </IncidentActionButton>
            ) : (
              <IncidentActionButton onClick={handleGenerateReport}>
                <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                Generate Report
              </IncidentActionButton>
            )}
          </div>

          {detail.status !== 'CLOSED' && detail.status !== 'FALSE_POSITIVE' && (
            <div className="rounded-lg border border-accent-primary/30 bg-surface-card-elevated p-4">
              <div className="mb-3 flex items-center gap-2">
                <MessageSquarePlus className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                <h4 className="text-sm font-semibold text-text-primary">Operator Notes</h4>
              </div>
              <textarea
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder="Add the operator observation, action taken, or handoff note..."
                rows={3}
                className="w-full resize-none rounded-md border border-border-default bg-surface-input p-3 text-sm text-text-primary placeholder:text-text-muted focus:border-accent-primary focus:outline-none"
              />
              <div className="mt-3">
                <IncidentActionButton onClick={handleAddNote} disabled={!noteText.trim()} tone="primary">
                  <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden="true" />
                  Save Note
                </IncidentActionButton>
              </div>
            </div>
          )}

          {playbookSteps.length > 0 && (
            <div className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
              <div className="mb-3 flex items-center gap-2">
                <ListChecks className="h-4 w-4 text-accent-primary" aria-hidden="true" />
                <h4 className="text-sm font-semibold text-text-primary">Playbook Steps</h4>
              </div>
              <div className="space-y-2">
                {playbookSteps.map((step) => (
                  <label
                    key={step.step}
                    className="flex items-start gap-3 rounded border border-border-default bg-surface-card px-3 py-3 transition-colors hover:border-border-active"
                  >
                    <input
                      type="checkbox"
                      checked={checkedSteps.has(step.step)}
                      onChange={() => toggleStep(step.step)}
                      className="mt-0.5 accent-accent-primary"
                    />
                    <div className="min-w-0">
                      <span className="text-sm font-medium text-text-primary">
                        {step.step}. {step.title}
                      </span>
                      <p className="mt-1 text-xs leading-5 text-text-muted">{step.description}</p>
                      {step.sla_minutes > 0 && (
                        <span className="mt-2 inline-flex items-center gap-1 rounded border border-border-default bg-surface-input px-2 py-0.5 text-[10px] text-text-muted">
                          <Clock className="h-3 w-3" aria-hidden="true" />
                          SLA {step.sla_minutes}m
                        </span>
                      )}
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
            <div className="mb-3 flex items-center gap-2">
              <Clock className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <h4 className="text-sm font-semibold text-text-primary">Timeline</h4>
              <span className="text-xs text-text-muted">({detail.timeline.length})</span>
            </div>
            {detail.timeline.length === 0 ? (
              <p className="text-xs text-text-muted">No timeline entries yet.</p>
            ) : (
              <div className="max-h-64 space-y-2 overflow-auto">
                {detail.timeline.map((entry, i) => (
                  <div key={`${entry.timestamp}-${i}`} className="rounded border border-border-default bg-surface-card px-3 py-2 text-xs">
                    <div className="flex flex-wrap items-center gap-2 text-text-muted">
                      <span className="font-mono">{formatRelativeTime(entry.timestamp)}</span>
                      <span className="rounded border border-border-default bg-surface-input px-1.5 py-0.5 font-medium">{entry.entry_type}</span>
                      <span>{entry.actor}</span>
                    </div>
                    <div className="mt-2 break-words text-text-primary">{entry.detail}</div>
                    {entry.receipt_id && <div className="mt-1 break-all font-mono text-[10px] text-text-muted">{entry.receipt_id}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>

          {detail.remediation_receipts.length > 0 && (
            <div className="rounded-lg border border-border-default bg-surface-card-elevated p-4">
              <h4 className="text-sm font-semibold text-text-primary">Remediation Receipts</h4>
              <div className="mt-3 space-y-1">
                {detail.remediation_receipts.map((rid) => (
                  <div key={rid} className="truncate rounded border border-border-default bg-surface-card px-3 py-2 font-mono text-xs text-text-secondary" title={rid}>
                    {rid}
                  </div>
                ))}
              </div>
            </div>
          )}

          {showCloseDialog && (
            <ModalFrame
              title="Close Incident"
              description={
                detail.severity === 'CRITICAL' || detail.severity === 'HIGH'
                  ? 'A root cause is required for HIGH and CRITICAL incidents.'
                  : 'Optionally provide a root cause or mark as false positive.'
              }
            >
              <div className="mt-4 space-y-3">
                <label className="block">
                  <span className="mb-1 block text-xs text-text-secondary">
                    Root Cause
                    {(detail.severity === 'CRITICAL' || detail.severity === 'HIGH') && !falsePositive && (
                      <span className="ml-1 text-state-error">*</span>
                    )}
                  </span>
                  <textarea
                    value={rootCause}
                    onChange={(e) => setRootCause(e.target.value)}
                    rows={3}
                    className="w-full resize-none rounded-md border border-border-default bg-surface-input p-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
                  />
                </label>

                <label className="flex items-center gap-2 text-sm text-text-primary">
                  <input type="checkbox" checked={falsePositive} onChange={(e) => setFalsePositive(e.target.checked)} className="accent-accent-primary" />
                  Mark as false positive
                </label>

                {falsePositive && (
                  <label className="block">
                    <span className="mb-1 block text-xs text-text-secondary">False Positive Reason</span>
                    <input
                      type="text"
                      value={fpReason}
                      onChange={(e) => setFpReason(e.target.value)}
                      className="w-full rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
                    />
                  </label>
                )}

                <label className="flex items-center gap-2 text-sm text-text-primary">
                  <input type="checkbox" checked={genReport} onChange={(e) => setGenReport(e.target.checked)} className="accent-accent-primary" />
                  Generate board report
                </label>
              </div>

              <div className="mt-6 flex justify-end gap-3">
                <IncidentActionButton onClick={() => setShowCloseDialog(false)}>
                  Cancel
                </IncidentActionButton>
                <IncidentActionButton
                  onClick={handleClose}
                  tone="danger"
                  disabled={(detail.severity === 'CRITICAL' || detail.severity === 'HIGH') && !falsePositive && !rootCause.trim()}
                >
                  Close Incident
                </IncidentActionButton>
              </div>
            </ModalFrame>
          )}

          {showEscalate && (
            <ModalFrame title="Escalate Incident" description="Increase the severity and provide a reason.">
              <div className="mt-4 space-y-3">
                <label className="block">
                  <span className="mb-1 block text-xs text-text-secondary">New Severity</span>
                  <select
                    value={escalateSeverity}
                    onChange={(e) => setEscalateSeverity(e.target.value)}
                    className="w-full rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary"
                  >
                    <option value="CRITICAL">CRITICAL</option>
                    <option value="HIGH">HIGH</option>
                    <option value="MEDIUM">MEDIUM</option>
                  </select>
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs text-text-secondary">
                    Reason <span className="text-state-error">*</span>
                  </span>
                  <textarea
                    value={escalateReason}
                    onChange={(e) => setEscalateReason(e.target.value)}
                    rows={3}
                    className="w-full resize-none rounded-md border border-border-default bg-surface-input p-2 text-sm text-text-primary focus:border-accent-primary focus:outline-none"
                  />
                </label>
              </div>

              <div className="mt-6 flex justify-end gap-3">
                <IncidentActionButton onClick={() => setShowEscalate(false)}>
                  Cancel
                </IncidentActionButton>
                <IncidentActionButton onClick={handleEscalate} disabled={!escalateReason.trim()} tone="warning">
                  Escalate
                </IncidentActionButton>
              </div>
            </ModalFrame>
          )}
        </div>
      )}
    </div>
  )

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-default bg-surface-card px-5 py-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="text-[10px] font-semibold uppercase tracking-widest text-accent-primary">Response Desk</div>
            <h2 className="mt-2 text-2xl font-semibold leading-tight text-text-primary">Incident Response</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              Triage active incidents, follow playbooks, capture responder notes, and close with root-cause evidence.
            </p>
          </div>
          <div className={`rounded-lg border px-4 py-3 ${incidentTileToneClass[highPriority > 0 ? 'error' : stats?.open ? 'warning' : 'healthy']}`}>
            <div className="flex items-center gap-2">
              {highPriority > 0 ? (
                <Siren className="h-4 w-4" aria-hidden="true" />
              ) : (
                <ShieldAlert className="h-4 w-4" aria-hidden="true" />
              )}
              <span className="text-sm font-semibold text-text-primary">
                {highPriority > 0 ? 'High Priority Active' : stats?.open ? 'Open Incidents' : 'No Open Incidents'}
              </span>
            </div>
            <div className="mt-2 text-xs leading-5 text-text-muted">
              {stats ? `${stats.open} open, ${stats.closed} closed, ${highPriority} critical/high.` : 'Loading incident posture.'}
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <IncidentTile label="Open" value={stats?.open ?? '--'} detail="Incidents not yet closed." tone={(stats?.open ?? 0) > 0 ? 'warning' : 'healthy'} />
        <IncidentTile label="Critical" value={stats?.by_severity?.CRITICAL ?? 0} detail="Immediate response priority." tone={(stats?.by_severity?.CRITICAL ?? 0) > 0 ? 'error' : 'muted'} />
        <IncidentTile label="High" value={stats?.by_severity?.HIGH ?? 0} detail="Elevated operator attention." tone={(stats?.by_severity?.HIGH ?? 0) > 0 ? 'warning' : 'muted'} />
        <IncidentTile label="Visible" value={incidents.length} detail="Matching current filters." tone="accent" />
        <IncidentTile label="Filters" value={activeFilterCount} detail="Active list constraints." tone={activeFilterCount > 0 ? 'accent' : 'muted'} />
      </section>

      <section className="rounded-lg border border-border-default bg-surface-card p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-accent-primary" aria-hidden="true" />
            <div>
              <h3 className="text-sm font-semibold text-text-primary">Triage Filters</h3>
              <p className="text-xs leading-5 text-text-muted">Filter by status, category, and severity before selecting an incident.</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              setStatusFilter('')
              setCategoryFilter('')
              setSeverityFilter('')
            }}
            disabled={activeFilterCount === 0}
            className="inline-flex items-center justify-center gap-2 rounded border border-border-default bg-surface-card-elevated px-3 py-2 text-xs text-text-secondary transition-colors hover:border-border-active hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-45"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            Clear Filters
          </button>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="w-full min-w-0 rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-border-active focus:outline-none"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s === 'All' ? '' : s}>
                  {s === 'All' ? 'All Statuses' : s}
                </option>
              ))}
            </select>
          </label>

          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Category</span>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="w-full min-w-0 rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-border-active focus:outline-none"
            >
              <option value="">All Categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>

          <label className="min-w-0">
            <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">Severity</span>
            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value)}
              className="w-full min-w-0 rounded-md border border-border-default bg-surface-input px-3 py-2 text-sm text-text-primary focus:border-border-active focus:outline-none"
            >
              {SEVERITY_OPTIONS.map((s) => (
                <option key={s} value={s === 'All' ? '' : s}>
                  {s === 'All' ? 'All Severities' : s}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6">
        <section className="rounded-lg border border-border-default bg-surface-card">
          <div className="flex flex-col gap-3 border-b border-border-default px-4 py-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-2">
              <ClipboardList className="h-4 w-4 text-accent-primary" aria-hidden="true" />
              <div>
                <h3 className="text-sm font-semibold text-text-primary">Incident Queue</h3>
                <p className="text-xs leading-5 text-text-muted">Select an incident to inspect its playbook, timeline, and evidence.</p>
              </div>
            </div>
            <span className="rounded border border-border-default bg-surface-card-elevated px-2 py-1 text-xs font-mono text-text-muted">
              Sort: {sortField} {sortLabel(sortField)}
            </span>
          </div>

          {incidents.length === 0 ? (
            <div className="p-6">
              <EmptyState title="No Incidents" description="No incidents match the current filters." />
            </div>
          ) : (
            <>
              <div className="divide-y divide-border-default lg:hidden">
                {sortedIncidents.map((inc) => (
                  <div key={inc.incident_id}>
                    <IncidentSummaryCard
                      incident={inc}
                      selected={selectedId === inc.incident_id}
                      onSelect={() => selectIncident(inc.incident_id)}
                    />
                    {selectedId === inc.incident_id && incidentDetailPanel}
                  </div>
                ))}
              </div>

              <div className="hidden overflow-x-auto lg:block">
                <table className="w-full min-w-[820px] text-sm">
                  <thead>
                    <tr className="border-b border-border-default text-xs uppercase tracking-wider text-text-muted">
                      {[
                        ['severity', 'Severity'],
                        ['category', 'Category'],
                        ['playbook_name', 'Playbook'],
                        ['status', 'Status'],
                        ['opened_at', 'Opened'],
                      ].map(([field, label]) => (
                        <th key={field} className="px-4 py-3 text-left">
                          <button
                            type="button"
                            onClick={() => handleSort(field as SortField)}
                            className="inline-flex items-center gap-1 hover:text-text-primary"
                          >
                            {label}
                            <ArrowUpDown className="h-3 w-3" aria-hidden="true" />
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedIncidents.map((inc) => (
                      <Fragment key={inc.incident_id}>
                        <tr
                          onClick={() => selectIncident(inc.incident_id)}
                          className={`cursor-pointer border-b border-border-default transition-colors hover:bg-surface-card-elevated ${
                            selectedId === inc.incident_id ? 'bg-accent-primary/5' : ''
                          }`}
                        >
                          <td className="px-4 py-3">{severityBadge(inc.severity)}</td>
                          <td className="px-4 py-3 text-text-primary">{inc.category}</td>
                          <td className="max-w-[220px] truncate px-4 py-3 font-mono text-xs text-text-secondary" title={inc.playbook_name}>
                            {inc.playbook_name}
                          </td>
                          <td className="px-4 py-3">{statusBadge(inc.status)}</td>
                          <td className="px-4 py-3 text-text-muted">{formatRelativeTime(inc.opened_at)}</td>
                        </tr>
                        {selectedId === inc.incident_id && (
                          <tr>
                            <td colSpan={5} className="p-0">
                              {incidentDetailPanel}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>

      </div>
    </div>
  )
}
