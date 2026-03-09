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
}

export interface UABConnectedApp {
  pid: number
  name: string
  framework: string
  connectionMethod: string
  windowTitle: string
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
  return apiGet<{ receipts: any[] }>(`/api/flags/uab-receipts${query ? '?' + query : ''}`)
}
export const fetchUABSessions = (limit = 20) =>
  apiGet<{ sessions: any[] }>(`/api/flags/uab-sessions?limit=${limit}`)
