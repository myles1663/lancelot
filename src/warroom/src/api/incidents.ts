import { apiGet, apiPost } from './client'

// ── Types ──────────────────────────────────────────────────────

export interface IncidentSummary {
  incident_id: string
  category: string
  severity: string
  status: string
  playbook_name: string
  opened_at: string
  dedup_key: string | null
}

export interface TimelineEntry {
  timestamp: string
  entry_type: string
  actor: string
  detail: string
  receipt_id: string | null
}

export interface IncidentDetail {
  incident_id: string
  trigger_receipt_id: string
  category: string
  severity: string
  playbook_name: string
  status: string
  opened_at: string
  paged_at: string | null
  responder_id: string | null
  acknowledged_at: string | null
  timeline: TimelineEntry[]
  remediation_receipts: string[]
  closed_at: string | null
  closed_by: string | null
  root_cause: string | null
  board_report_generated: boolean
  dedup_key: string | null
}

export interface IncidentStats {
  total: number
  open: number
  closed: number
  by_severity: Record<string, number>
}

export interface PlaybookStep {
  step: number
  title: string
  description: string
  action_type: string
  sla_minutes: number
  decision_points: Array<{ condition: string; action: string }>
  actions: string[]
}

export interface PlaybookMetadata {
  name: string
  display_name: string
  description: string
  category: string
  severity_default: string
  industry: string
  version: string
  tags: string[]
  step_count: number
  extends: string | null
}

export interface PlaybookDetail extends PlaybookMetadata {
  trigger: Record<string, unknown>
  paging: Record<string, unknown>
  steps: PlaybookStep[]
}

// ── API Functions ──────────────────────────────────────────────

export async function fetchIncidents(params?: {
  status?: string
  category?: string
  severity?: string
  limit?: number
  offset?: number
}): Promise<{ incidents: IncidentSummary[]; count: number }> {
  const query = new URLSearchParams()
  if (params?.status) query.set('status', params.status)
  if (params?.category) query.set('category', params.category)
  if (params?.severity) query.set('severity', params.severity)
  if (params?.limit) query.set('limit', String(params.limit))
  if (params?.offset) query.set('offset', String(params.offset))
  const qs = query.toString()
  return apiGet(`/api/incidents${qs ? '?' + qs : ''}`)
}

export async function fetchIncident(id: string): Promise<IncidentDetail> {
  return apiGet(`/api/incidents/${id}`)
}

export async function fetchIncidentStats(): Promise<IncidentStats> {
  return apiGet('/api/incidents/stats')
}

export async function acknowledgeIncident(
  id: string,
  _operatorId: string
): Promise<{ status: string }> {
  return apiPost(`/api/incidents/${id}/acknowledge`, {})
}

export async function updateIncidentStatus(
  id: string,
  _operatorId: string,
  status: string,
  note?: string
): Promise<{ status: string }> {
  return apiPost(`/api/incidents/${id}/status`, {
    status,
    note: note || '',
  })
}

export async function addTimelineEntry(
  id: string,
  _operatorId: string,
  entryText: string
): Promise<{ status: string }> {
  return apiPost(`/api/incidents/${id}/timeline`, {
    entry_text: entryText,
  })
}

export async function linkReceipt(
  id: string,
  _operatorId: string,
  receiptId: string
): Promise<{ status: string }> {
  return apiPost(`/api/incidents/${id}/link-receipt`, {
    receipt_id: receiptId,
  })
}

export async function escalateIncident(
  id: string,
  _operatorId: string,
  newSeverity: string,
  reason: string
): Promise<{ status: string }> {
  return apiPost(`/api/incidents/${id}/escalate`, {
    new_severity: newSeverity,
    reason,
  })
}

export async function closeIncident(
  id: string,
  _operatorId: string,
  rootCause?: string,
  falsePositive?: boolean,
  falsePositiveReason?: string,
  generateReport?: boolean
): Promise<{ status: string; board_report_generated: boolean }> {
  return apiPost(`/api/incidents/${id}/close`, {
    root_cause: rootCause,
    false_positive: falsePositive || false,
    false_positive_reason: falsePositiveReason,
    generate_report: generateReport || false,
  })
}

export async function generateReport(
  id: string
): Promise<{ status: string; size_bytes: number }> {
  return apiPost(`/api/incidents/${id}/report`, {})
}

export function getReportDownloadUrl(id: string): string {
  return `/api/incidents/${id}/report/download`
}

// ── Playbook API ───────────────────────────────────────────────

export async function fetchPlaybooks(params?: {
  category?: string
  industry?: string
}): Promise<{ playbooks: PlaybookMetadata[] }> {
  const query = new URLSearchParams()
  if (params?.category) query.set('category', params.category)
  if (params?.industry) query.set('industry', params.industry)
  const qs = query.toString()
  return apiGet(`/api/playbooks${qs ? '?' + qs : ''}`)
}

export async function fetchPlaybook(
  name: string
): Promise<PlaybookDetail> {
  return apiGet(`/api/playbooks/${name}`)
}
