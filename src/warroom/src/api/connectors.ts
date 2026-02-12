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
