import { apiGet, apiPost, apiPut } from './client'

export interface FlagInfo {
  enabled: boolean
  restart_required: boolean
  description: string
  category: string
  requires: string[]
  conflicts: string[]
  warning: string
  has_editor?: string
  confirm_enable?: string
  hidden?: boolean
}

export interface FlagsResponse {
  flags: Record<string, FlagInfo>
}

export interface ToggleFlagResponse {
  flag: string
  enabled: boolean
  restart_required: boolean
  message: string
  agent_reachable?: boolean
  agent_start_hint?: string
}

export interface AllowlistResponse {
  domains: string[]
  path?: string
}

export const fetchFlags = () => apiGet<FlagsResponse>('/api/flags')
export const toggleFlag = (name: string) => apiPost<ToggleFlagResponse>(`/api/flags/${name}/toggle`)
export const fetchNetworkAllowlist = () => apiGet<AllowlistResponse>('/api/flags/network-allowlist')
export const updateNetworkAllowlist = (domains: string[]) =>
  apiPut<AllowlistResponse>('/api/flags/network-allowlist', { domains })

// Host Agent Bridge
export interface HostAgentStatus {
  reachable: boolean
  platform: string
  platform_version: string
  hostname: string
  agent_version: string
}

export const fetchHostAgentStatus = () => apiGet<HostAgentStatus>('/api/flags/host-agent-status')
export const shutdownHostAgent = () => apiPost<{ status: string }>('/api/flags/host-agent-shutdown')

// Host Write Commands
export interface WriteCommandsResponse {
  commands: string[]
  raw: string
  path?: string
}

export const fetchHostWriteCommands = () => apiGet<WriteCommandsResponse>('/api/flags/host-write-commands')
export const saveHostWriteCommands = (raw: string) =>
  apiPut<{ commands: string[]; count: number }>('/api/flags/host-write-commands', { raw })

// Host Write Commands Sub-Toggle
export const fetchHostWriteStatus = () => apiGet<{ enabled: boolean }>('/api/flags/host-write-status')
export const toggleHostWriteCommands = () => apiPost<{ enabled: boolean }>('/api/flags/host-write-toggle')

// UAB (Universal App Bridge)
export interface UABStatus {
  reachable: boolean
  version: string
  connected_apps: number
  supported_frameworks: string[]
  uptime_seconds: number
  transport?: string
  standalone_features?: string[]
  connections?: UABConnectedApp[]
}

export interface UABConnectedApp {
  pid: number
  name: string
  framework: string
  connectionMethod: string
  windowTitle: string
  elementCount?: number
}

export interface UABReceipt {
  receipt_id: string
  timestamp: string
  session_id: string | null
  parent_receipt_id: string | null
  app_name: string
  app_pid: number
  app_framework: string | null
  window_title: string | null
  connection_method: string | null
  action_type: string
  mutating: boolean
  risk_level: string
  element_id: string | null
  element_type: string | null
  element_label: string | null
  element_path: string | null
  action_performed: string | null
  action_params: Record<string, unknown>
  pre_state: Record<string, unknown>
  post_state: Record<string, unknown>
  elements_returned: number
  query_selector: Record<string, unknown> | null
  chain_id: string | null
  chain_name: string | null
  step_index: number | null
  total_steps: number | null
  state_changed: boolean | null
  expected_outcome: string | null
  actual_outcome: string | null
  governance_gate: string
  approval_id: string | null
  policy_snapshot: Record<string, unknown>
  duration_ms: number | null
  success: boolean
  error_message: string | null
}

export interface UABSessionSummary {
  session_id: string
  app_name: string
  app_pid: number
  app_framework: string | null
  connection_method: string | null
  connected_at: string
  disconnected_at: string | null
  total_actions: number
  mutating_actions: number
  read_only_actions: number
  action_summary: Record<string, number>
  elements_touched: string[]
  max_risk_level: string
  receipt_ids: string[]
  active: boolean
}

export interface UABReceiptsResponse {
  receipts: UABReceipt[]
  error?: string
}

export interface UABSessionsResponse {
  sessions: UABSessionSummary[]
  error?: string
}

export const fetchUABStatus = () => apiGet<UABStatus>('/api/flags/uab-status')
export const fetchUABApps = () => apiGet<{ apps: UABConnectedApp[] }>('/api/flags/uab-apps')
export const fetchUABReceipts = (params?: {
  limit?: number
  app_name?: string
  mutating_only?: boolean
  action_type?: string
}) => {
  const qs = new URLSearchParams()
  if (params?.limit) qs.set('limit', String(params.limit))
  if (params?.app_name) qs.set('app_name', params.app_name)
  if (params?.mutating_only) qs.set('mutating_only', 'true')
  if (params?.action_type) qs.set('action_type', params.action_type)
  const query = qs.toString()
  return apiGet<UABReceiptsResponse>(`/api/flags/uab-receipts${query ? '?' + query : ''}`)
}
export const fetchUABSessions = (limit = 20) =>
  apiGet<UABSessionsResponse>(`/api/flags/uab-sessions?limit=${limit}`)
