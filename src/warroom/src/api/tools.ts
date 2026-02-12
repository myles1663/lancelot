import { apiGet } from './client'

export interface ToolProviderHealth {
  state: string
  error: string | null
}

export interface ToolsHealthResponse {
  providers: Record<string, ToolProviderHealth>
  summary: {
    total_providers: number
    healthy: number
    degraded: number
    offline: number
  }
  enabled: boolean
}

export interface ToolsRoutingResponse {
  routing: Record<string, unknown>
  enabled: boolean
}

export interface ToolsConfigResponse {
  enabled: boolean
  safe_mode: boolean
  receipts: boolean
}

export const fetchToolsHealth = () => apiGet<ToolsHealthResponse>('/api/tools/health')
export const fetchToolsRouting = () => apiGet<ToolsRoutingResponse>('/api/tools/routing')
export const fetchToolsConfig = () => apiGet<ToolsConfigResponse>('/api/tools/config')
