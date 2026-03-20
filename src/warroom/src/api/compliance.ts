import { apiGet, apiPost } from './client'

// ── Types ──────────────────────────────────────────────────────

export interface ExportFormat {
  id: string
  name: string
  description: string
  available: boolean
}

export interface ExportRequest {
  format: string
  period_start: string
  period_end: string
  quest_id?: string
  anomaly_threshold?: number
}

export interface ExportResponse {
  export_id: string
  export_format: string
  period_start: string
  period_end: string
  receipt_count: number
  chain_integrity: string
  output_sha256: string
  export_duration_ms: number
  generated_at: string
  success: boolean
  error: string | null
  download_url: string
}

export interface ExportHistoryEntry {
  export_id: string
  export_format: string
  period_start: string
  period_end: string
  receipt_count: number
  chain_integrity: string
  output_sha256: string
  export_duration_ms: number
  generated_at: string
  operator_id: string | null
  filename: string
}

export interface ChainIntegrityResult {
  status: string
  period_start: string
  period_end: string
  total_receipts: number
  receipts_with_parents: number
  orphaned_count: number
  is_intact: boolean
}

export interface VerifyResult {
  export_id: string
  verified: boolean
  current_sha256: string
  original_sha256?: string
  mismatch?: boolean
  reason?: string
}

// ── API Functions ──────────────────────────────────────────────

export function fetchExportFormats() {
  return apiGet<{ formats: ExportFormat[] }>('/api/compliance/formats')
}

export function generateExport(req: ExportRequest) {
  return apiPost<ExportResponse>('/api/compliance/export', req)
}

export function fetchExportHistory() {
  return apiGet<{ exports: ExportHistoryEntry[]; total: number }>('/api/compliance/history')
}

export function checkChainIntegrity(periodStart: string, periodEnd: string, questId?: string) {
  const params: Record<string, string> = { period_start: periodStart, period_end: periodEnd }
  if (questId) params.quest_id = questId
  return apiGet<ChainIntegrityResult>('/api/compliance/chain-integrity', params)
}

export function verifyExport(exportId: string) {
  return apiPost<VerifyResult>(`/api/compliance/verify/${exportId}`)
}

export function getExportDownloadUrl(exportId: string): string {
  return `/api/compliance/download/${exportId}`
}
