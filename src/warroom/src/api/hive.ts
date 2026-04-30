// ============================================================
// HIVE Agent Mesh API Client
// ============================================================

import { apiGet, apiPost } from './client'

// ── Types ────────────────────────────────────────────────────

export interface HiveStatus {
  status: string
  enabled: boolean
  quest_id?: string
  goal?: string
  active_agents?: number
  max_agents?: number
  plan?: {
    subtask_count: number
    execution_order: string[][]
  }
  results_count?: number
  plan_revision_count?: number
}

export interface HiveAgent {
  agent_id: string
  state: string
  task_description: string
  quest_id?: string
  action_count: number
  control_method: string
  created_at: string
  collapse_reason?: string
  collapse_message?: string
  interventions: Record<string, unknown>[]
  scoped_soul_hash?: string
}

export interface HiveRoster {
  active: HiveAgent[]
  archived: HiveAgent[]
}

export interface HiveAgentsResponse {
  agents: HiveAgent[]
}

export interface HiveTaskResult {
  quest_id: string
  success: boolean
  error?: string
  results: {
    agent_id: string
    success: boolean
    action_count?: number
    error?: string
  }[]
  plan?: {
    subtask_count: number
    execution_order: string[][]
  }
}

export interface HiveIntervention {
  id: string
  action_type: string
  action_name: string
  inputs: Record<string, unknown>
  status: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface HiveInterventionsResponse {
  interventions: HiveIntervention[]
}

// ── API Functions ────────────────────────────────────────────

export function getHiveStatus() {
  return apiGet<HiveStatus>('/api/hive/status')
}

export function getHiveRoster() {
  return apiGet<HiveRoster>('/api/hive/roster')
}

export function getHiveAgents() {
  return apiGet<HiveAgentsResponse>('/api/hive/agents')
}

export function getHiveAgentHistory() {
  return apiGet<HiveAgentsResponse>('/api/hive/agents/history')
}

export function getHiveAgent(agentId: string) {
  return apiGet<HiveAgent>(`/api/hive/agents/${agentId}`)
}

export function getAgentSoul(agentId: string) {
  return apiGet<{ agent_id: string; soul: unknown }>(`/api/hive/agents/${agentId}/soul`)
}

export function submitTask(goal: string, context?: Record<string, unknown>) {
  return apiPost<HiveTaskResult>('/api/hive/tasks', { goal, context })
}

export function pauseAgent(agentId: string, reason: string) {
  return apiPost<{ status: string; agent_id: string }>(
    `/api/hive/agents/${agentId}/pause`,
    { reason },
  )
}

export function resumeAgent(agentId: string) {
  return apiPost<{ status: string; agent_id: string }>(
    `/api/hive/agents/${agentId}/resume`,
  )
}

export function killAgent(agentId: string, reason: string) {
  return apiPost<{ status: string; agent_id: string }>(
    `/api/hive/agents/${agentId}/kill`,
    { reason },
  )
}

export function modifyAgent(
  agentId: string,
  reason: string,
  feedback?: string,
  constraints?: Record<string, unknown>,
) {
  return apiPost<Record<string, unknown>>(
    `/api/hive/agents/${agentId}/modify`,
    { reason, feedback, constraints },
  )
}

export function killAll(reason: string) {
  return apiPost<{ status: string; collapsed: string[] }>(
    '/api/hive/kill-all',
    { reason },
  )
}

export function getInterventions() {
  return apiGet<HiveInterventionsResponse>('/api/hive/interventions')
}

export function getTaskInterventions(questId: string) {
  return apiGet<HiveInterventionsResponse>(`/api/hive/interventions/${questId}`)
}
