import { apiGet, apiPost } from './client'

export interface FlagInfo {
  enabled: boolean
  restart_required: boolean
  description: string
  category: string
  requires: string[]
  conflicts: string[]
  warning: string
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

export const fetchFlags = () => apiGet<FlagsResponse>('/api/flags')
export const toggleFlag = (name: string) => apiPost<ToggleFlagResponse>(`/api/flags/${name}/toggle`)
