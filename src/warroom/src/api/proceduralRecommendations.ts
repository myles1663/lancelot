import { apiGet, apiPost } from './client'

export interface ProceduralRecommendation {
  recommendation_id: string
  category: string
  title: string
  observation: string
  risk_or_opportunity: string
  recommendation: string
  suggested_action: string
  score: number
  score_breakdown: Record<string, number>
  evidence: string[]
  delivery_mode: string
  status: string
  user_response: string
  created_at: number
  updated_at: number
  snoozed_until?: number | null
  quest_id?: string
  actioncard_id?: string
  sop_draft_path?: string
}

export interface ProceduralRecommendationsResponse {
  recommendations: ProceduralRecommendation[]
  count: number
}

export interface ProceduralRecommendationStats {
  total: number
  by_status: Record<string, number>
  by_category: Record<string, number>
}

export function fetchProceduralRecommendations(params?: {
  status?: string
  category?: string
  limit?: number
}) {
  const query: Record<string, string> = {}
  if (params?.status) query.status = params.status
  if (params?.category) query.category = params.category
  if (params?.limit) query.limit = String(params.limit)
  return apiGet<ProceduralRecommendationsResponse>('/api/procedural-recommendations/', query)
}

export function fetchProceduralRecommendationStats() {
  return apiGet<{ stats: ProceduralRecommendationStats }>('/api/procedural-recommendations/stats')
}

export function acceptProceduralRecommendation(id: string) {
  return apiPost(`/api/procedural-recommendations/${encodeURIComponent(id)}/accept`, {})
}

export function dismissProceduralRecommendation(id: string) {
  return apiPost(`/api/procedural-recommendations/${encodeURIComponent(id)}/dismiss`, {})
}

export function snoozeProceduralRecommendation(id: string, snoozeHours = 24) {
  return apiPost(`/api/procedural-recommendations/${encodeURIComponent(id)}/snooze`, {
    snooze_hours: snoozeHours,
  })
}

export function convertProceduralRecommendationToSop(id: string) {
  return apiPost(`/api/procedural-recommendations/${encodeURIComponent(id)}/convert-to-sop`, {})
}
