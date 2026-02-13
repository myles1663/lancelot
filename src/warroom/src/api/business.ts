import { apiGet, apiPost } from './client'

// ── Types ────────────────────────────────────────────

export interface ClientBilling {
  stripe_customer_id: string
  subscription_id: string
  current_period_end: string | null
  payment_status: string
}

export interface ClientPreferences {
  tone: string
  platforms: string[]
  hashtag_policy: string
  emoji_policy: string
  brand_voice_notes: string
  excluded_topics: string[]
  posting_schedule: Record<string, unknown>
}

export interface ContentHistory {
  total_pieces_delivered: number
  last_delivery_at: string | null
  average_satisfaction: number
}

export interface BalClient {
  id: string
  name: string
  email: string
  status: string
  plan_tier: string
  billing: ClientBilling
  preferences: ClientPreferences
  content_history: ContentHistory
  memory_block_id: string | null
  created_at: string
  updated_at: string
}

export interface ClientListResponse {
  clients: BalClient[]
  total: number
}

// ── Fetchers ─────────────────────────────────────────

export const fetchBalClients = () =>
  apiGet<ClientListResponse>('/api/v1/clients')

export const fetchBalClientsByStatus = (status: string) =>
  apiGet<ClientListResponse>('/api/v1/clients', { status })

export const pauseClient = (clientId: string, reason = '') =>
  apiPost<BalClient>(`/api/v1/clients/${clientId}/pause`, { reason })

export const resumeClient = (clientId: string) =>
  apiPost<BalClient>(`/api/v1/clients/${clientId}/resume`)

export const activateClient = (clientId: string) =>
  apiPost<BalClient>(`/api/v1/clients/${clientId}/activate`)
