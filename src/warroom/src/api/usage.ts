import { apiGet, apiPost } from './client'
import type {
  UsageSummaryResponse,
  UsageLanesResponse,
  UsageModelsResponse,
  UsageSavingsResponse,
  UsageMonthlyResponse,
} from '@/types/api'

/** GET /usage/summary — Full usage and cost summary */
export function fetchUsageSummary() {
  return apiGet<UsageSummaryResponse>('/usage/summary')
}

/** GET /usage/lanes — Per-lane usage breakdown */
export function fetchUsageLanes() {
  return apiGet<UsageLanesResponse>('/usage/lanes')
}

/** GET /usage/models — Per-model usage breakdown */
export function fetchUsageModels() {
  return apiGet<UsageModelsResponse>('/usage/models')
}

/** GET /usage/savings — Estimated savings from local models */
export function fetchUsageSavings() {
  return apiGet<UsageSavingsResponse>('/usage/savings')
}

/** GET /usage/monthly — Monthly usage data from persistence */
export function fetchUsageMonthly(month?: string) {
  return apiGet<UsageMonthlyResponse>('/usage/monthly', month ? { month } : undefined)
}

/** POST /usage/reset — Reset in-memory usage counters */
export function resetUsage() {
  return apiPost<{ message: string; usage: Record<string, unknown> }>('/usage/reset')
}
