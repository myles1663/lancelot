import { apiGet, apiPost } from './client'

export interface TrustRecord {
  capability: string
  scope: string
  current_tier: number
  default_tier: number
  is_graduated: boolean
  consecutive_successes: number
  total_successes: number
  total_failures: number
  total_rollbacks: number
  success_rate: number
  can_graduate: boolean
  last_success: string
  last_failure: string
}

export interface TrustProposal {
  id: string
  capability: string
  scope: string
  current_tier: number
  proposed_tier: number
  consecutive_successes: number
  status: string
  created_at: string
}

export interface TrustEvent {
  capability: string
  scope: string
  timestamp: string
  from_tier: number
  to_tier: number
  trigger: string
  owner_approved: boolean | null
}

export function fetchTrustRecords() {
  return apiGet<{ records: TrustRecord[]; total: number }>('/api/trust/records')
}

export function fetchTrustProposals() {
  return apiGet<{ proposals: TrustProposal[]; total: number }>('/api/trust/proposals')
}

export function fetchTrustTimeline() {
  return apiGet<{ events: TrustEvent[]; total: number }>('/api/trust/timeline')
}

export function approveTrustProposal(id: string, reason?: string) {
  return apiPost<{ status: string }>(`/api/trust/proposals/${id}/approve`, { reason })
}

export function declineTrustProposal(id: string, reason?: string) {
  return apiPost<{ status: string }>(`/api/trust/proposals/${id}/decline`, { reason })
}
