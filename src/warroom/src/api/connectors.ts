// ============================================================
// Connectors Management API
// Endpoints for listing, enabling/disabling, and configuring connectors
// ============================================================

import { apiGet, apiPost, apiDelete } from './client'

// ── Response Types ──────────────────────────────────────────

export interface CredentialInfo {
  vault_key: string
  name: string
  type: string
  required: boolean
  present: boolean
  scopes: string[]
}

export interface ConnectorInfo {
  id: string
  name: string
  description: string
  version: string
  author: string
  source: string
  enabled: boolean
  backend: string | null
  available_backends: string[] | null
  target_domains: string[]
  data_reads: string[]
  data_writes: string[]
  does_not_access: string[]
  credentials: CredentialInfo[]
  operation_count: number
}

export interface ConnectorsListResponse {
  connectors: ConnectorInfo[]
  total: number
  enabled_count: number
  configured_count: number
}

export interface ConnectorToggleResponse {
  id: string
  enabled: boolean
}

export interface BackendSetResponse {
  connector_id: string
  backend: string
}

export interface StoreCredentialResponse {
  stored: boolean
  vault_key: string
}

export interface DeleteCredentialResponse {
  deleted: boolean
}

export interface ValidateCredentialResponse {
  valid: boolean
  missing: string[]
  error: string
}

// ── Management API  (/api/connectors/*) ─────────────────────

export const fetchConnectors = () =>
  apiGet<ConnectorsListResponse>('/api/connectors')

export const enableConnector = (id: string) =>
  apiPost<ConnectorToggleResponse>(`/api/connectors/${id}/enable`)

export const disableConnector = (id: string) =>
  apiPost<ConnectorToggleResponse>(`/api/connectors/${id}/disable`)

export const setConnectorBackend = (id: string, backend: string) =>
  apiPost<BackendSetResponse>(`/api/connectors/${id}/backend`, { backend })

// ── Credential API  (/connectors/{id}/credentials/*) ────────

export const storeCredential = (
  connectorId: string,
  vaultKey: string,
  value: string,
  type: string = 'api_key',
) =>
  apiPost<StoreCredentialResponse>(`/connectors/${connectorId}/credentials`, {
    vault_key: vaultKey,
    value,
    type,
  })

export const deleteCredential = (connectorId: string, vaultKey: string) =>
  apiDelete<DeleteCredentialResponse>(
    `/connectors/${connectorId}/credentials/${vaultKey}`,
  )

export const validateCredentials = (connectorId: string) =>
  apiPost<ValidateCredentialResponse>(
    `/connectors/${connectorId}/credentials/validate`,
  )

// ── Google OAuth API  (/api/google-oauth/*) ──────────────────

export interface GoogleOAuthStartResponse {
  auth_url: string
  message: string
  request_id: string
}

export interface GoogleOAuthStatusResponse {
  configured: boolean
  valid: boolean
  status: string
  has_client_credentials: boolean
  has_access_token: boolean
  has_refresh_token: boolean
  expires_at?: string
  expires_in_seconds?: number
  scopes: string[]
  refresh_thread_alive?: boolean
  feature_enabled: boolean
  request_id: string
}

export const startGoogleOAuth = (clientId: string, clientSecret: string) =>
  apiPost<GoogleOAuthStartResponse>('/api/google-oauth/start', {
    client_id: clientId,
    client_secret: clientSecret,
  })

export const fetchGoogleOAuthStatus = () =>
  apiGet<GoogleOAuthStatusResponse>('/api/google-oauth/status')

export const revokeGoogleOAuth = () =>
  apiPost<{ status: string; message: string; request_id: string }>('/api/google-oauth/revoke')
