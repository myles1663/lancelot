// ============================================================
// Time-Travel Debugging API Client
// ============================================================

import { apiGet, apiPost } from './client'

// ── Types ──────────────────────────────────────────────────

export interface TimeTravelStatus {
  enabled: boolean
  engine_ready?: boolean
  quest_executor_ready?: boolean
  snapshot_reader_ready?: boolean
  receipt_service_ready?: boolean
  soul_version: string | null
  fork_allowed: boolean
  require_approval_tier: number
  runtime_degraded?: boolean
  degraded_reasons?: string[]
  runtime_errors?: string[]
}

export interface ReceiptNode {
  id: string
  timestamp: string
  action_type: string
  action_name: string
  status: string
  tier: number
  parent_id: string | null
  quest_id: string | null
  duration_ms: number | null
  inputs: Record<string, unknown>
  outputs: Record<string, unknown>
  metadata: Record<string, unknown>
  operator_id: string | null
}

export interface QuestReceiptsResponse {
  quest_id: string
  receipt_count: number
  receipts: ReceiptNode[]
}

export interface StateSnapshot {
  timestamp: string
  receipt_id: string | null
  quest_id: string | null
  soul_version: string | null
  kill_switches: Record<string, boolean>
  trust_tier: number | null
  trust_records: Record<string, unknown>[]
  cost_data: Record<string, unknown>
  active_flags: Record<string, boolean>
  receipt_chain_length: number
  metadata: Record<string, unknown>
}

export interface InspectionResult {
  success: boolean
  receipt_id: string | null
  snapshot: StateSnapshot | null
  error: string | null
}

export interface ReplayResult {
  success: boolean
  replay_quest_id: string | null
  source_quest_id: string | null
  receipt_id: string | null
  error: string | null
  approval_status: string | null
}

export interface ForkResult {
  success: boolean
  fork_quest_id: string | null
  source_quest_id: string | null
  receipt_id: string | null
  error: string | null
  approval_status: string | null
  modifications_applied: Record<string, unknown>
}

// ── API Functions ──────────────────────────────────────────

export function fetchTimeTravelStatus(): Promise<TimeTravelStatus> {
  return apiGet<TimeTravelStatus>('/api/timetravel/status')
}

export function fetchQuestReceipts(questId: string): Promise<QuestReceiptsResponse> {
  return apiGet<QuestReceiptsResponse>(`/api/timetravel/quest/${questId}/receipts`)
}

export function fetchReceiptSnapshot(receiptId: string): Promise<StateSnapshot> {
  return apiGet<StateSnapshot>(`/api/timetravel/receipt/${receiptId}/snapshot`)
}

export function createInspection(receiptId: string): Promise<InspectionResult> {
  return apiPost<InspectionResult>('/api/timetravel/inspect', { receipt_id: receiptId })
}

export function createReplay(sourceQuestId: string): Promise<ReplayResult> {
  return apiPost<ReplayResult>('/api/timetravel/replay', { source_quest_id: sourceQuestId })
}

export function createFork(
  sourceQuestId: string,
  modifications: Record<string, unknown>,
): Promise<ForkResult> {
  return apiPost<ForkResult>('/api/timetravel/fork', {
    source_quest_id: sourceQuestId,
    modifications,
  })
}
