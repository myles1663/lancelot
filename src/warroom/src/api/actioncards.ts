import { apiGet, apiPost } from './client'
import type {
  ActionCardButton,
  ActionCardData,
  ActionCardResolveResponse,
  ActionCardsPendingResponse,
  ActionCardType,
} from '@/types/api'

interface RawActionCardData {
  card_id?: string
  cardId?: string
  card_type?: ActionCardType
  cardType?: ActionCardType
  quest_id?: string | null
  questId?: string | null
  source_system?: string
  sourceSystem?: string
  source_item_id?: string
  sourceItemId?: string
  title?: string
  description?: string
  buttons?: ActionCardButton[]
  resolved?: boolean
  resolved_action?: string
  resolvedAction?: string
  resolved_channel?: string
  resolvedChannel?: string
  created_at?: number
  presentedAt?: number
  resolved_at?: number
  resolvedAt?: number
}

function normalizeActionCard(card: RawActionCardData): ActionCardData {
  return {
    cardId: card.cardId ?? card.card_id ?? '',
    cardType: card.cardType ?? card.card_type ?? 'info',
    questId: card.questId ?? card.quest_id ?? null,
    sourceSystem: card.sourceSystem ?? card.source_system,
    sourceItemId: card.sourceItemId ?? card.source_item_id,
    title: card.title ?? '',
    description: card.description ?? '',
    buttons: card.buttons ?? [],
    resolved: card.resolved ?? false,
    resolvedAction: card.resolvedAction ?? card.resolved_action,
    resolvedChannel: card.resolvedChannel ?? card.resolved_channel,
    presentedAt: card.presentedAt ?? card.created_at ?? Date.now() / 1000,
    resolvedAt: card.resolvedAt ?? card.resolved_at,
  }
}

/** GET /api/actioncards/pending — Fetch all pending action cards */
export async function fetchPendingActionCards(): Promise<ActionCardsPendingResponse> {
  const response = await apiGet<{ cards: RawActionCardData[]; count: number }>('/api/actioncards/?status=pending')
  const cards = response.cards.map(normalizeActionCard)
  return { cards, count: response.count ?? cards.length }
}

/** POST /api/actioncards/:cardId/resolve — Resolve an action card */
export function resolveActionCard(cardId: string, buttonId: string) {
  return apiPost<ActionCardResolveResponse>(`/api/actioncards/${cardId}/resolve/${buttonId}`, {})
}
