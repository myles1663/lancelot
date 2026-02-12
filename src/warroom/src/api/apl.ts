import { apiGet, apiPost } from './client'

export interface AplRule {
  id: string
  name: string
  description: string
  pattern_type: string
  status: string
  conditions_summary: string
  auto_decisions_today: number
  auto_decisions_total: number
  max_daily: number
  max_total: number
  activated_at: string
  created_at: string
}

export interface AplProposal {
  id: string
  name: string
  description: string
  pattern_type: string
  conditions: Record<string, unknown>
  created_at: string
}

export interface AplDecision {
  id: string
  capability: string
  target: string
  risk_tier: number
  decision: string
  is_auto: boolean
  rule_id: string
  reason: string
  recorded_at: string
}

export interface CircuitBreaker {
  id: string
  name: string
  daily_usage: number
  max_daily: number
}

export function fetchAplRules(status?: string) {
  return apiGet<{ rules: AplRule[]; total: number }>('/api/apl/rules', status ? { status } : undefined)
}

export function fetchAplProposals() {
  return apiGet<{ proposals: AplProposal[]; total: number }>('/api/apl/proposals')
}

export function fetchAplDecisions(limit = 50) {
  return apiGet<{ decisions: AplDecision[]; total: number; auto_approved: number }>(
    '/api/apl/decisions',
    { limit: String(limit) },
  )
}

export function fetchCircuitBreakers() {
  return apiGet<{ circuit_breakers: CircuitBreaker[]; total: number }>('/api/apl/circuit-breakers')
}

export function pauseAplRule(id: string) {
  return apiPost<{ status: string }>(`/api/apl/rules/${id}/pause`)
}

export function resumeAplRule(id: string) {
  return apiPost<{ status: string }>(`/api/apl/rules/${id}/resume`)
}

export function revokeAplRule(id: string, reason?: string) {
  return apiPost<{ status: string }>(`/api/apl/rules/${id}/revoke`, { reason })
}

export function activateAplProposal(id: string) {
  return apiPost<{ status: string }>(`/api/apl/proposals/${id}/activate`)
}

export function declineAplProposal(id: string, reason?: string) {
  return apiPost<{ status: string }>(`/api/apl/proposals/${id}/decline`, { reason })
}
