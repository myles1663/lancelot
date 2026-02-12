import { apiGet } from './client'
import type {
  HealthCheckResponse,
  HealthReadyResponse,
  HealthLiveResponse,
  ReadinessResponse,
} from '@/types/api'

/** GET /health — Gateway health with component status */
export function fetchHealth() {
  return apiGet<HealthCheckResponse>('/health')
}

/** GET /ready — Gateway readiness probe */
export function fetchReady() {
  return apiGet<ReadinessResponse>('/ready')
}

/** GET /health/live — Process liveness probe */
export function fetchHealthLive() {
  return apiGet<HealthLiveResponse>('/health/live')
}

/** GET /health/ready — Full health snapshot with degraded reasons */
export function fetchHealthReady() {
  return apiGet<HealthReadyResponse>('/health/ready')
}
