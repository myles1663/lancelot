import { apiGet } from './client'
import type {
  UsageSummaryResponse,
  UsageLanesResponse,
  UsageModelsResponse,
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

/** GET /usage/monthly — Monthly usage data from persistence */
export function fetchUsageMonthly(month?: string) {
  return apiGet<UsageMonthlyResponse>('/usage/monthly', month ? { month } : undefined)
}
