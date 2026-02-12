import { apiGet, apiPost } from './client'

export interface GovernanceStats {
  stats: {
    trust: Record<string, unknown>
    apl: Record<string, unknown>
  }
}

export interface GovernanceDecision {
  id: string
  capability: string
  target: string
  risk_tier: number
  decision: string
  reason: string
  rule_id: string
  is_auto: boolean
  recorded_at: string
}

export interface ApprovalItem {
  id: string
  type: 'graduation' | 'apl_rule'
  capability?: string
  scope?: string
  name?: string
  description?: string
  current_tier?: number
  proposed_tier?: number
  pattern_type?: string
  consecutive_successes?: number
  status: string
  created_at: string
}

export function fetchGovernanceStats() {
  return apiGet<GovernanceStats>('/api/governance/stats')
}

export function fetchGovernanceDecisions(limit = 50, capability?: string) {
  const params: Record<string, string> = { limit: String(limit) }
  if (capability) params.capability = capability
  return apiGet<{ decisions: GovernanceDecision[]; total: number }>('/api/governance/decisions', params)
}

export function fetchGovernanceApprovals() {
  return apiGet<{ approvals: ApprovalItem[]; total: number }>('/api/governance/approvals')
}

export function approveItem(id: string, reason?: string) {
  return apiPost<{ status: string; id: string }>(`/api/governance/approvals/${id}/approve`, { reason })
}

export function denyItem(id: string, reason?: string) {
  return apiPost<{ status: string; id: string }>(`/api/governance/approvals/${id}/deny`, { reason })
}
