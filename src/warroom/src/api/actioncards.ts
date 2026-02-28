import { apiGet, apiPost } from './client'
import type { ActionCardsPendingResponse, ActionCardResolveResponse } from '@/types/api'

/** GET /api/actioncards/pending — Fetch all pending action cards */
export function fetchPendingActionCards() {
  return apiGet<ActionCardsPendingResponse>('/api/actioncards/pending')
}

/** POST /api/actioncards/:cardId/resolve — Resolve an action card */
export function resolveActionCard(cardId: string, buttonId: string) {
  return apiPost<ActionCardResolveResponse>(`/api/actioncards/${cardId}/resolve`, {
    button_id: buttonId,
  })
}
