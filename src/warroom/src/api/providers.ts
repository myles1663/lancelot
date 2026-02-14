// ============================================================
// Provider Stack API — model stack + switching (v8.3.1)
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

// --- v8.3.1: Provider switching + lane overrides ---

export interface AvailableProvider {
  name: string
  display_name: string
  has_key: boolean
  active: boolean
}

export interface AvailableProvidersResponse {
  providers: AvailableProvider[]
}

export interface SwitchProviderResponse {
  status: string
  message?: string
  stack?: ProviderStackResponse
}

export interface LaneOverrideResponse {
  status: string
  message?: string
  stack?: ProviderStackResponse
}

export interface LaneResetResponse {
  status: string
  message?: string
  stack?: ProviderStackResponse
}

// --- Fetchers ---

export const fetchProviderStack = () =>
  apiGet<ProviderStackResponse>('/api/v1/providers/stack')

export const refreshModelDiscovery = () =>
  apiPost<RefreshResponse>('/api/v1/providers/refresh')

export const fetchAvailableProviders = () =>
  apiGet<AvailableProvidersResponse>('/api/v1/providers/available')

export const switchProvider = (provider: string) =>
  apiPost<SwitchProviderResponse>('/api/v1/providers/switch', { provider })

export const overrideLane = (lane: string, model_id: string) =>
  apiPost<LaneOverrideResponse>('/api/v1/providers/lanes/override', { lane, model_id })

export const resetLanes = () =>
  apiPost<LaneResetResponse>('/api/v1/providers/lanes/reset')
