import { apiGet, apiPost } from './client'
import type { TokensListResponse, TokenGetResponse, TokenRevokeResponse } from '@/types/api'

/** GET /tokens — List execution tokens */
export function fetchTokens(status?: string, limit = 50) {
  const params: Record<string, string> = { limit: String(limit) }
  if (status) params.status = status
  return apiGet<TokensListResponse>('/tokens', params)
}

/** GET /tokens/:id — Get a single execution token */
export function fetchToken(tokenId: string) {
  return apiGet<TokenGetResponse>(`/tokens/${tokenId}`)
}

/** POST /tokens/:id/revoke — Revoke an execution token */
export function revokeToken(tokenId: string, reason?: string) {
  return apiPost<TokenRevokeResponse>(`/tokens/${tokenId}/revoke`, {
    reason: reason || 'Manual revocation via War Room',
  })
}
