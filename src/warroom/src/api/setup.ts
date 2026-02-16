import { apiGet, apiPost, apiDelete } from './client'
import type {
  SystemInfoResponse,
  VaultKeysResponse,
  LogsResponse,
  SetupActionResponse,
  ConfigReloadResponse,
  MemoryPurgeResponse,
  TokensListResponse,
  TokenRevokeResponse,
} from '@/types/api'

// ── System Info ──────────────────────────────────────────────────

/** GET /api/setup/system-info */
export function fetchSystemInfo() {
  return apiGet<SystemInfoResponse>('/api/setup/system-info')
}

// ── Container Controls ───────────────────────────────────────────

/** POST /api/setup/restart */
export function restartContainer() {
  return apiPost<SetupActionResponse>('/api/setup/restart', { confirm: true })
}

/** POST /api/setup/shutdown */
export function shutdownContainer() {
  return apiPost<SetupActionResponse>('/api/setup/shutdown', { confirm: true })
}

// ── Log Viewer ───────────────────────────────────────────────────

/** GET /api/setup/logs?lines=N&file=audit|vault */
export function fetchLogs(lines = 200, file = 'audit') {
  return apiGet<LogsResponse>('/api/setup/logs', {
    lines: String(lines),
    file,
  })
}

// ── Vault Management ─────────────────────────────────────────────

/** GET /api/setup/vault/keys */
export function fetchVaultKeys() {
  return apiGet<VaultKeysResponse>('/api/setup/vault/keys')
}

/** DELETE /api/setup/vault/keys/{key} */
export function deleteVaultKey(key: string) {
  return apiDelete<SetupActionResponse>(`/api/setup/vault/keys/${encodeURIComponent(key)}`)
}

// ── Receipt Management ───────────────────────────────────────────

/** POST /api/setup/receipts/clear */
export function clearReceipts() {
  return apiPost<SetupActionResponse>('/api/setup/receipts/clear', { confirm: true })
}

// Receipt stats: use fetchReceiptStats from './receipts' instead

// ── Token Management (existing endpoints) ────────────────────────

/** GET /tokens — list execution tokens */
export function fetchTokens(limit = 50) {
  return apiGet<TokensListResponse>('/tokens', { limit: String(limit) })
}

/** POST /tokens/{id}/revoke — revoke a token */
export function revokeToken(tokenId: string, reason = 'Revoked via Setup panel') {
  return apiPost<TokenRevokeResponse>(`/tokens/${tokenId}/revoke`, { reason })
}

// ── Usage (existing endpoint) ────────────────────────────────────

/** POST /usage/reset — reset usage counters */
export function resetUsage() {
  return apiPost<{ message: string }>('/usage/reset')
}

// ── Configuration ────────────────────────────────────────────────

/** POST /api/setup/config/reload */
export function reloadConfig() {
  return apiPost<ConfigReloadResponse>('/api/setup/config/reload')
}

// ── Export / Backup ──────────────────────────────────────────────

/** GET /api/setup/export — download as ZIP */
export async function exportBackup() {
  const token = localStorage.getItem('lancelot_api_token')
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch('/api/setup/export', { headers })
  if (!res.ok) throw new Error('Export failed')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'lancelot-backup.zip'
  a.click()
  URL.revokeObjectURL(url)
}

// ── Danger Zone ──────────────────────────────────────────────────

/** POST /api/setup/factory-reset */
export function factoryReset(confirmationText: string) {
  return apiPost<SetupActionResponse>('/api/setup/factory-reset', {
    confirm: true,
    confirmation_text: confirmationText,
  })
}

/** POST /api/setup/memory/purge */
export function purgeMemory() {
  return apiPost<MemoryPurgeResponse>('/api/setup/memory/purge', { confirm: true })
}

/** POST /api/setup/flags/reset */
export function resetFlags() {
  return apiPost<SetupActionResponse>('/api/setup/flags/reset', { confirm: true })
}
