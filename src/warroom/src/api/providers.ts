// ============================================================
// Provider Stack API — model stack visibility (v8.3.0)
// ============================================================

import { apiGet, apiPost } from './client'

export interface LaneAssignment {
  model: string
  display_name: string
  context_window: number
  cost_output_per_1k: number
  supports_tools: boolean
}

export interface DiscoveredModel {
  id: string
  display_name: string
  context_window: number
  supports_tools: boolean
  capability_tier: string
  cost_input_per_1k: number
  cost_output_per_1k: number
}

export interface ProviderStackResponse {
  provider: string
  provider_display_name: string
  lanes: Record<string, LaneAssignment>
  discovered_models: DiscoveredModel[]
  models_count: number
  last_refresh: string | null
  status: string
}

export interface RefreshResponse {
  status: string
  models_found?: number
  lanes?: Record<string, string>
  message?: string
}

export const fetchProviderStack = () =>
  apiGet<ProviderStackResponse>('/api/v1/providers/stack')

export const refreshModelDiscovery = () =>
  apiPost<RefreshResponse>('/api/v1/providers/refresh')
