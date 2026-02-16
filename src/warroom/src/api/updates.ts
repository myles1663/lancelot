import { apiGet, apiPost } from './client'
import type { UpdateStatusResponse } from '@/types/api'

/** GET /api/updates/status — cached update status (cheap poll) */
export function fetchUpdateStatus() {
  return apiGet<UpdateStatusResponse>('/api/updates/status')
}

/** POST /api/updates/check — force an immediate version check */
export function checkForUpdate() {
  return apiPost<UpdateStatusResponse & { status: string }>('/api/updates/check')
}

/** POST /api/updates/dismiss — dismiss the update banner */
export function dismissUpdate() {
  return apiPost<{ status: string }>('/api/updates/dismiss')
}
