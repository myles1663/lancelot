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
}

export interface FlagsResponse {
  flags: Record<string, FlagInfo>
}

export interface ToggleFlagResponse {
  flag: string
  enabled: boolean
  restart_required: boolean
  message: string
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
