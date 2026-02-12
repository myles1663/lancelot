import { apiGet } from './client'
import type { RouterDecisionsResponse, RouterStatsResponse } from '@/types/api'

/** GET /router/decisions — Recent routing decisions */
export function fetchRouterDecisions() {
  return apiGet<RouterDecisionsResponse>('/router/decisions')
}

/** GET /router/stats — Routing statistics */
export function fetchRouterStats() {
  return apiGet<RouterStatsResponse>('/router/stats')
}
