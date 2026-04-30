// ============================================================
// A2A Protocol API Client
// ============================================================

import { apiGet, apiPost, apiDelete } from './client'

// ── Types ──────────────────────────────────────────────────

export interface A2AStatus {
  enabled: boolean
  soul_version: string | null
  inbound_enabled: boolean
  outbound_enabled: boolean
  registered_agents: number
  max_delegation_depth: number
}

export interface RemoteAgent {
  agent_id: string
  display_name: string
  agent_card_url: string
  agent_framework: string
  auth_type: string
  credentials_ref: string
  inbound_trust_tier: number
  outbound_trust_tier: number
  direction: string
  network_allowlist_entries: string[]
  kill_switch_id: string
  last_verified: string
  status: string
  auto_registered: boolean
  interaction_count: number
  success_count: number
  last_interaction: string
  last_outcome: string
  registered_at: string
  card_status: string
  // Detail view extras
  recent_receipts?: Record<string, unknown>[]
  soul_permissions?: Record<string, unknown>
}

export interface AgentListResponse {
  agents: RemoteAgent[]
  total: number
}

export interface LancelotAgentCard {
  name: string
  description: string
  url: string
  version: string
  a2a_protocol_version: string
  skills: { id: string; name: string; description: string }[]
  authentication: Record<string, unknown>
  capabilities: Record<string, boolean>
  governance_declaration?: Record<string, unknown>
}

// ── API Functions ──────────────────────────────────────────

export function fetchA2AStatus(): Promise<A2AStatus> {
  return apiGet<A2AStatus>('/api/a2a/status')
}

export function fetchRemoteAgents(params?: {
  direction?: string
  status?: string
  framework?: string
}): Promise<AgentListResponse> {
  return apiGet<AgentListResponse>('/api/a2a/agents', params as Record<string, string>)
}

export function fetchRemoteAgent(agentId: string): Promise<RemoteAgent> {
  return apiGet<RemoteAgent>(`/api/a2a/agents/${agentId}`)
}

export function registerRemoteAgent(data: {
  agent_id: string
  display_name: string
  agent_card_url?: string
  agent_framework?: string
  auth_type?: string
  direction?: string
}): Promise<{ agent_id: string; kill_switch_id: string; status: string }> {
  return apiPost('/api/a2a/agents', data)
}

export function revokeRemoteAgent(agentId: string): Promise<{ agent_id: string; status: string }> {
  return apiDelete(`/api/a2a/agents/${agentId}`)
}

export function verifyAgentCard(agentId: string): Promise<{ agent_id: string; verified: boolean; card_status: string }> {
  return apiPost(`/api/a2a/agents/${agentId}/verify`)
}

export function fetchOwnAgentCard(): Promise<LancelotAgentCard> {
  return apiGet<LancelotAgentCard>('/api/a2a/card')
}

export function regenerateAgentCard(): Promise<{ status: string; skills_count: number }> {
  return apiPost('/api/a2a/card/regenerate')
}

export function delegateTask(data: {
  target_agent_id: string
  content: string
  task_type?: string
}): Promise<Record<string, unknown>> {
  return apiPost('/api/a2a/delegate', data)
}

export function fetchA2AReceipts(limit?: number): Promise<{ receipts: Record<string, unknown>[]; total: number }> {
  return apiGet('/api/a2a/receipts', limit ? { limit: String(limit) } : undefined)
}
