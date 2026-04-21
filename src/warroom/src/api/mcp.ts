// ============================================================
// MCP (Model Context Protocol) Management API
// Endpoints for listing, registering, and configuring MCP servers
// ============================================================

import { apiGet, apiPost, apiDelete } from './client'

// ── Response Types ──────────────────────────────────────────

export interface MCPServerInfo {
  server_id: string
  name: string
  endpoint: string
  transport: string
  auth_type: string
  has_credentials: boolean
  default_risk_tier: string
  status: string
  network_domains: string[]
  kill_switch_id: string
  registered_at: string
  tool_count: number
}

export interface MCPToolInfo {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface MCPServersListResponse {
  servers: MCPServerInfo[]
  total: number
  active_count: number
  feature_enabled: boolean
}

export interface MCPRegisterResponse {
  registered: boolean
  server_id: string
  kill_switch_id: string
  error?: string
}

export interface MCPServerDetailResponse {
  server: MCPServerInfo
  tools: MCPToolInfo[]
  soul_permitted: boolean
  kill_switch_active: boolean
  kill_switch?: {
    allowed: boolean
    switch_id: string
    scope: string
    source: string
    reason: string
  }
  network_allowed: boolean
}

export interface MCPTestResult {
  success: boolean
  tool_count: number
  latency_ms: number
  error?: string
}

export interface MCPReceiptSummary {
  total_calls: number
  total_blocked: number
  block_gates: Record<string, number>
  recent_calls: { server_id: string; tool_name: string; status: string; timestamp: string }[]
}

// ── API Functions (/api/mcp/*) ──────────────────────────────

export const fetchMCPServers = () =>
  apiGet<MCPServersListResponse>('/api/mcp/servers')

export const fetchMCPServerDetail = (serverId: string) =>
  apiGet<MCPServerDetailResponse>(`/api/mcp/servers/${serverId}`)

export const registerMCPServer = (config: {
  server_id: string
  name: string
  endpoint: string
  auth_type: string
  vault_key?: string
  auth_header?: string
  default_risk_tier: string
  network_domains: string[]
}) =>
  apiPost<MCPRegisterResponse>('/api/mcp/servers', config)

export const unregisterMCPServer = (serverId: string) =>
  apiDelete<{ removed: boolean }>(`/api/mcp/servers/${serverId}`)

export const setMCPServerStatus = (serverId: string, status: string) =>
  apiPost<{ server_id: string; status: string }>(
    `/api/mcp/servers/${serverId}/status`,
    { status },
  )

export const testMCPServer = (serverId: string) =>
  apiPost<MCPTestResult>(`/api/mcp/servers/${serverId}/test`)

export const storeMCPCredential = (
  serverId: string,
  vaultKey: string,
  value: string,
  type: string = 'api_key',
) =>
  apiPost<{ stored: boolean }>(`/api/mcp/servers/${serverId}/credential`, {
    vault_key: vaultKey,
    value,
    type,
  })

export const fetchMCPReceiptSummary = () =>
  apiGet<MCPReceiptSummary>('/api/mcp/receipts/summary')
