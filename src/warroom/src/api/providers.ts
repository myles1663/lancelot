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

// --- API Key Management ---

export interface ProviderKeyInfo {
  provider: string
  display_name: string
  env_var: string
  has_key: boolean
  key_preview: string
  active: boolean
  oauth_configured: boolean
  oauth_status: string | null
}

export interface ProviderKeysResponse {
  keys: ProviderKeyInfo[]
}

export interface RotateKeyResponse {
  status: string
  provider?: string
  key_preview?: string
  models_discovered?: number
  hot_swapped?: boolean
  persisted_to_env?: boolean
  message?: string
}

export const fetchProviderKeys = () =>
  apiGet<ProviderKeysResponse>('/api/v1/providers/keys')

export const rotateProviderKey = (provider: string, apiKey: string) =>
  apiPost<RotateKeyResponse>('/api/v1/providers/keys/rotate', { provider, api_key: apiKey })

// --- V28: OAuth Management ---

export interface OAuthInitiateResponse {
  status: string
  auth_url?: string
  state?: string
  message?: string
}

export interface OAuthStatusResponse {
  configured: boolean
  valid?: boolean
  status: string
  expires_at?: string
  expires_in_seconds?: number
  error?: string
}

export interface OAuthRevokeResponse {
  status: string
  message?: string
}

export const initiateOAuth = () =>
  apiPost<OAuthInitiateResponse>('/api/v1/providers/oauth/initiate')

export const fetchOAuthStatus = () =>
  apiGet<OAuthStatusResponse>('/api/v1/providers/oauth/status')

export const revokeOAuth = () =>
  apiPost<OAuthRevokeResponse>('/api/v1/providers/oauth/revoke')
