import { apiGet } from './client'

export interface ReceiptItem {
  id: string
  timestamp: string
  action_type: string
  action_name: string
  inputs: Record<string, unknown>
  outputs: Record<string, unknown>
  status: string
  duration_ms: number | null
  token_count: number | null
  tier: number
  parent_id: string | null
  quest_id: string | null
  error_message: string | null
  metadata: Record<string, unknown>
}

export interface ReceiptStats {
  total_receipts: number
  by_status: Record<string, number>
  by_action_type: Record<string, number>
  tokens: { total: number; average: number; max: number }
  duration_ms: { total: number; average: number; max: number }
}

export function fetchReceipts(params?: {
  limit?: number
  offset?: number
  action_type?: string
  status?: string
  quest_id?: string
  since?: string
  until?: string
  q?: string
}) {
  const queryParams: Record<string, string> = {}
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined) queryParams[k] = String(v)
    })
  }
  return apiGet<{ receipts: ReceiptItem[]; total: number }>('/api/receipts', queryParams)
}

export function fetchReceipt(id: string) {
  return apiGet<{ receipt: ReceiptItem }>(`/api/receipts/${id}`)
}

export function fetchReceiptStats(since?: string) {
  return apiGet<{ stats: ReceiptStats }>('/api/receipts/stats', since ? { since } : undefined)
}
